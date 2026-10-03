"""Reading and writing ``root.json`` - the base identity every lane starts from.

``root.json`` is Playwright's ``storageState`` format: a list of cookies and a list of
per-origin storage. Two facts shape everything here.

**IndexedDB is opt-in and most logins live there.** ``storageState()`` captures cookies
and localStorage by default. Firebase, Supabase and Auth0 keep their sessions in
IndexedDB, so the default quietly produces a file that looks fine and is logged out of
half your sites. On one measured profile, cookies were 1.5 MB of a 31 MB identity set.
AutoWeb passes ``indexed_db=True`` unless you turn it off per origin.

**State flows out of a browser profile but never back in.**
``launch_persistent_context()`` has no ``storage_state`` parameter: pass one and it
launches successfully, then discards it. So the base is a JSON file, and lanes are
seeded with ``new_context(storage_state=...)``. That asymmetry is why this module
exists at all rather than copying a profile directory.

See ``docs/STORAGE-EXPORT.md`` for the measurements behind both.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError


class StateError(Exception):
    """Raised when a state file is missing, unreadable or not storageState-shaped."""


@dataclass(frozen=True)
class OriginSummary:
    """What one origin contributes to a state file."""

    origin: str
    cookies: int
    local_storage_keys: int
    indexeddb_stores: int
    bytes: int

    @property
    def carries_session(self) -> bool:
        """Whether this origin holds anything that could keep you logged in.

        An origin with no cookies and no storage is noise: something touched it during
        a run and left nothing behind.
        """
        return bool(self.cookies or self.local_storage_keys or self.indexeddb_stores)


@dataclass(frozen=True)
class StateSummary:
    """A whole state file, measured.

    You need this before you can set ``caps`` honestly. Guessing at
    ``caps.total_bytes`` without knowing what a real export weighs produces either a
    cap that never fires or one that silently evicts your logins.
    """

    path: Path
    total_bytes: int
    cookies: int
    origins: tuple[OriginSummary, ...]

    @property
    def origins_with_session(self) -> tuple[OriginSummary, ...]:
        return tuple(o for o in self.origins if o.carries_session)


def load(path: Path) -> dict[str, Any]:
    """Read a storageState file, failing with a message you can act on."""
    if not path.is_file():
        raise StateError(
            f"{path}: no state file here. Create one with 'autoweb state export'."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise StateError(f"{path}: not valid JSON: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise StateError(f"{path}: cannot be read: {exc}") from exc

    if not isinstance(raw, dict):
        raise StateError(
            f"{path}: expected a storageState object, got {type(raw).__name__}"
        )
    for key in ("cookies", "origins"):
        value = raw.get(key, [])
        if not isinstance(value, list):
            raise StateError(
                f"{path}: '{key}' must be a list, got {type(value).__name__}. "
                f"This does not look like a Playwright storageState file."
            )
    return raw


def save(path: Path, state: dict[str, Any]) -> None:
    """Write a state file atomically, keeping the previous version as ``.bak``.

    Identity is expensive to rebuild: it costs a human sitting at a browser typing
    passwords. A half-written ``root.json`` would mean doing that again, so the write
    goes to a temp file and renames, and the file it replaces is kept.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        if path.is_file():
            backup = path.with_suffix(path.suffix + ".bak")
            backup.unlink(missing_ok=True)
            path.replace(backup)
        tmp.replace(path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise StateError(f"{path}: cannot write state: {exc}") from exc


def summarise(path: Path, state: dict[str, Any] | None = None) -> StateSummary:
    """Measure a state file: per-origin cookies, localStorage keys, IndexedDB stores."""
    state = state if state is not None else load(path)

    cookies_by_host: dict[str, int] = {}
    for cookie in state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        host = str(cookie.get("domain", "")).lstrip(".").lower()
        cookies_by_host[host] = cookies_by_host.get(host, 0) + 1

    summaries: list[OriginSummary] = []
    seen_hosts: set[str] = set()
    for entry in state.get("origins", []):
        if not isinstance(entry, dict):
            continue
        origin = str(entry.get("origin", ""))
        host = origin.split("://")[-1].split("/")[0].split(":")[0].lower()
        seen_hosts.add(host)
        local = entry.get("localStorage") or []
        idb = entry.get("indexedDB") or []
        summaries.append(OriginSummary(
            origin=origin,
            cookies=cookies_by_host.get(host, 0),
            local_storage_keys=len(local) if isinstance(local, list) else 0,
            indexeddb_stores=len(idb) if isinstance(idb, list) else 0,
            bytes=len(json.dumps(entry)),
        ))

    # Cookie-only hosts never appear in `origins`, and they are often the whole
    # session. Listing them keeps the origin count honest.
    for host, count in sorted(cookies_by_host.items()):
        if host and host not in seen_hosts:
            summaries.append(OriginSummary(
                origin=host, cookies=count, local_storage_keys=0,
                indexeddb_stores=0, bytes=0,
            ))

    summaries.sort(key=lambda s: (-s.bytes, -s.cookies, s.origin))
    return StateSummary(
        path=path,
        total_bytes=len(json.dumps(state)),
        cookies=len([c for c in state.get("cookies", []) if isinstance(c, dict)]),
        origins=tuple(summaries),
    )


def export_interactive(
    out: Path,
    url: str,
    *,
    browser: str = "chrome",
    indexeddb: bool = True,
    timeout_seconds: int = 600,
    wait_for_enter=input,
) -> StateSummary:
    """Open a headed browser, wait for a human to log in, then capture the state.

    The human does the logging in. AutoWeb never sees, stores or types a password; it
    opens a window, waits, and reads the resulting session out of the browser. That is
    not only safer, it is the only approach that survives MFA and bot detection.

    The browser is a **fresh** context rather than your real Chrome profile. Opening
    your day-to-day profile would lock it, and on Windows a second Chrome on the same
    profile fails silently (exit 0 or 21, no error) - see ``docs/CONSTRAINTS.md``.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency is declared
        raise StateError(
            "playwright is not installed. Run: uv sync"
        ) from exc

    launch_args: dict[str, Any] = {"headless": False}
    # 'chromium' means Playwright's bundled build, which has no channel.
    if browser != "chromium":
        launch_args["channel"] = browser

    with sync_playwright() as pw:
        try:
            instance = pw.chromium.launch(**launch_args)
        except Exception as exc:
            raise StateError(
                f"could not launch '{browser}': {exc}\n"
                f"If Chrome is not installed, set lanes.browser = \"chromium\" in "
                f"autoweb.toml and run: npx playwright install chromium"
            ) from exc

        context = instance.new_context()
        context.set_default_timeout(timeout_seconds * 1000)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        print()
        print(f"  A browser window is open at {url}")
        print("  Log in there by hand. Take as long as you need.")
        print("  AutoWeb does not see your password; it only reads the session after.")
        print()
        wait_for_enter("  Press Enter here once you are logged in... ")

        state = context.storage_state(indexed_db=indexeddb)
        context.close()
        instance.close()

    save(out, state)
    return summarise(out, state)


def seeded_context_check(state_path: Path, url: str, *, browser: str = "chrome",
                         indexeddb: bool = True) -> str:
    """Open a fresh isolated browser seeded from *state_path* and return the page title.

    This is the honest test of an export: a brand new browser, nothing on disk, given
    only the JSON. If it is still logged in, the export captured what mattered.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise StateError("playwright is not installed. Run: uv sync") from exc

    if not state_path.is_file():
        raise StateError(f"{state_path}: no state file to seed from")

    launch_args: dict[str, Any] = {"headless": True}
    if browser != "chromium":
        launch_args["channel"] = browser

    with sync_playwright() as pw:
        instance = pw.chromium.launch(**launch_args)
        context = instance.new_context(storage_state=str(state_path))
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        title = page.title()
        context.close()
        instance.close()
    return title


__all__ = [
    "ConfigError",
    "OriginSummary",
    "StateError",
    "StateSummary",
    "export_interactive",
    "load",
    "save",
    "seeded_context_check",
    "summarise",
]

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

from .config import ConfigError, _host_of


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
    # Both keys, present, not merely well-typed if they happen to exist. Playwright
    # always writes both, even when empty, so a file missing one is not a storageState -
    # and `raw.get(key, [])` used to let any JSON object through as "0 cookies, 0
    # origins", which reads like a successful export of nothing. The same false-pass
    # shape as a verify that asserted nothing.
    missing = [key for key in ("cookies", "origins") if key not in raw]
    if missing:
        raise StateError(
            f"{path}: not a Playwright storageState file - it has no "
            f"{' and no '.join(repr(k) for k in missing)} key. "
            f"Export one with 'autoweb state export <url>'."
        )
    for key in ("cookies", "origins"):
        if not isinstance(raw[key], list):
            raise StateError(
                f"{path}: '{key}' must be a list, got {type(raw[key]).__name__}. "
                f"This does not look like a Playwright storageState file."
            )
    return raw


def ensure_writable(path: Path) -> None:
    """Check *path* can be written to, before anything expensive happens.

    Called before the browser opens. A human spends minutes logging in, possibly
    through MFA, and discovering only afterwards that the output directory is a file
    means that session is gone and they do it again. Find out first.
    """
    parent = path.parent
    for ancestor in [parent, *parent.parents]:
        if ancestor.exists():
            if not ancestor.is_dir():
                raise StateError(
                    f"{path}: cannot write here - '{ancestor}' is a file, not a "
                    f"directory"
                )
            break
    if path.exists() and not path.is_file():
        raise StateError(f"{path}: exists and is not a file")
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StateError(f"{path}: cannot create {parent}: {exc}") from exc


def save(path: Path, state: dict[str, Any]) -> None:
    """Write a state file atomically, keeping the previous version as ``.bak``.

    Identity is expensive to rebuild: it costs a human sitting at a browser typing
    passwords. A half-written ``root.json`` would mean doing that again, so the write
    goes to a temp file and renames, and the file it replaces is kept.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        if path.is_file():
            backup = path.with_suffix(path.suffix + ".bak")
            backup.unlink(missing_ok=True)
            path.replace(backup)
        tmp.replace(path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass    # cleanup is best-effort; the original error is what matters
        raise StateError(f"{path}: cannot write state: {exc}") from exc


def summarise(path: Path, state: dict[str, Any] | None = None) -> StateSummary:
    """Measure a state file: per-origin cookies, localStorage keys, IndexedDB stores.

    Origins are keyed by host, not by the origin string, so a site visited over both
    http and https, or on two ports, counts once. Counting them separately would
    inflate the ``max_origins`` cap and attribute the same cookies repeatedly.
    """
    state = state if state is not None else load(path)

    cookies_by_host: dict[str, int] = {}
    for cookie in state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        domain = cookie.get("domain")
        if not isinstance(domain, str):
            continue
        # Cookie domains carry a leading dot to mean "and subdomains"; origins never
        # do. Strip it so `.example.com` and `https://example.com` are one host.
        host = _host_of(domain.lstrip("."))
        if host:
            cookies_by_host[host] = cookies_by_host.get(host, 0) + 1

    # Merge by host so duplicates collapse instead of each claiming the host's cookies.
    by_host: dict[str, dict[str, Any]] = {}
    for entry in state.get("origins", []):
        if not isinstance(entry, dict):
            continue
        origin = entry.get("origin")
        if not isinstance(origin, str) or not origin.strip():
            continue
        host = _host_of(origin)
        if not host:
            continue
        local = entry.get("localStorage") or []
        idb = entry.get("indexedDB") or []
        acc = by_host.setdefault(host, {"origin": origin, "ls": 0, "idb": 0, "bytes": 0})
        acc["ls"] += len(local) if isinstance(local, list) else 0
        acc["idb"] += len(idb) if isinstance(idb, list) else 0
        acc["bytes"] += len(json.dumps(entry))

    summaries = [
        OriginSummary(
            origin=acc["origin"],
            cookies=cookies_by_host.get(host, 0),
            local_storage_keys=acc["ls"],
            indexeddb_stores=acc["idb"],
            bytes=acc["bytes"],
        )
        for host, acc in by_host.items()
    ]

    # Cookie-only hosts never appear in `origins`, and they are often the whole
    # session. Listing them keeps the origin count honest.
    summaries.extend(
        OriginSummary(origin=host, cookies=count, local_storage_keys=0,
                      indexeddb_stores=0, bytes=0)
        for host, count in sorted(cookies_by_host.items())
        if host not in by_host
    )

    summaries.sort(key=lambda s: (-s.bytes, -s.cookies, s.origin))

    # Measure the file as written, because caps.total_bytes is documented as the
    # maximum size of root.json and that is what a reader will check with `ls`.
    try:
        total = path.stat().st_size
    except OSError:
        total = len(json.dumps(state, indent=2)) + 1

    return StateSummary(
        path=path,
        total_bytes=total,
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
    # Before the browser opens, not after: a login that cannot be saved is a login
    # the human has to do again.
    ensure_writable(out)

    pw_api = _playwright()
    with pw_api() as pw:
        instance = _launch(pw, browser, headless=False)
        context = instance.new_context()
        context.set_default_timeout(timeout_seconds * 1000)
        page = context.new_page()
        _goto(page, url, timeout_seconds)

        print()
        print(f"  A browser window is open at {url}")
        print("  Log in there by hand. Take as long as you need.")
        print("  AutoWeb does not see your password; it only reads the session after.")
        print()
        try:
            wait_for_enter("  Press Enter here once you are logged in... ")
        except EOFError as exc:
            # Non-interactive stdin: a pipe, a CI job, `< /dev/null`. There is no
            # human to log in, so there is nothing to capture.
            context.close()
            instance.close()
            raise StateError(
                "'state export' needs an interactive terminal: a human has to log in "
                "before there is a session to capture. Run it by hand, not from a "
                "pipe or a CI job."
            ) from exc

        state = context.storage_state(indexed_db=indexeddb)
        context.close()
        instance.close()

    save(out, state)
    return summarise(out, state)


@dataclass(frozen=True)
class SeedResult:
    """What a fresh browser saw when seeded from a state file."""

    title: str
    final_url: str
    status: int | None
    body_text: str = ""
    """Visible text of the page.

    A title is a weak signal: plenty of sites serve one title across their login
    page and their secure area. The words on the page are what a human would read
    to decide whether they are logged in.
    """


def seeded_context_check(state_path: Path, url: str, *, browser: str = "chrome",
                         timeout_seconds: int = 60) -> SeedResult:
    """Open a fresh isolated browser seeded from *state_path* and report what it saw.

    This is the honest test of an export: a brand new browser, nothing on disk, given
    only the JSON. If it is still logged in, the export captured what mattered.

    Returns the final URL as well as the title, because a title proves very little.
    Plenty of sites serve the same ``<title>`` on their login page and their secure
    area, and only the URL reveals that you were bounced.
    """
    load(state_path)        # reject anything that is not storageState-shaped, with a
                            # message, rather than letting Playwright raise mid-launch

    pw_api = _playwright()
    with pw_api() as pw:
        instance = _launch(pw, browser, headless=True)
        context = instance.new_context(storage_state=str(state_path))
        page = context.new_page()
        response = _goto(page, url, timeout_seconds)
        try:
            body_text = page.inner_text("body")
        except Exception:  # noqa: BLE001 - a page with no body is not an error here
            body_text = ""
        result = SeedResult(
            title=page.title(),
            final_url=page.url,
            status=response.status if response is not None else None,
            body_text=body_text,
        )
        context.close()
        instance.close()
    return result


# --- browser plumbing --------------------------------------------------------
#
# Playwright raises its own exception types from deep inside the driver. Letting
# those reach the CLI means a traceback for an unreachable host or an expired
# certificate, neither of which is a bug in AutoWeb. Everything below converts them
# into StateError so the user gets one line and exit 2.


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency is declared
        raise StateError("playwright is not installed. Run: uv sync") from exc
    return sync_playwright


def _launch(pw, browser: str, *, headless: bool):
    """Launch *browser*, routing non-Chromium engines to their own browser type."""
    try:
        if browser == "firefox":
            return pw.firefox.launch(headless=headless)
        if browser == "webkit":
            return pw.webkit.launch(headless=headless)
        # 'chromium' is Playwright's bundled build and takes no channel; 'chrome'
        # and 'msedge' are Chromium channels pointing at an installed browser.
        args: dict[str, Any] = {"headless": headless}
        if browser != "chromium":
            args["channel"] = browser
        return pw.chromium.launch(**args)
    except Exception as exc:
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else str(exc)
        hint = (
            "Install it with: uv run playwright install chromium"
            if browser == "chromium"
            else f"Install it with: uv run playwright install {browser}"
        )
        raise StateError(f"could not launch '{browser}': {first_line}\n  {hint}") from exc


def export_from_profile(
    out: Path,
    profile_dir: Path,
    *,
    visit: list[str] | None = None,
    browser: str = "chromium",
    indexeddb: bool = True,
    timeout_seconds: int = 120,
) -> StateSummary:
    """Capture a storageState out of an **existing** browser profile. No human needed.

    This is the other direction of the asymmetry that shapes the whole project: state
    flows *out* of a profile freely, and never back in. So a profile somebody already
    logged into by hand, months ago, is a perfectly good source of identity, and
    `export_interactive` is only for when no such profile exists.

    **Pass a copy of the profile, not the live one.** Launching a browser on a profile
    directory locks it, and a second browser on a locked profile fails silently on
    Windows. Copying also means a bug here cannot damage the original.

    **`visit` is not optional in practice, and this is the subtle part.** A storageState
    collects localStorage and IndexedDB by running script in a page per origin, and the
    only origins it considers are the ones the context has actually visited. A profile
    freshly launched has visited nothing, so an export without `visit` tends to return
    cookies and no origin storage at all - which looks like a successful export of a
    half-identity. Name the origins you care about and they get walked first.

    Returns the summary so the caller can see what was actually captured rather than
    assuming.
    """
    ensure_writable(out)
    if not profile_dir.is_dir():
        raise StateError(
            f"{profile_dir}: no profile directory here. Point --from-profile at a "
            f"browser profile directory, or copy one there first."
        )

    pw_api = _playwright()
    with pw_api() as pw:
        context = _launch_persistent(pw, browser, profile_dir, headless=True)
        context.set_default_timeout(timeout_seconds * 1000)
        try:
            for url in visit or []:
                page = context.new_page()
                try:
                    _goto(page, url, timeout_seconds)
                    # Settle: tokens in IndexedDB are often written by script that runs
                    # after domcontentloaded, so harvesting immediately can miss them.
                    page.wait_for_timeout(2000)
                finally:
                    page.close()
            state = context.storage_state(indexed_db=indexeddb)
        finally:
            context.close()

    save(out, state)
    return summarise(out, state)


def _launch_persistent(pw, browser: str, profile_dir: Path, *, headless: bool):
    """Open a persistent context on *profile_dir*.

    Deliberately no `storage_state` argument. `launch_persistent_context` accepts one,
    launches, and silently ignores it, which is the single fact the architecture is
    built around.
    """
    try:
        args: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
        }
        if browser == "firefox":
            return pw.firefox.launch_persistent_context(**args)
        if browser == "webkit":
            return pw.webkit.launch_persistent_context(**args)
        if browser != "chromium":
            args["channel"] = browser
        return pw.chromium.launch_persistent_context(**args)
    except Exception as exc:
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else str(exc)
        raise StateError(
            f"could not open the profile at {profile_dir} with '{browser}': "
            f"{first_line}" \
            f"\n  If another browser is using that directory, close it or "
            f"copy the profile and point at the copy."
        ) from exc


def _goto(page, url: str, timeout_seconds: int):
    """Navigate, converting Playwright's errors into something actionable."""
    try:
        return page.goto(url, wait_until="domcontentloaded",
                         timeout=timeout_seconds * 1000)
    except Exception as exc:
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else str(exc)
        raise StateError(f"could not load {url}: {first_line}") from exc


__all__ = [
    "ConfigError",
    "OriginSummary",
    "SeedResult",
    "StateError",
    "StateSummary",
    "export_from_profile",
    "export_interactive",
    "load",
    "save",
    "seeded_context_check",
    "summarise",
]

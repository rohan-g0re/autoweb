"""Fold what the lanes learned back into the shared identity.

A three-way merge, with `root.json` as the common ancestor. The ancestor is what makes
this tractable: every lane started from the same file and that file does not change
during a run, so for any value we know what it was before, and "a lane changed it" is a
fact rather than a guess.

Five rules, and each one exists because the obvious alternative is wrong.

**Never propagate a deletion.** A lane that does not have a cookie did not necessarily
delete it; far more often it simply never visited that site. Treating absence as removal
would make every run quietly erode the identity.

**Unknown is not empty.** `storageState()` returns `indexedDB: []` both when a lane
harvested an origin and found nothing, and when nothing harvested IndexedDB at all. The
two are indistinguishable in the file, so an empty list never overwrites a non-empty
ancestor. The cost is that a genuine clearing of IndexedDB does not propagate; the
alternative is destroying real login state on every merge, which is worse.

**A conflict evicts the origin instead of picking a winner.** When two lanes change the
same value to different things, one of them holds a session token the server has already
rotated away. Writing either one back can present a retired refresh token, and a server
that implements replay detection is entitled to revoke the whole token family, signing
the user out everywhere. Dropping the origin costs one manual login. Guessing can cost
every login.

**One writer, after every lane is dead.** Lanes are read-only inputs by the time they get
here, and the write is atomic with a backup kept.

**A cap is an error, not a truncation.** Silently dropping origins to fit a limit would
make the identity depend on dictionary ordering.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .state import StateError, load, save


class MergeError(Exception):
    """A merge that must not be written: a cap exceeded, or an input we cannot trust."""


# A cookie's identity, per RFC 6265: the triple, not the name.
CookieKey = tuple[str, str, str]


def cookie_key(cookie: dict[str, Any]) -> CookieKey:
    return (str(cookie.get("name", "")),
            str(cookie.get("domain", "")),
            str(cookie.get("path", "/")))


@dataclass
class OriginDecision:
    """What happened to one origin, and why. This is the audit trail."""

    origin: str
    action: str              # "kept", "updated", "added", "evicted"
    reason: str = ""
    changed_by: list[str] = field(default_factory=list)


@dataclass
class MergeResult:
    state: dict[str, Any]
    decisions: list[OriginDecision]
    cookies_kept: int
    cookies_updated: int
    cookies_added: int
    cookies_evicted: int
    evicted_origins: list[str]
    rotating_violations: list[str]

    @property
    def total_bytes(self) -> int:
        return len(json.dumps(self.state))


def _origins_by_name(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for origin in state.get("origins", []):
        if isinstance(origin, dict) and origin.get("origin"):
            out[str(origin["origin"])] = origin
    return out


def _local_storage(origin: dict[str, Any]) -> dict[str, str]:
    items = origin.get("localStorage") or []
    return {str(i.get("name")): str(i.get("value", ""))
            for i in items if isinstance(i, dict) and i.get("name") is not None}


def _as_local_storage(mapping: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in sorted(mapping.items())]


def _host_of(origin: str) -> str:
    """Bare host from an origin, for matching cookies against an evicted origin."""
    text = origin.split("://", 1)[-1]
    return text.split("/", 1)[0].split(":", 1)[0].lstrip(".").lower()


def _cookie_matches_host(cookie: dict[str, Any], host: str) -> bool:
    domain = str(cookie.get("domain", "")).lstrip(".").lower()
    return domain == host or host.endswith("." + domain) or domain.endswith("." + host)


def merge(root: dict[str, Any], lanes: dict[str, dict[str, Any]],
          cfg: Config | None = None) -> MergeResult:
    """Three-way merge *lanes* onto *root*, with root as the common ancestor.

    `lanes` maps a lane's name to its captured state, so every decision can name the
    lane responsible. A name is not cosmetic here: when two lanes conflict, which two is
    the thing the operator needs to know.
    """
    root_origins = _origins_by_name(root)
    root_cookies = {cookie_key(c): c for c in root.get("cookies", [])
                    if isinstance(c, dict)}

    # Which hosts are special is a config question, and `rule_for` already implements
    # most-specific-wins matching, so ask it per origin rather than reimplementing it.
    every_host = {_host_of(name) for name in root_origins}
    for state in lanes.values():
        every_host |= {_host_of(name) for name in _origins_by_name(state)}
    for cookie in root.get("cookies", []):
        if isinstance(cookie, dict):
            every_host.add(_host_of(str(cookie.get("domain", ""))))

    sticky: set[str] = set()
    rotating: set[str] = set()
    if cfg is not None:
        for host in every_host:
            if not host:
                continue
            rule = cfg.rule_for(host)
            if getattr(rule, "sticky", False):
                sticky.add(host)
            if getattr(rule, "rotates", False):
                rotating.add(host)

    # --- which lanes touched which origin, for the rotating-site check -------
    touched: dict[str, list[str]] = {}
    for lane, state in lanes.items():
        for name in _origins_by_name(state):
            touched.setdefault(_host_of(name), []).append(lane)
    rotating_violations = sorted(
        f"{host} was touched by {len(who)} lanes ({', '.join(sorted(who))})"
        for host, who in touched.items() if host in rotating and len(who) > 1
    )

    # --- cookies ------------------------------------------------------------
    merged_cookies: dict[CookieKey, dict[str, Any]] = dict(root_cookies)
    kept = updated = added = 0
    evicted_cookie_keys: set[CookieKey] = set()
    conflicted_hosts: set[str] = set()

    all_keys: set[CookieKey] = set(root_cookies)
    lane_cookies: dict[str, dict[CookieKey, dict[str, Any]]] = {}
    for lane, state in lanes.items():
        lane_cookies[lane] = {cookie_key(c): c for c in state.get("cookies", [])
                              if isinstance(c, dict)}
        all_keys |= set(lane_cookies[lane])

    for key in sorted(all_keys):
        ancestor = root_cookies.get(key)
        # Only lanes that actually have this cookie get a vote. A lane without it is
        # silent, never a deletion.
        voters = {lane: cookies[key] for lane, cookies in lane_cookies.items()
                  if key in cookies}
        if not voters:
            kept += 1
            continue

        base_value = None if ancestor is None else str(ancestor.get("value", ""))
        changed = {lane: c for lane, c in voters.items()
                   if str(c.get("value", "")) != base_value}
        if not changed:
            kept += 1
            continue

        distinct = {str(c.get("value", "")) for c in changed.values()}
        if len(distinct) > 1:
            host = _host_of(key[1])
            if host in sticky:
                # Sticky wins over eviction: the operator has said this origin is more
                # expensive to re-establish than it is risky to leave alone.
                merged_cookies[key] = ancestor if ancestor is not None else \
                    next(iter(changed.values()))
                kept += 1
                continue
            evicted_cookie_keys.add(key)
            conflicted_hosts.add(host)
            continue

        merged_cookies[key] = next(iter(changed.values()))
        if ancestor is None:
            added += 1
        else:
            updated += 1

    # An eviction is per origin, not per cookie: half a session is not a session.
    for host in conflicted_hosts:
        for key in list(merged_cookies):
            if _cookie_matches_host(merged_cookies[key], host):
                evicted_cookie_keys.add(key)
                merged_cookies.pop(key, None)
    for key in evicted_cookie_keys:
        merged_cookies.pop(key, None)

    # --- origins ------------------------------------------------------------
    decisions: list[OriginDecision] = []
    merged_origins: dict[str, dict[str, Any]] = {
        name: json.loads(json.dumps(origin)) for name, origin in root_origins.items()
    }

    every_origin = set(root_origins) | {
        name for state in lanes.values() for name in _origins_by_name(state)
    }
    for name in sorted(every_origin):
        host = _host_of(name)
        ancestor = root_origins.get(name)
        contributors = {lane: _origins_by_name(state)[name]
                        for lane, state in lanes.items()
                        if name in _origins_by_name(state)}

        if host in conflicted_hosts and host not in sticky:
            merged_origins.pop(name, None)
            decisions.append(OriginDecision(
                name, "evicted",
                "two lanes changed the same value to different things, so one holds a "
                "token the server has already rotated away. Log in again.",
                sorted(contributors)))
            continue

        if not contributors:
            decisions.append(OriginDecision(name, "kept", "no lane visited it"))
            continue

        base_ls = _local_storage(ancestor) if ancestor else {}
        merged_ls = dict(base_ls)
        ls_conflict = False
        changed_by: list[str] = []

        keys = set(base_ls)
        for origin in contributors.values():
            keys |= set(_local_storage(origin))
        for k in sorted(keys):
            before = base_ls.get(k)
            votes = {lane: _local_storage(o)[k] for lane, o in contributors.items()
                     if k in _local_storage(o)}
            if not votes:
                continue                       # absent in every lane: never a deletion
            differing = {lane: v for lane, v in votes.items() if v != before}
            if not differing:
                continue
            if len({*differing.values()}) > 1 and host not in sticky:
                ls_conflict = True
                break
            merged_ls[k] = next(iter(differing.values()))
            changed_by += list(differing)

        if ls_conflict:
            merged_origins.pop(name, None)
            conflicted_hosts.add(host)
            decisions.append(OriginDecision(
                name, "evicted",
                "two lanes wrote different values to the same localStorage key",
                sorted(contributors)))
            continue

        # IndexedDB: an empty list is unknown, not empty. See the module docstring.
        merged_idb = (ancestor or {}).get("indexedDB") or []
        for lane, origin in contributors.items():
            candidate = origin.get("indexedDB") or []
            if candidate and candidate != merged_idb:
                merged_idb = candidate
                changed_by.append(lane)

        entry: dict[str, Any] = {"origin": name,
                                 "localStorage": _as_local_storage(merged_ls)}
        if merged_idb:
            entry["indexedDB"] = merged_idb
        merged_origins[name] = entry

        if ancestor is None:
            decisions.append(OriginDecision(name, "added", "first seen in a lane",
                                            sorted(set(changed_by))))
        elif changed_by:
            decisions.append(OriginDecision(name, "updated", "a lane changed it",
                                            sorted(set(changed_by))))
        else:
            decisions.append(OriginDecision(name, "kept", "visited, nothing changed"))

    merged = {
        "cookies": [merged_cookies[k] for k in sorted(merged_cookies)],
        "origins": [merged_origins[n] for n in sorted(merged_origins)],
    }
    result = MergeResult(
        state=merged,
        decisions=decisions,
        cookies_kept=kept,
        cookies_updated=updated,
        cookies_added=added,
        cookies_evicted=len(evicted_cookie_keys),
        evicted_origins=sorted(d.origin for d in decisions if d.action == "evicted"),
        rotating_violations=rotating_violations,
    )
    if cfg is not None:
        _enforce_caps(result, cfg)
    return result


def _enforce_caps(result: MergeResult, cfg: Config) -> None:
    """Refuse to write rather than quietly dropping things to fit.

    A cap that truncates makes the surviving identity depend on dictionary ordering,
    which is the kind of bug that shows up as one site being logged out on Tuesdays.
    """
    caps = getattr(cfg, "caps", None)
    if caps is None:
        return
    total = result.total_bytes
    max_bytes = getattr(caps, "total_bytes", 0)
    if max_bytes and total > max_bytes:
        raise MergeError(
            f"the merged state is {total} bytes, over the caps.total_bytes limit of "
            f"{max_bytes}. Nothing was written. Either raise the cap or drop origins "
            f"you do not need with a smaller set of lanes."
        )
    max_origins = getattr(caps, "max_origins", 0)
    if max_origins and len(result.state["origins"]) > max_origins:
        raise MergeError(
            f"the merged state has {len(result.state['origins'])} origins, over the "
            f"caps.max_origins limit of {max_origins}. Nothing was written."
        )
    max_stores = getattr(caps, "max_indexeddb_per_origin", 0)
    if max_stores:
        for origin in result.state["origins"]:
            stores = sum(len(db.get("stores", []) or [])
                         for db in origin.get("indexedDB", []) or [])
            if stores > max_stores:
                raise MergeError(
                    f"{origin['origin']} has {stores} IndexedDB stores, over the "
                    f"caps.max_indexeddb_per_origin limit of {max_stores}. Nothing "
                    f"was written."
                )


def merge_files(root_path: Path, lane_paths: list[Path],
                cfg: Config | None = None) -> MergeResult:
    """Load, merge, and leave writing to the caller.

    Reading every lane before touching the root means a malformed lane file fails the
    whole merge instead of half-applying it.
    """
    if not lane_paths:
        raise MergeError("no lane files to merge")
    root = load(root_path)
    lanes: dict[str, dict[str, Any]] = {}
    for path in lane_paths:
        try:
            lanes[path.stem] = load(path)
        except StateError as exc:
            raise MergeError(f"{path}: cannot be merged: {exc}") from exc
    return merge(root, lanes, cfg)


def write_root(root_path: Path, result: MergeResult) -> Path:
    """Replace the root atomically, keeping the previous one as `.bak`.

    `save` already writes through a temporary file and renames, so a crash mid-write
    cannot leave a half-identity. The `.bak` is for the other failure: a merge that was
    technically valid and still wrong.
    """
    if root_path.is_file():
        backup = root_path.with_suffix(root_path.suffix + ".bak")
        backup.write_bytes(root_path.read_bytes())
    save(root_path, result.state)
    return root_path


__all__ = ["MergeError", "MergeResult", "OriginDecision", "cookie_key", "merge",
           "merge_files", "write_root"]

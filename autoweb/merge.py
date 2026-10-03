"""Fold what the lanes learned back into the shared identity.

A three-way merge, with `root.json` as the common ancestor. The ancestor is what makes
this tractable. Every lane starts from the same file and that file does not change during
a run, so "a lane changed this" is a fact rather than a guess. It is also what stops the
obvious false positive: an isolated lane seeded from `root.json` exports *all* of root's
cookies, including for sites it never opened, so comparing lanes against each other would
report a conflict every time any one of them rotated anything. Every comparison in here is
against the ancestor, for every store, without exception.

**Order of operations matters more than any single rule.** Conflicts are detected across
cookies, localStorage and IndexedDB *first*, for every host, and only then is anything
evicted or merged. An earlier version detected cookie conflicts, evicted, and then found
localStorage conflicts afterwards with nothing left to act on them, which kept the cookie
jar of a site it had already decided was unsafe to keep. That is a half-identity: a
browser that looks signed in and is not, which is harder to diagnose than being signed
out.

The rules, each of which exists because the obvious alternative is worse:

**Never propagate a deletion.** A lane that lacks a cookie did not necessarily delete it;
far more often it never visited that site. Treating absence as removal would erode the
identity a little on every run.

**Unknown is not empty.** `storageState()` returns `indexedDB: []` both when a lane
harvested an origin and found nothing and when nothing harvested IndexedDB at all. The
file cannot tell you which, so an empty list never overwrites a non-empty ancestor. The
cost is that genuinely clearing IndexedDB does not propagate, which is the cheaper mistake
when the alternative is destroying a Firebase or Auth0 login.

**A conflict evicts the whole host instead of picking a winner.** When two lanes change
one value to different things, one of them holds a token the server has already rotated
away. Writing it back presents a retired credential, and a server implementing replay
detection may revoke the entire family and sign the user out everywhere. Eviction costs
one manual login. Guessing can cost all of them. This applies to IndexedDB exactly as it
does to cookies: that is where refresh tokens actually live.

**Eviction is by host, and cookies and storage are evicted together.** A cookie's domain
and an origin's host are different strings for the same site: `.linkedin.com` against
`https://www.linkedin.com`. Matching them exactly meant a cookie conflict silently spared
every origin of that site, and the gap between the two blast radii is where the
half-identity lived.

**A cap refuses the write rather than truncating.** Trimming to fit would make which login
survives depend on argument order. Violations are reported on the result instead of raised
so that `--dry-run` can still show the operator every decision alongside the reason
nothing can be written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, _host_of
from .state import StateError, load, save


class MergeError(Exception):
    """A merge that must not proceed: an input we cannot trust, or a bad invocation."""


# A cookie's identity, per RFC 6265: the triple, not the name. A name-keyed merge would
# cross-contaminate `__Host-` and `__Secure-` variants of the same name.
CookieKey = tuple[str, str, str]


def cookie_key(cookie: dict[str, Any]) -> CookieKey:
    return (_text(cookie.get("name")), _text(cookie.get("domain")),
            _text(cookie.get("path")) or "/")


def _text(value: Any) -> str:
    """Stringify for comparison, mapping absent and null to empty.

    `str(None)` would write the literal four characters `None` into an identity file and
    make a null compare unequal to a real value, manufacturing a conflict out of nothing.
    """
    return "" if value is None else str(value)


def hosts_match(a: str, b: str) -> bool:
    """Whether two hostnames belong to the same site for eviction purposes.

    Deliberately symmetric. A cookie scoped to `.linkedin.com` and an origin at
    `https://www.linkedin.com` are the same identity, and treating them as different is
    how a site survives being evicted.
    """
    if not a or not b:
        return a == b
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _matches_any(host: str, hosts: set[str]) -> bool:
    return any(hosts_match(host, other) for other in hosts)


@dataclass
class OriginDecision:
    """What happened to one origin or cookie host, and why. The audit trail."""

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
    cap_violations: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        """Size of the file as it will actually be written, not of a compact encoding.

        `caps.total_bytes` is documented as the size a reader sees with `ls`, and `save`
        writes with `indent=2`, which is commonly 1.5 to 2 times larger.
        """
        return len(json.dumps(self.state, indent=2) + "\n")

    @property
    def writable(self) -> bool:
        return not self.cap_violations


def _origins_by_name(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for origin in state.get("origins") or []:
        if isinstance(origin, dict) and origin.get("origin"):
            out[str(origin["origin"])] = origin
    return out


def _cookies_by_key(state: dict[str, Any]) -> dict[CookieKey, dict[str, Any]]:
    return {cookie_key(c): c for c in (state.get("cookies") or [])
            if isinstance(c, dict)}


def _local_storage(origin: dict[str, Any]) -> dict[str, str]:
    items = origin.get("localStorage") or []
    return {_text(i.get("name")): _text(i.get("value"))
            for i in items if isinstance(i, dict) and i.get("name") is not None}


def _as_local_storage(mapping: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in sorted(mapping.items())]


def _idb(origin: dict[str, Any] | None) -> list[Any]:
    value = (origin or {}).get("indexedDB")
    return value if isinstance(value, list) else []


def _expires(cookie: dict[str, Any]) -> float:
    try:
        return float(cookie.get("expires", -1))
    except (TypeError, ValueError):
        return -1.0


def merge(root: dict[str, Any], lanes: dict[str, dict[str, Any]],
          cfg: Config | None = None) -> MergeResult:
    """Three-way merge *lanes* onto *root*, with root as the common ancestor.

    `lanes` maps a lane's name to its captured state so every decision can name the lane
    responsible. Lanes are processed in sorted name order, so the result is a function of
    the inputs rather than of the order they were passed on a command line.
    """
    ordered = {name: lanes[name] for name in sorted(lanes)}
    root_origins = _origins_by_name(root)
    root_cookies = _cookies_by_key(root)
    lane_cookies = {name: _cookies_by_key(state) for name, state in ordered.items()}
    lane_origins = {name: _origins_by_name(state) for name, state in ordered.items()}

    # --- every host this merge touches, from both cookies and origins, both sides ----
    every_host: set[str] = {_host_of(name) for name in root_origins}
    every_host |= {_host_of(key[1]) for key in root_cookies}
    for name in ordered:
        every_host |= {_host_of(origin) for origin in lane_origins[name]}
        every_host |= {_host_of(key[1]) for key in lane_cookies[name]}
    every_host.discard("")

    sticky: set[str] = set()
    rotating: set[str] = set()
    if cfg is not None:
        for host in every_host:
            rule = cfg.rule_for(host)
            if rule.sticky:
                sticky.add(host)
            if rule.rotates:
                rotating.add(host)

    # A rotating site must be held by one lane only. Counted over cookies as well as
    # origins: a session that lives purely in cookies has no entry in `origins` at all,
    # and that is exactly the kind of site whose token family gets revoked.
    touched: dict[str, set[str]] = {}
    for name in ordered:
        for origin in lane_origins[name]:
            touched.setdefault(_host_of(origin), set()).add(name)
        for key in lane_cookies[name]:
            touched.setdefault(_host_of(key[1]), set()).add(name)
    rotating_violations = sorted(
        f"{host} was touched by {len(who)} lanes ({', '.join(sorted(who))})"
        for host, who in touched.items()
        if _matches_any(host, rotating) and len(who) > 1
    )

    # --- PASS 1: find every conflict, in every store, before evicting anything -------
    conflicts: dict[str, list[str]] = {}       # host -> the lanes that disagreed

    def note_conflict(host: str, who: list[str]) -> None:
        conflicts.setdefault(host, [])
        for lane in who:
            if lane not in conflicts[host]:
                conflicts[host].append(lane)

    every_cookie = set(root_cookies) | {k for c in lane_cookies.values() for k in c}
    for key in sorted(every_cookie):
        ancestor = root_cookies.get(key)
        base = None if ancestor is None else _text(ancestor.get("value"))
        changed = {name: cookies[key] for name, cookies in lane_cookies.items()
                   if key in cookies and _text(cookies[key].get("value")) != base}
        if len({_text(c.get("value")) for c in changed.values()}) > 1:
            note_conflict(_host_of(key[1]), sorted(changed))

    for name_of_origin in sorted(set(root_origins) | {
            o for origins in lane_origins.values() for o in origins}):
        host = _host_of(name_of_origin)
        ancestor = root_origins.get(name_of_origin)
        base_ls = _local_storage(ancestor) if ancestor else {}
        contributors = {name: lane_origins[name][name_of_origin] for name in ordered
                        if name_of_origin in lane_origins[name]}

        keys = set(base_ls)
        for origin in contributors.values():
            keys |= set(_local_storage(origin))
        for k in sorted(keys):
            before = base_ls.get(k)
            changed_ls = {name: _local_storage(o)[k]
                          for name, o in contributors.items()
                          if k in _local_storage(o) and _local_storage(o)[k] != before}
            if len(set(changed_ls.values())) > 1:
                note_conflict(host, sorted(changed_ls))

        # IndexedDB gets the same treatment as everything else. It did not, once, and
        # that is the single most dangerous bug this module has had: refresh tokens live
        # here, and last-writer-wins on a refresh token is how a family gets revoked.
        base_idb = _idb(ancestor)
        changed_idb = {name: _idb(o) for name, o in contributors.items()
                       if _idb(o) and _idb(o) != base_idb}
        if len({json.dumps(v, sort_keys=True) for v in changed_idb.values()}) > 1:
            note_conflict(host, sorted(changed_idb))

    evicted_hosts = {host for host in conflicts if not _matches_any(host, sticky)}

    # --- PASS 2: build the merged state, skipping anything on an evicted host ---------
    merged_cookies: dict[CookieKey, dict[str, Any]] = {}
    kept = updated = added = evicted_cookies = 0

    every_cookie = set(root_cookies) | {k for c in lane_cookies.values() for k in c}
    for key in sorted(every_cookie):
        host = _host_of(key[1])
        if _matches_any(host, evicted_hosts):
            if key in root_cookies:
                evicted_cookies += 1
            continue
        ancestor = root_cookies.get(key)
        base = None if ancestor is None else _text(ancestor.get("value"))
        voters = {name: cookies[key] for name, cookies in lane_cookies.items()
                  if key in cookies}
        changed = {name: c for name, c in voters.items()
                   if _text(c.get("value")) != base}

        if changed:
            merged_cookies[key] = next(iter(changed.values()))
            if ancestor is None:
                added += 1
            else:
                updated += 1
            continue

        # The value is unchanged, but a server that extends a cookie without rotating it
        # refreshes `expires` only. Keeping root's copy forever meant root quietly aged
        # until the browser discarded the cookie on load and the next lane started
        # logged out with no error anywhere. So take the longest-lived copy.
        best = ancestor
        for candidate in voters.values():
            if best is None or _expires(candidate) > _expires(best):
                best = candidate
        if best is not None:
            merged_cookies[key] = best
        kept += 1

    merged_origins: dict[str, dict[str, Any]] = {}
    decisions: list[OriginDecision] = []

    for name_of_origin in sorted(set(root_origins) | {
            o for origins in lane_origins.values() for o in origins}):
        host = _host_of(name_of_origin)
        ancestor = root_origins.get(name_of_origin)
        contributors = {name: lane_origins[name][name_of_origin] for name in ordered
                        if name_of_origin in lane_origins[name]}

        if _matches_any(host, evicted_hosts):
            who = sorted(set(conflicts.get(host, [])) | set(contributors))
            decisions.append(OriginDecision(
                name_of_origin, "evicted",
                "two lanes changed the same value to different things, so one holds a "
                "token the server has already rotated away. Log in again.",
                who))
            continue

        if not contributors:
            decisions.append(OriginDecision(name_of_origin, "kept",
                                            "no lane visited it"))
            if ancestor is not None:
                merged_origins[name_of_origin] = json.loads(json.dumps(ancestor))
            continue

        base_ls = _local_storage(ancestor) if ancestor else {}
        merged_ls = dict(base_ls)
        changed_by: list[str] = []
        keys = set(base_ls)
        for origin in contributors.values():
            keys |= set(_local_storage(origin))
        for k in sorted(keys):
            before = base_ls.get(k)
            changed_ls = {name: _local_storage(o)[k]
                          for name, o in contributors.items()
                          if k in _local_storage(o) and _local_storage(o)[k] != before}
            if changed_ls:
                merged_ls[k] = next(iter(changed_ls.values()))
                changed_by += list(changed_ls)

        merged_idb = _idb(ancestor)
        for name, origin in contributors.items():
            candidate = _idb(origin)
            if candidate and candidate != _idb(ancestor):
                merged_idb = candidate
                changed_by.append(name)

        entry: dict[str, Any] = {"origin": name_of_origin,
                                 "localStorage": _as_local_storage(merged_ls)}
        if merged_idb:
            entry["indexedDB"] = merged_idb
        merged_origins[name_of_origin] = entry

        if ancestor is None:
            decisions.append(OriginDecision(name_of_origin, "added",
                                            "first seen in a lane",
                                            sorted(set(changed_by))))
        elif changed_by:
            decisions.append(OriginDecision(name_of_origin, "updated",
                                            "a lane changed it",
                                            sorted(set(changed_by))))
        else:
            decisions.append(OriginDecision(name_of_origin, "kept",
                                            "visited, nothing changed"))

    # A host whose session is only cookies has no origin, so without this it would be
    # evicted with nothing in the report to say so.
    reported = {_host_of(d.origin) for d in decisions}
    for host in sorted(evicted_hosts):
        if not _matches_any(host, reported):
            decisions.append(OriginDecision(
                host, "evicted",
                "two lanes changed the same cookie to different values. This host has "
                "no stored origin, so its cookies alone were dropped. Log in again.",
                sorted(conflicts.get(host, []))))

    result = MergeResult(
        state={"cookies": [merged_cookies[k] for k in sorted(merged_cookies)],
               "origins": [merged_origins[n] for n in sorted(merged_origins)]},
        decisions=decisions,
        cookies_kept=kept,
        cookies_updated=updated,
        cookies_added=added,
        cookies_evicted=evicted_cookies,
        evicted_origins=sorted(d.origin for d in decisions if d.action == "evicted"),
        rotating_violations=rotating_violations,
    )
    result.cap_violations = check_caps(result, cfg)
    return result


def check_caps(result: MergeResult, cfg: Config | None) -> list[str]:
    """Report cap violations rather than raising them.

    Raising from inside `merge` meant `--dry-run` failed identically to a real run, so an
    operator who hit a cap could not see the decisions that would have been made. The
    write is still refused; see `write_root`.
    """
    if cfg is None:
        return []
    violations: list[str] = []
    caps = cfg.caps
    if caps.total_bytes and result.total_bytes > caps.total_bytes:
        violations.append(
            f"the merged state would be {result.total_bytes} bytes as written, over the "
            f"caps.total_bytes limit of {caps.total_bytes}. Raise the cap in "
            f"autoweb.toml if this identity is legitimately this large; a real profile "
            f"with several Google origins easily is."
        )
    if caps.max_origins and len(result.state["origins"]) > caps.max_origins:
        violations.append(
            f"the merged state has {len(result.state['origins'])} origins, over the "
            f"caps.max_origins limit of {caps.max_origins}."
        )
    if caps.max_indexeddb_per_origin:
        for origin in result.state["origins"]:
            stores = 0
            for db in origin.get("indexedDB") or []:
                if isinstance(db, dict):
                    stores += len(db.get("stores") or [])
            if stores > caps.max_indexeddb_per_origin:
                violations.append(
                    f"{origin['origin']} has {stores} IndexedDB stores, over the "
                    f"caps.max_indexeddb_per_origin limit of "
                    f"{caps.max_indexeddb_per_origin}."
                )
    return violations


def merge_files(root_path: Path, lane_paths: list[Path],
                cfg: Config | None = None) -> MergeResult:
    """Load every input before anything is written, so a bad file cannot half-apply."""
    if not lane_paths:
        raise MergeError("no lane files to merge")
    try:
        root = load(root_path)
    except StateError as exc:
        raise MergeError(f"{root_path}: cannot be used as the merge ancestor: "
                         f"{exc}") from exc

    lanes: dict[str, dict[str, Any]] = {}
    for path in lane_paths:
        if path.stem in lanes:
            raise MergeError(
                f"two lane files are both named '{path.stem}' ({path}). Lane states are "
                f"keyed by filename, so one would silently replace the other. Rename "
                f"them so each lane is distinguishable."
            )
        try:
            lanes[path.stem] = load(path)
        except StateError as exc:
            raise MergeError(f"{path}: cannot be merged: {exc}") from exc
    return merge(root, lanes, cfg)


def write_root(root_path: Path, result: MergeResult) -> Path:
    """Replace the root, refusing when a cap was exceeded.

    `save` writes through a temporary file and moves the previous root aside as `.bak`,
    so a crash cannot leave a half-identity and a merge that was valid but wrong is
    recoverable.
    """
    if not result.writable:
        raise MergeError(
            "refusing to write: " + "; ".join(result.cap_violations)
        )
    save(root_path, result.state)
    return root_path


__all__ = ["CookieKey", "MergeError", "MergeResult", "OriginDecision", "check_caps",
           "cookie_key", "hosts_match", "merge", "merge_files", "write_root"]

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

**Except for the values a browser mints for itself, which are not credentials.** That
rule, applied to every cookie, is wrong in the common case, and was measured to be wrong
on the first real run. Four lanes read LinkedIn and Google read-only; `li_at` and
`JSESSIONID` came back byte-identical from all four, so nothing had rotated - and the
merge still proposed evicting `www.linkedin.com`, `accounts.google.com`, `www.google.com`
and `li.protechts.net`, leaving `0 origins`. The disagreement was entirely in
bot-management cookies: `__cf_bm`, `_px3`, `pxcts`, `__Secure-3PSIDCC`. Those are bound
to one browser instance by design, so N fresh browsers always produce N different values.
Writing that merge would have signed `root.json` out of LinkedIn as the direct result of
a read-only run. A safety rule that destroys the identity on every successful run is not
a safety rule, so a conflict confined to `VOLATILE_COOKIE_NAMES` keeps root's copy and
evicts nothing.

The same vendors write the same kind of state into localStorage, and the same run proved
it: with the cookie rule in place, `li.protechts.net` was still evicted over
`localStorage[PXdOjV695v_px-ff]`, a PerimeterX fingerprint keyed by their app id. So
`VOLATILE_STORAGE_MARKERS` does for storage keys what the cookie list does for cookies.
Matching the key rather than the host is deliberate. Evicting protechts.net is harmless
in itself, because nobody has an account there; the case that matters is the same key
appearing under `https://www.linkedin.com`, where a host-based exemption would not have
helped and a real login would have gone.

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


# Cookies a browser mints for itself: bot management, load-balancer stickiness, and
# fingerprint-bound clearances. Lanes disagreeing here says "these are two different
# browsers", which is the premise of running lanes at all, not "a credential rotated".
#
# **Measured conflicting** on the four-lane LinkedIn/Google run of 2026-10-03: `__cf_bm`,
# `_px3`, `pxcts`, `__Secure-3PSIDCC`. The rest are the same products' other cookie
# names, taken from vendor documentation and *not* observed here.
#
# Deliberately a short list of names, and deliberately not a pattern like "anything
# starting with an underscore". Everything here survives being stale because the site
# re-mints it on the next page load. A name that does not have that property would hide
# a real rotation, which is the failure this module exists to prevent, so a name goes on
# only with a reason. `JSESSIONID` is pointedly absent: LinkedIn uses it as its CSRF
# token, it is paired with `li_at`, and it did not conflict.
VOLATILE_COOKIE_NAMES = frozenset({
    "__cf_bm", "cf_clearance", "__cflb", "__cfruid",         # Cloudflare
    "_px2", "_px3", "_pxde", "_pxhd", "_pxvid", "pxcts",     # PerimeterX / HUMAN
    "_abck", "ak_bmsc", "bm_mi", "bm_sv", "bm_sz",           # Akamai
    "datadome",                                              # DataDome
    "awsalb", "awsalbcors", "awsalbtg", "awsalbtgcors",      # AWS ALB stickiness
    "__secure-1psidcc", "__secure-3psidcc",                  # Google, re-minted per browser
})

# Names carrying a per-browser id *after* the prefix, so the whole name differs too.
VOLATILE_COOKIE_PREFIXES = ("incap_ses_", "visid_incap_", "nlbi_")


# localStorage keys holding per-browser bot-management state. Substring rather than
# prefix because the vendor's app id comes first: the measured key was
# `PXdOjV695v_px-ff`.
#
# **Measured conflicting** on the four-lane run of 2026-10-03: one key matching `_px-`.
# `_px` as a leading marker is PerimeterX's documented client-side prefix and was *not*
# observed here.
VOLATILE_STORAGE_MARKERS = ("_px-", "_px_")
VOLATILE_STORAGE_PREFIXES = ("_px",)


def is_volatile_key(name: str) -> bool:
    """Whether lanes disagreeing about this storage key is expected rather than alarming.

    Narrower than it looks: a bare `_px` prefix or an embedded `_px-`. A broader rule -
    anything with an underscore, anything that looks like a hash - would hide a genuine
    token conflict, which is the failure this module exists to prevent.
    """
    lowered = name.lower()
    return (lowered.startswith(VOLATILE_STORAGE_PREFIXES)
            or any(marker in lowered for marker in VOLATILE_STORAGE_MARKERS))


def is_volatile(name: str) -> bool:
    """Whether lanes disagreeing about this cookie is expected rather than alarming.

    Case-insensitive: servers are inconsistent about it, and Chrome hands over
    `__Secure-3PSIDCC` in exactly that spelling.
    """
    lowered = name.lower()
    return (lowered in VOLATILE_COOKIE_NAMES
            or lowered.startswith(VOLATILE_COOKIE_PREFIXES))


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


def _host(value: str) -> str:
    """A cookie domain and an origin host, reduced to the same spelling.

    `config._host_of` strips a *trailing* dot but not a leading one, because for a URL
    there is never a leading one. A cookie domain has one constantly: `.linkedin.com`.
    Leaving it on meant `.linkedin.com` and `www.linkedin.com` compared as different
    sites, so a cookie conflict could not evict that site's storage and a storage
    conflict could not evict its cookies - the exact half-identity this module is
    supposed to prevent, reintroduced by reusing a normaliser written for URLs.
    """
    return _host_of(value).lstrip(".")


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
    # Of the kept cookies, how many were value-identical but had their expiry extended.
    # Broken out because "1 added, 0 updated" after four real logged-in sessions reads
    # like the merge did nothing, and whether an expiry refresh counts as an update is a
    # question the output should answer rather than the reader of this file.
    cookies_refreshed: int = 0
    # host -> volatile cookie names that disagreed and were deliberately not acted on.
    # Reported, because "nothing conflicted" and "four bot-management cookies conflicted
    # and were ignored on purpose" must not look the same from outside.
    volatile_conflicts: dict[str, list[str]] = field(default_factory=dict)

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
    every_host: set[str] = {_host(name) for name in root_origins}
    every_host |= {_host(key[1]) for key in root_cookies}
    for name in ordered:
        every_host |= {_host(origin) for origin in lane_origins[name]}
        every_host |= {_host(key[1]) for key in lane_cookies[name]}
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
            touched.setdefault(_host(origin), set()).add(name)
        for key in lane_cookies[name]:
            touched.setdefault(_host(key[1]), set()).add(name)
    rotating_violations = sorted(
        f"{host} was touched by {len(who)} lanes ({', '.join(sorted(who))})"
        for host, who in touched.items()
        if _matches_any(host, rotating) and len(who) > 1
    )

    # --- PASS 1: find every conflict, in every store, before evicting anything -------
    conflicts: dict[str, list[str]] = {}        # host -> the lanes that disagreed
    conflict_detail: dict[str, list[str]] = {}  # host -> what they disagreed about
    volatile: dict[str, set[str]] = {}          # host -> ignored volatile cookie names

    def note_conflict(host: str, who: list[str], what: str) -> None:
        conflicts.setdefault(host, [])
        for lane in who:
            if lane not in conflicts[host]:
                conflicts[host].append(lane)
        detail = conflict_detail.setdefault(host, [])
        if what not in detail:
            detail.append(what)

    def conflict_report(host: str) -> tuple[list[str], list[str]]:
        """Who disagreed, and about what, across every host that matches this one.

        A lookup keyed on the exact host was wrong: a cookie on `.linkedin.com` reduces
        to `linkedin.com` while its origin is `www.linkedin.com`, so eviction fired
        through `hosts_match` but the report came back empty and named neither a lane nor
        a value. An eviction notice that cannot say what triggered it is how a merge bug
        gets mistaken for token rotation.
        """
        who: list[str] = []
        what: list[str] = []
        for other, lanes_involved in conflicts.items():
            if hosts_match(host, other):
                who += [x for x in lanes_involved if x not in who]
                what += [x for x in conflict_detail.get(other, []) if x not in what]
        return who, what

    every_cookie = set(root_cookies) | {k for c in lane_cookies.values() for k in c}
    for key in sorted(every_cookie):
        ancestor = root_cookies.get(key)
        base = None if ancestor is None else _text(ancestor.get("value"))
        changed = {name: cookies[key] for name, cookies in lane_cookies.items()
                   if key in cookies and _text(cookies[key].get("value")) != base}
        if len({_text(c.get("value")) for c in changed.values()}) > 1:
            if is_volatile(key[0]):
                volatile.setdefault(_host(key[1]), set()).add(key[0])
                continue
            note_conflict(_host(key[1]), sorted(changed), f"cookie {key[0]}")

    for name_of_origin in sorted(set(root_origins) | {
            o for origins in lane_origins.values() for o in origins}):
        host = _host(name_of_origin)
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
                if is_volatile_key(k):
                    volatile.setdefault(host, set()).add(f"localStorage[{k}]")
                    continue
                note_conflict(host, sorted(changed_ls), f"localStorage[{k}]")

        # IndexedDB gets the same treatment as everything else. It did not, once, and
        # that is the single most dangerous bug this module has had: refresh tokens live
        # here, and last-writer-wins on a refresh token is how a family gets revoked.
        base_idb = _idb(ancestor)
        changed_idb = {name: _idb(o) for name, o in contributors.items()
                       if _idb(o) and _idb(o) != base_idb}
        if len({json.dumps(v, sort_keys=True) for v in changed_idb.values()}) > 1:
            note_conflict(host, sorted(changed_idb), "indexedDB")

    evicted_hosts = {host for host in conflicts if not _matches_any(host, sticky)}

    # --- PASS 2: build the merged state, skipping anything on an evicted host ---------
    merged_cookies: dict[CookieKey, dict[str, Any]] = {}
    kept = updated = added = evicted_cookies = refreshed = 0

    every_cookie = set(root_cookies) | {k for c in lane_cookies.values() for k in c}
    for key in sorted(every_cookie):
        host = _host(key[1])
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

        if is_volatile(key[0]) and \
                len({_text(c.get("value")) for c in changed.values()}) > 1:
            # A disagreement pass 1 declined to evict over. Taking a lane's copy would
            # import one browser's fingerprint into the shared identity, which is exactly
            # what these cookies encode; root's copy is stale at worst. A volatile cookie
            # absent from root is left absent rather than crowning a winner.
            if ancestor is not None:
                merged_cookies[key] = ancestor
                kept += 1
            continue

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
        if ancestor is not None and best is not ancestor:
            refreshed += 1

    merged_origins: dict[str, dict[str, Any]] = {}
    decisions: list[OriginDecision] = []

    for name_of_origin in sorted(set(root_origins) | {
            o for origins in lane_origins.values() for o in origins}):
        host = _host(name_of_origin)
        ancestor = root_origins.get(name_of_origin)
        contributors = {name: lane_origins[name][name_of_origin] for name in ordered
                        if name_of_origin in lane_origins[name]}

        if _matches_any(host, evicted_hosts):
            disagreed, about = conflict_report(host)
            decisions.append(OriginDecision(
                name_of_origin, "evicted",
                f"lanes disagreed on {', '.join(about) or 'a stored value'}, so one of "
                f"them holds a token the server has already rotated away. Log in again.",
                sorted(set(disagreed) | set(contributors))))
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
            if len(set(changed_ls.values())) > 1 and is_volatile_key(k):
                # Ignored in pass 1, so there is no winner to crown here either. Root's
                # value stays; a key absent from root is left absent.
                continue
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
    reported = {_host(d.origin) for d in decisions}
    for host in sorted(evicted_hosts):
        if not _matches_any(host, reported):
            disagreed, about = conflict_report(host)
            decisions.append(OriginDecision(
                host, "evicted",
                f"lanes disagreed on {', '.join(about) or 'a cookie'}. This host has no "
                f"stored origin, so its cookies alone were dropped. Log in again.",
                sorted(disagreed)))

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
        cookies_refreshed=refreshed,
        volatile_conflicts={host: sorted(names)
                            for host, names in sorted(volatile.items())},
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


__all__ = ["VOLATILE_COOKIE_NAMES", "VOLATILE_COOKIE_PREFIXES",
           "VOLATILE_STORAGE_MARKERS", "VOLATILE_STORAGE_PREFIXES", "CookieKey",
           "MergeError", "MergeResult", "OriginDecision", "check_caps", "cookie_key",
           "hosts_match", "is_volatile", "is_volatile_key", "merge", "merge_files",
           "write_root"]

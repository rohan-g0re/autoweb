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
evicted, resolved or merged. An earlier version detected cookie conflicts, evicted, and
then found localStorage conflicts afterwards with nothing left to act on them, which kept
the cookie jar of a site it had already decided was unsafe to keep. That is a
half-identity: a browser that looks signed in and is not, which is harder to diagnose than
being signed out.

The rules, each of which exists because the obvious alternative is worse:

**Never propagate a deletion.** A lane that lacks a cookie did not necessarily delete it;
far more often it never visited that site. Treating absence as removal would erode the
identity a little on every run.

**Unknown is not empty.** `storageState()` returns `indexedDB: []` both when a lane
harvested an origin and found nothing and when nothing harvested IndexedDB at all. The
file cannot tell you which, so an empty list never overwrites a non-empty ancestor. The
cost is that genuinely clearing IndexedDB does not propagate, which is the cheaper mistake
when the alternative is destroying a Firebase or Auth0 login.

**A conflict is resolved by recency.** When two lanes change one value to different
things, the merge keeps the one issued most recently: the cookie with the later
`expires`, where a session cookie (`expires` of -1, or none) ranks below any dated one,
and the lane file saved last when expiry does not decide it. localStorage and IndexedDB
carry no expiry, so for them the save time alone decides. A tie on both falls to the lane
whose name sorts first, so the result is still a function of the inputs. Every pick is
reported on `MergeResult.resolved`, naming the winner, the losers and the timestamps
that decided it, because "nothing conflicted" and "four values conflicted and one was
chosen" must not look the same from outside.

This replaced a rule that evicted the whole host on any conflict, on the reasoning that
one of two different values must be a token the server had already rotated away, and
presenting it could get the family revoked. The reasoning was sound and the rule was not,
because a value alone cannot tell a rotated credential from one a browser minted for
itself. The first real run showed the cost. Four lanes read LinkedIn and Google
read-only; `li_at` and `JSESSIONID` came back byte-identical from all four, so nothing had
rotated - and the merge still proposed evicting `www.linkedin.com`,
`accounts.google.com`, `www.google.com` and `li.protechts.net`, leaving `0 origins`. The
disagreement was entirely in bot-management state, measured as `__cf_bm`, `_px3`,
`pxcts`, `__Secure-3PSIDCC` and `localStorage[PXdOjV695v_px-ff]`. Each is bound to one
browser instance by design, so N fresh browsers always produce N values. Writing that
merge would have signed `root.json` out of LinkedIn as the direct result of a read-only
run.

The fix at the time was an allowlist of such names, and an allowlist fails open in the
wrong direction. Every per-browser value not on it - Google's plain `SIDCC`, GA4's
`_ga_<id>`, Datadog's `_dd_s`, found in review rather than on a run - still turned a
read-only run into a sign-out, and the list could only be completed by losing a login to
each missing name first. So the list is gone, and those values now merge like any other:
one lane's copy is no worse than root's, since either is one browser's and the site
re-mints it on the next load.

Recency is never worse than eviction. If the server revoked the token family by reuse
detection, that happened during the run, when two browsers presented one token, and no
pick made afterwards can undo it. If the server allows concurrent sessions, every value
works and any pick is fine. If it keeps only the most recently issued token, that token
carries the latest expiry, and expiry is what picks it. Eviction costs a manual login in
all three cases; recency costs one in none. What catches a token that is dead anyway is
the next run's `autoweb goal check`, which fails on a declared assertion rather than
guessing from a value.

**`rotates = true` is the declared exception.** For a host whose config rule says it
uses reuse detection, a conflict still evicts that host, because the user has said the
re-login should be forced now rather than discovered by the next run. `sticky` keeps its
meaning, never evict, so a host that is both sticky and rotates is resolved by recency
like any other: sticky wins. A rotating host with no conflict merges with no warning. An
earlier warning fired whenever more than one lane "touched" a rotating host, and it fired
on every multi-lane run, because an isolated lane exports every cookie root gave it
whether or not it opened the site.

**IndexedDB is compared per database and per object store.** One JSON blob per origin
made a lane that changed database A conflict with a lane that changed database B, though
neither touched the other's data. Each store is now its own unit, and so are a
database's own fields such as its version. Two lanes changing one unit differently
resolve by save time. A database or store a lane did not export is absent, not deleted,
by the two rules above.

**Eviction is by host, and cookies and storage are evicted together.** A cookie's domain
and an origin's host are different strings for the same site: `.linkedin.com` against
`https://www.linkedin.com`. Matching them exactly meant a cookie conflict silently spared
every origin of that site, and the gap between the two blast radii is where the
half-identity lived. Eviction only happens to a `rotates` host now, and the rule is
unchanged there.

**Nothing may vanish unreported.** Every cookie the ancestor held must end up either in
the result or in the evicted count, and the merge refuses to write if it cannot account
for one. This is not belt-and-braces: the first real merge wrote a `root.json` that had
lost 737 cookie rows while reporting `0 evicted`. The identity key was
`(name, domain, path)`, which under CHIPS is not an identity at all - a partitioned
cookie is per top-level site, so 36 names appeared in several partitions each and all but
one copy of each was silently dropped. The count said "3166 kept" because it was counting
keys rather than rows, so the arithmetic that should have exposed it was the thing hiding
it. LinkedIn and Google were unaffected and `state verify` passed, which is precisely why
a loud invariant is worth more here than a careful reading of the summary.

**A cap refuses the write rather than truncating.** Trimming to fit would make which login
survives depend on argument order. Violations are reported on the result instead of raised
so that `--dry-run` can still show the operator every decision alongside the reason
nothing can be written.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Config, _host_of
from .state import StateError, load, save


class MergeError(Exception):
    """A merge that must not proceed: an input we cannot trust, or a bad invocation."""


# A cookie's identity: name, domain, path - per RFC 6265 - *and* the partition key. A
# name-keyed merge would cross-contaminate `__Host-` and `__Secure-` variants of the same
# name, and a triple-keyed one loses partitioned cookies, which is not hypothetical: the
# first real merge dropped 737 rows this way. Under CHIPS (`Set-Cookie: ...; Partitioned`)
# a cookie is scoped to the top-level site it was set under, so the same name, domain and
# path in two partitions are two different cookies that must both survive.
CookieKey = tuple[str, str, str, str]


def cookie_key(cookie: dict[str, Any]) -> CookieKey:
    return (_text(cookie.get("name")), _text(cookie.get("domain")),
            _text(cookie.get("path")) or "/", _partition(cookie))


def _partition(cookie: dict[str, Any]) -> str:
    """One stable string for a cookie's partition, or empty for an unpartitioned one.

    Chromium's `CookiePartitionKey` is a *pair*: the top-level site and a
    has-cross-site-ancestor bit. Both halves are part of the identity, so two cookies
    differing only in that bit are two cookies, and Chrome exports both.

    Playwright carries the pair two ways. As an object it is `topLevelSite` plus
    `hasCrossSiteAncestor`; as a bare site string the bit arrives beside it as
    `_crHasCrossSiteAncestor`. Normalising to the pair rather than to the encoding makes
    both spellings agree while keeping the bit. The previous version canonicalised an
    object partitionKey with sorted keys, which kept the bit, and ignored the sibling
    field entirely, so a string-encoded pair lost it and two real cookies collapsed into
    one. Measured on the re-merge: two `tag_user_id` rows on `.trueclassictees.com`, one
    partition site, the bit True on one and False on the other, and only one survived.

    An absent bit on a partitioned cookie means **true**, which is Playwright's own
    semantics rather than a guess: it writes the field for every partitioned cookie on
    export, and on import reads it as `_crHasCrossSiteAncestor ?? true`. **Read from the
    playwright-core bundle of both playwright 1.63 and playwright-core 1.64**, the latter
    being the copy the lanes' MCP actually resolves; the five relevant lines are
    identical in the two.

    This was briefly a third value, "absent", on the reasoning that splitting one cookie
    into two is the recoverable direction. That reasoning is wrong here, and usefully so.
    Two rows differing only in absent-versus-true land in the *same* browser partition on
    seed, so one silently overwrites the other - and the merge would report two surviving
    identities while the browser kept one. An accounting invariant that disagrees with
    the browser is worse than no invariant, because it is the thing you check the browser
    against.
    """
    raw = cookie.get("partitionKey")
    if isinstance(raw, dict):
        site = _text(raw.get("topLevelSite"))
        ancestor = raw.get("hasCrossSiteAncestor")
    else:
        site = _text(raw)
        ancestor = cookie.get("_crHasCrossSiteAncestor")
    if not site:
        return ""
    bit = "1" if (ancestor is None or ancestor) else "0"
    return f"{site}|{bit}"


def _row_counts(state: dict[str, Any]) -> dict[CookieKey, int]:
    """How many *rows* each cookie identity has, which `_cookies_by_key` cannot say.

    `_cookies_by_key` builds a dict, so duplicate keys collapse on load and the loss
    happens before the merge runs. Counting rows separately is what lets the invariant
    notice that a key arrived twice and left once - the second CHIPS defect was invisible
    to a set-membership check because both rows mapped to one key that *was* present in
    the output.
    """
    counts: dict[CookieKey, int] = {}
    for cookie in state.get("cookies") or []:
        if isinstance(cookie, dict):
            key = cookie_key(cookie)
            counts[key] = counts.get(key, 0) + 1
    return counts


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
    cap_violations: list[str] = field(default_factory=list)
    # Of the kept cookies, how many were value-identical but had their expiry extended.
    # Broken out because "1 added, 0 updated" after four real logged-in sessions reads
    # like the merge did nothing, and whether an expiry refresh counts as an update is a
    # question the output should answer rather than the reader of this file.
    cookies_refreshed: int = 0
    # Ancestor cookies that are neither in the result nor counted as evicted. Always
    # empty; a non-empty list is a bug in this module and refuses the write, because the
    # alternative is what already happened once - a root.json quietly missing 737 rows
    # that still passed `state verify` and looked entirely healthy.
    lost_cookies: list[str] = field(default_factory=list)
    # One line per value two or more lanes changed differently, saying which lane's copy
    # was kept and the expiry or save time that decided it. Reported, because a pick the
    # operator cannot see is a guess the operator cannot check.
    resolved: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        """Size of the file as it will actually be written, not of a compact encoding.

        `caps.total_bytes` is documented as the size a reader sees with `ls`, and `save`
        writes with `indent=2`, which is commonly 1.5 to 2 times larger.
        """
        return len(json.dumps(self.state, indent=2) + "\n")

    @property
    def cookie_rows(self) -> int:
        """Rows as written. `cookies_kept` counts identities, and the difference between
        the two is where the CHIPS collapse hid."""
        return len(self.state.get("cookies") or [])

    @property
    def writable(self) -> bool:
        return not self.cap_violations and not self.lost_cookies


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


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


# --- IndexedDB, one unit per database's own fields and one per object store --------
# A unit is (database name, store name), with None as the store for the database's own
# fields (`version` and anything else beside `stores`). Comparing per unit is what lets
# a lane that changed database A and a lane that changed database B both survive.
IdbUnit = tuple[str, str | None]


def _unit_order(unit: IdbUnit) -> tuple[str, int, str]:
    return (unit[0], 0 if unit[1] is None else 1, unit[1] or "")


def _unit_label(unit: IdbUnit) -> str:
    return f"indexedDB[{unit[0]}]" if unit[1] is None else f"indexedDB[{unit[0]}/{unit[1]}]"


def _idb_units(idb: list[Any]) -> dict[IdbUnit, Any]:
    """Split an origin's IndexedDB into units. Entries without a name are skipped here;
    an ancestor's are carried over verbatim by `_rebuild_idb`, so nothing is lost."""
    units: dict[IdbUnit, Any] = {}
    for db in idb:
        if not isinstance(db, dict) or db.get("name") is None:
            continue
        name = _text(db["name"])
        units[(name, None)] = {k: v for k, v in db.items() if k != "stores"}
        for store in db.get("stores") or []:
            if isinstance(store, dict) and store.get("name") is not None:
                units[(name, _text(store["name"]))] = store
    return units


def _ordered(first: list[str], present: set[str]) -> list[str]:
    """Ancestor order for what the ancestor had, then anything new in sorted order, so an
    untouched database is written back the way it was read."""
    head = [n for n in first if n in present]
    return head + sorted(present - set(head))


def _rebuild_idb(base: list[Any], units: dict[IdbUnit, Any]) -> list[Any]:
    base_dbs = [db for db in base if isinstance(db, dict) and db.get("name") is not None]
    base_order = [_text(db["name"]) for db in base_dbs]
    store_order = {_text(db["name"]): [_text(s["name"]) for s in db.get("stores") or []
                                       if isinstance(s, dict) and s.get("name") is not None]
                   for db in base_dbs}
    out: list[Any] = []
    for db_name in _ordered(base_order, {u[0] for u in units}):
        meta = units.get((db_name, None), {"name": db_name})
        stores = {u[1]: v for u, v in units.items() if u[0] == db_name and u[1] is not None}
        entry = dict(meta)
        entry["stores"] = [stores[s] for s in _ordered(store_order.get(db_name, []),
                                                        set(stores))]
        out.append(entry)
    # Anything the ancestor held that this module cannot name is kept, never dropped.
    out += [db for db in base if not (isinstance(db, dict) and db.get("name") is not None)]
    return out


def _idb_changes(base: dict[IdbUnit, Any], contributors: dict[str, dict[str, Any]]
                 ) -> dict[IdbUnit, dict[str, Any]]:
    """Per unit, the lanes whose copy differs from the ancestor's. A lane with an empty
    list contributes no units, which is "unknown is not empty"; a lane without a unit says
    nothing about it, which is "never propagate a deletion"."""
    changes: dict[IdbUnit, dict[str, Any]] = {}
    for name, origin in contributors.items():
        for unit, value in _idb_units(_idb(origin)).items():
            if unit not in base or _canon(value) != _canon(base[unit]):
                changes.setdefault(unit, {})[name] = value
    return changes


# --- recency ------------------------------------------------------------------------


def _iso(seconds: float) -> str:
    try:
        return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return str(seconds)


def _most_recent(candidates: dict[str, Any], saved_at: dict[str, float],
                 expiry: Callable[[Any], float] | None = None) -> str:
    """The lane whose copy was issued last: expiry first when there is one, then the
    lane file's save time. Names are scanned in sorted order and `max` keeps the first of
    equals, so a full tie goes to the lane whose name sorts first."""
    def rank(name: str) -> tuple[float, float]:
        issued = expiry(candidates[name]) if expiry is not None else 0.0
        return (issued, saved_at.get(name, 0.0))
    return max(sorted(candidates), key=rank)


def _resolved_line(host: str, what: str, winner: str, losers: list[str],
                   saved_at: dict[str, float], expires: float | None = None) -> str:
    saved = _iso(saved_at[winner]) if winner in saved_at else "unknown"
    when = f"saved {saved}"
    if expires is not None:
        when = f"expires {'session' if expires < 0 else _iso(expires)} / {when}"
    return (f"{host} {what} kept {winner}'s value ({when}) over "
            + ", ".join(f"{name}'s" for name in losers))


def _cookie_label(key: CookieKey) -> str:
    label = f"cookie {key[0]}"
    return label + (f" [partition {key[3]}]" if key[3] else "")


def merge(root: dict[str, Any], lanes: dict[str, dict[str, Any]],
          cfg: Config | None = None,
          saved_at: dict[str, float] | None = None) -> MergeResult:
    """Three-way merge *lanes* onto *root*, with root as the common ancestor.

    `lanes` maps a lane's name to its captured state so every decision can name the lane
    responsible. Lanes are processed in sorted name order, so the result is a function of
    the inputs rather than of the order they were passed on a command line.

    `saved_at` maps a lane's name to when its file was saved, in POSIX seconds. It breaks
    ties between cookies of equal expiry and decides storage conflicts outright, and it is
    an argument rather than a `stat` call so this function stays pure. A lane missing
    from it ranks as saved at the epoch and is reported as "saved unknown".
    """
    ordered = {name: lanes[name] for name in sorted(lanes)}
    saved = dict(saved_at or {})
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

    # --- PASS 1: find every conflict, in every store, before evicting anything -------
    conflicts: dict[str, list[str]] = {}        # host -> the lanes that disagreed
    conflict_detail: dict[str, list[str]] = {}  # host -> what they disagreed about

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
            note_conflict(_host(key[1]), sorted(changed), f"cookie {key[0]}")

    every_origin = sorted(set(root_origins) | {
        o for origins in lane_origins.values() for o in origins})
    for name_of_origin in every_origin:
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
                note_conflict(host, sorted(changed_ls), f"localStorage[{k}]")

        # IndexedDB gets the same treatment as everything else. It did not, once, and
        # that is the single most dangerous bug this module has had: refresh tokens live
        # here, and an unreported last-writer-wins on a refresh token is a guess nobody
        # can check. Compared per unit, so two lanes in two databases are not a conflict.
        changes = _idb_changes(_idb_units(_idb(ancestor)), contributors)
        for unit in sorted(changes, key=_unit_order):
            if len({_canon(v) for v in changes[unit].values()}) > 1:
                note_conflict(host, sorted(changes[unit]), _unit_label(unit))

    # Only a host the user declared as rotating is evicted, and sticky overrides that.
    evicted_hosts = {host for host in conflicts
                     if _matches_any(host, rotating) and not _matches_any(host, sticky)}

    # --- PASS 2: build the merged state, resolving by recency off evicted hosts -------
    merged_cookies: dict[CookieKey, dict[str, Any]] = {}
    kept = updated = added = evicted_cookies = refreshed = 0
    resolved: list[str] = []

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

        if changed:
            winner = _most_recent(changed, saved, _expires)
            merged_cookies[key] = changed[winner]
            won = _text(changed[winner].get("value"))
            losers = [n for n in sorted(changed) if _text(changed[n].get("value")) != won]
            if losers:
                resolved.append(_resolved_line(host, _cookie_label(key), winner, losers,
                                               saved, _expires(changed[winner])))
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

    for name_of_origin in every_origin:
        host = _host(name_of_origin)
        ancestor = root_origins.get(name_of_origin)
        contributors = {name: lane_origins[name][name_of_origin] for name in ordered
                        if name_of_origin in lane_origins[name]}

        if _matches_any(host, evicted_hosts):
            disagreed, about = conflict_report(host)
            decisions.append(OriginDecision(
                name_of_origin, "evicted",
                f"marked `rotates = true` and lanes disagreed on "
                f"{', '.join(about) or 'a stored value'}, so either value may be a token "
                f"the server has already retired. Log in again.",
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
            if not changed_ls:
                continue
            winner = _most_recent(changed_ls, saved)
            merged_ls[k] = changed_ls[winner]
            changed_by += list(changed_ls)
            losers = [n for n in sorted(changed_ls) if changed_ls[n] != changed_ls[winner]]
            if losers:
                resolved.append(_resolved_line(host, f"localStorage[{k}]", winner,
                                               losers, saved))

        base_idb = _idb(ancestor)
        units = _idb_units(base_idb)
        changes = _idb_changes(units, contributors)
        for unit in sorted(changes, key=_unit_order):
            candidates = changes[unit]
            winner = _most_recent(candidates, saved)
            units[unit] = candidates[winner]
            changed_by += list(candidates)
            won = _canon(candidates[winner])
            losers = [n for n in sorted(candidates) if _canon(candidates[n]) != won]
            if losers:
                resolved.append(_resolved_line(host, _unit_label(unit), winner, losers,
                                               saved))
        merged_idb = _rebuild_idb(base_idb, units) if changes else base_idb

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
                f"marked `rotates = true` and lanes disagreed on "
                f"{', '.join(about) or 'a cookie'}. This host has no stored origin, so "
                f"its cookies alone were dropped. Log in again.",
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
        cookies_refreshed=refreshed,
        resolved=resolved,
    )
    # Row counts, not set membership. A key with two rows in and one out is a loss even
    # though the key itself is present, and that is the only shape the second CHIPS
    # defect had. It also means a pair this module's key definition cannot tell apart
    # refuses the write instead of quietly keeping whichever came last.
    lost: list[str] = []
    for key, rows_in in sorted(_row_counts(root).items()):
        if _matches_any(_host(key[1]), evicted_hosts):
            continue
        rows_out = 1 if key in merged_cookies else 0
        if rows_out < rows_in:
            where = f"{key[0]} on {key[1]}{key[2]}"
            if key[3]:
                where += f" [partition {key[3]}]"
            lost.append(f"{where}: {rows_in} row(s) in, {rows_out} out")
    result.lost_cookies = lost
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
    saved_at: dict[str, float] = {}
    for path in lane_paths:
        if path.stem in lanes:
            raise MergeError(
                f"two lane files are both named '{path.stem}' ({path}). Lane states are "
                f"keyed by filename, so one would silently replace the other. Rename "
                f"them so each lane is distinguishable."
            )
        try:
            lanes[path.stem] = load(path)
            # When the lane saved its state, which is when its values were last issued
            # as far as this file can know. Recency needs it; `merge` stays pure.
            saved_at[path.stem] = path.stat().st_mtime
        except (StateError, OSError) as exc:
            raise MergeError(f"{path}: cannot be merged: {exc}") from exc
    return merge(root, lanes, cfg, saved_at)


def write_root(root_path: Path, result: MergeResult) -> Path:
    """Replace the root, refusing when a cap was exceeded.

    `save` writes through a temporary file and moves the previous root aside as `.bak`,
    so a crash cannot leave a half-identity and a merge that was valid but wrong is
    recoverable.
    """
    if result.lost_cookies:
        shown = result.lost_cookies[:10]
        raise MergeError(
            f"refusing to write: {len(result.lost_cookies)} cookie(s) in the ancestor "
            f"are neither in the merged state nor counted as evicted, so this merge "
            f"would lose them silently. This is a bug in autoweb.merge, not in your "
            f"inputs, and the previous root is untouched. A cookie listed with "
            f"more rows in than out means two rows share this module's idea of a "
            f"cookie identity and it cannot tell them apart. First few: "
            + "; ".join(shown)
        )
    if not result.writable:
        raise MergeError(
            "refusing to write: " + "; ".join(result.cap_violations)
        )
    save(root_path, result.state)
    return root_path


__all__ = ["CookieKey", "MergeError", "MergeResult", "OriginDecision", "check_caps",
           "cookie_key", "hosts_match", "merge", "merge_files", "write_root"]

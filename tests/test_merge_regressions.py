"""Every test here is a defect that actually existed in `autoweb/merge.py`.

An independent review of the first version found three ways to produce a `root.json` that
was plausible, passed every test then present, and was wrong. All three were silent, and
the silence is what made them dangerous: a crash is visible, a half-identity is not.

They are kept in their own file so that nobody tidying up the main suite deletes one for
looking redundant. None of them is redundant. Each describes a path by which a real
person's logged-in sessions were destroyed or, worse, a retired OAuth refresh token was
written back where presenting it could revoke the whole family.

The rule they were written against changed: a conflict no longer evicts a host unless
its config says `rotates = true`, and otherwise keeps the most recently issued value.
Tests whose defect lives in the eviction path now set `rotates`, so the path they guard
is still exercised. The real-world inputs - `__cf_bm`, `PXdOjV695v_px-ff`, the partition
keys - are kept as inputs, so this file still proves the historical bugs stay fixed.
"""

from __future__ import annotations

import json

import pytest

from autoweb.config import Config
from autoweb.merge import MergeError, merge, merge_files, write_root


def cookie(name, value, domain=".a.example", path="/", expires=1759000000):
    return {"name": name, "value": value, "domain": domain, "path": path,
            "expires": expires, "httpOnly": True, "secure": True, "sameSite": "Lax"}


def origin(name, local=None, idb=None):
    entry = {"origin": name,
             "localStorage": [{"name": k, "value": v} for k, v in (local or {}).items()]}
    if idb is not None:
        entry["indexedDB"] = idb
    return entry


def state(cookies=None, origins=None):
    return {"cookies": cookies or [], "origins": origins or []}


def config_at(tmp_path, body=""):
    (tmp_path / "autoweb.toml").write_text(body, encoding="utf-8")
    return Config.load(tmp_path)


def rotates(tmp_path, host="a.example"):
    return config_at(tmp_path, f'[origins."{host}"]\nrotates = true\n')


T0 = 1790000000.0


# --- the three criticals ----------------------------------------------------


def test_two_lanes_with_different_indexeddb_are_not_a_silent_last_writer_wins():
    """IndexedDB had no conflict detection at all, so the last contributor silently won,
    in the one store where OAuth refresh tokens actually live.

    Now each store is its own unit: lane 1's store A and lane 2's store B both land, and
    the one thing they really disagree on, the database version, is picked by save time
    and reported. Nothing is decided without a line saying so."""
    root = state(origins=[origin("https://www.a.example", {},
                                 idb=[{"name": "fb", "version": 1, "stores": []}])])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://www.a.example", {},
                                        idb=[{"name": "fb", "version": 2,
                                              "stores": [{"name": "A"}]}])]),
        "lane-2": state(origins=[origin("https://www.a.example", {},
                                        idb=[{"name": "fb", "version": 3,
                                              "stores": [{"name": "B"}]}])]),
    }, saved_at={"lane-1": T0, "lane-2": T0 + 1})
    assert result.state["origins"][0]["indexedDB"] == [
        {"name": "fb", "version": 3, "stores": [{"name": "A"}, {"name": "B"}]}]
    assert len(result.resolved) == 1
    assert result.resolved[0].startswith(
        "www.a.example indexedDB[fb] kept lane-2's value")


def test_a_rotates_host_still_evicts_on_an_indexeddb_conflict(tmp_path):
    """Refresh tokens live here, so the declared exception has to reach IndexedDB."""
    root = state(origins=[origin("https://www.a.example", {},
                                 idb=[{"name": "fb", "version": 1,
                                       "stores": [{"name": "tokens", "v": 0}]}])])
    result = merge(root, {
        f"lane-{n}": state(origins=[origin("https://www.a.example", {},
                                           idb=[{"name": "fb", "version": 1,
                                                 "stores": [{"name": "tokens",
                                                             "v": n}]}])])
        for n in (1, 2)
    }, rotates(tmp_path))
    assert result.state["origins"] == []
    reason = next(d.reason for d in result.decisions if d.action == "evicted")
    assert "indexedDB[fb/tokens]" in reason


def test_an_unchanged_lane_cannot_revert_a_lane_that_refreshed_indexeddb():
    """The nastiest of the three.

    IndexedDB was compared against the running merged value instead of the ancestor, so a
    lane that merely opened the site and changed nothing overwrote a lane that had
    genuinely refreshed its token. That writes back the credential the server has already
    rotated away, and names the innocent lane as the one that changed it. The outcome also
    flipped with the order of the filenames on the command line.
    """
    stale = [{"name": "fb", "version": 1,
              "stores": [{"name": "tokens", "records": [{"key": "rt", "value": "old"}]}]}]
    fresh = [{"name": "fb", "version": 2,
              "stores": [{"name": "tokens", "records": [{"key": "rt", "value": "new"}]}]}]
    root = state(origins=[origin("https://www.a.example", {}, idb=stale)])
    lanes = {
        "lane-1": state(origins=[origin("https://www.a.example", {}, idb=fresh)]),
        "lane-2": state(origins=[origin("https://www.a.example", {}, idb=stale)]),
    }
    # lane-2 saved later. Recency must still not let it win: it changed nothing, so it
    # is not a candidate at all, which is what comparing against the ancestor buys.
    result = merge(root, lanes, saved_at={"lane-1": T0, "lane-2": T0 + 60})
    assert result.state["origins"][0]["indexedDB"] == fresh, "the stale lane won"
    assert result.resolved == []
    assert result.decisions[0].changed_by == ["lane-1"]
    # And the answer must be a function of the inputs, not of their order.
    assert merge(root, dict(reversed(list(lanes.items()))),
                 saved_at={"lane-1": T0, "lane-2": T0 + 60}).state == result.state


def test_a_local_storage_conflict_evicts_that_hosts_cookies_too(tmp_path):
    """Conflicts were detected in two passes with eviction in between, so a localStorage
    conflict found in the second pass had nothing left to act on. The site's cookie jar
    survived a decision that the site was unsafe to keep. Eviction is now only for a
    `rotates` host, and there the order of operations still decides it."""
    root = state([cookie("li_at", "V1")],
                 [origin("https://www.a.example", {"token": "T0"})])
    result = merge(root, {
        "lane-1": state([cookie("li_at", "V1")],
                        [origin("https://www.a.example", {"token": "T1"})]),
        "lane-2": state([cookie("li_at", "V1")],
                        [origin("https://www.a.example", {"token": "T2"})]),
    }, rotates(tmp_path))
    assert result.state["origins"] == []
    assert result.state["cookies"] == [], "the cookie jar outlived its own origin"


def test_a_domain_scoped_cookie_conflict_evicts_the_hosts_origins(tmp_path):
    """The one that would have fired on the first real run.

    Cookies are domain-scoped (`.linkedin.com`) and origins are host-specific
    (`https://www.linkedin.com`). The eviction set was keyed by cookie domain and probed
    with an exact match against origin host, so every realistically-shaped site survived
    its own cookie conflict while its cookies silently vanished, and the report said
    nothing had been evicted.
    """
    root = state([cookie("sid", "old", ".a.example")],
                 [origin("https://www.a.example", {"t": "T0"}),
                  origin("https://mail.a.example", {"t": "T0"})])
    result = merge(root, {
        "lane-1": state([cookie("sid", "x", ".a.example")]),
        "lane-2": state([cookie("sid", "y", ".a.example")]),
    }, rotates(tmp_path))
    assert result.state["cookies"] == []
    assert result.state["origins"] == [], "origins outlived their own cookie jar"
    assert len(result.evicted_origins) == 2


# --- the rest ---------------------------------------------------------------


def test_an_unchanged_cookie_still_gets_its_expiry_refreshed():
    """Servers commonly extend a long-lived cookie without rotating its value. Comparing
    on `value` alone meant root kept the original `expires` through every merge until it
    fell into the past, after which a seeded browser discarded the cookie on load and the
    next lane started logged out with no error anywhere."""
    root = state([cookie("li_at", "V", expires=1759000000)])
    result = merge(root, {"lane-1": state([cookie("li_at", "V", expires=1790000000)])})
    assert result.state["cookies"][0]["expires"] == 1790000000
    assert result.cookies_kept == 1, "an extended expiry is not a changed value"


def test_sticky_protects_a_host_that_exists_only_in_lane_cookies(tmp_path):
    """The hosts to consult config about were gathered from root's cookies and both sides'
    origins, but not from lane cookies, so a host first seen in a lane never reached
    `rule_for` and `sticky` could not protect it. Sticky only matters on a `rotates` host
    now, so the rule carries both."""
    cfg = config_at(tmp_path,
                    '[origins."auth.example"]\nsticky = true\nrotates = true\n')
    result = merge(state(), {
        "lane-1": state([cookie("sid", "A", "auth.example")]),
        "lane-2": state([cookie("sid", "B", "auth.example")]),
    }, cfg)
    assert result.state["cookies"], "sticky did not protect a lane-only host"
    assert result.evicted_origins == []
    assert len(result.resolved) == 1


def test_a_rotating_cookie_only_site_still_reaches_its_rule(tmp_path):
    """The rotating-site check counted lane origins only. A session living purely in
    cookies has no entry in `origins` at all, and that is exactly the kind of site whose
    token family gets revoked. The same lane-only host must reach `rule_for` for eviction
    as it once had to for the warning this replaced."""
    result = merge(state(), {
        "lane-1": state([cookie("sid", "A", "auth.example")]),
        "lane-2": state([cookie("sid", "B", "auth.example")]),
    }, rotates(tmp_path, "auth.example"))
    assert result.state["cookies"] == []
    assert result.evicted_origins == ["auth.example"]


def test_a_cookie_only_conflict_appears_in_the_report(tmp_path):
    """A conflicted host with no stored origin produced no decision at all, so cookies
    vanished and the per-origin report showed nothing. The audit trail is the only human
    check standing between this and a real identity."""
    result = merge(state([cookie("sid", "old", "cookies-only.example")]), {
        "lane-1": state([cookie("sid", "x", "cookies-only.example")]),
        "lane-2": state([cookie("sid", "y", "cookies-only.example")]),
    }, rotates(tmp_path, "cookies-only.example"))
    assert result.state["cookies"] == []
    evicted = [d for d in result.decisions if d.action == "evicted"]
    assert evicted, "cookies were dropped with nothing in the report to say so"
    assert "cookies-only.example" in evicted[0].origin


def test_total_bytes_measures_the_file_as_it_will_be_written():
    """The cap was checked against a compact encoding while `save` writes with indent=2,
    so a cap could pass at 1.9 MB and then write a 3.5 MB file."""
    result = merge(state(), {"lane-1": state([cookie("sid", "v")],
                                             [origin("https://a.example", {"k": "v"})])})
    expected = len(json.dumps(result.state, indent=2)) + 1      # trailing newline
    assert result.total_bytes == expected


def test_null_values_do_not_become_the_string_None():
    """`str(None)` would write the literal four characters None into an identity file, and
    make a null compare unequal to a real value, manufacturing a conflict from nothing."""
    null_cookie = {"name": "sid", "value": None, "domain": ".a.example", "path": "/"}
    result = merge(state([dict(null_cookie)]),
                   {"lane-1": state([dict(null_cookie)])})
    assert result.evicted_origins == []
    for c in result.state["cookies"]:
        assert c.get("value") != "None"


def test_two_lane_files_with_the_same_stem_are_refused(tmp_path):
    """Lane states are keyed by filename stem, so two files called state.json in different
    directories silently dropped one lane while the printed count still said two."""
    root_path = tmp_path / "root.json"
    root_path.write_text(json.dumps(state()), encoding="utf-8")
    first, second = tmp_path / "a", tmp_path / "b"
    for d in (first, second):
        d.mkdir()
        (d / "state.json").write_text(json.dumps(state()), encoding="utf-8")
    with pytest.raises(MergeError, match="both named"):
        merge_files(root_path, [first / "state.json", second / "state.json"])


def test_a_malformed_root_is_a_merge_error(tmp_path):
    """`merge_files` documents MergeError; the ancestor load was the one path unwrapped."""
    root_path = tmp_path / "root.json"
    root_path.write_text('{"nope": true}', encoding="utf-8")
    lane = tmp_path / "lane-1.json"
    lane.write_text(json.dumps(state()), encoding="utf-8")
    with pytest.raises(MergeError, match="ancestor"):
        merge_files(root_path, [lane])


def test_a_cap_violation_blocks_the_write_but_not_the_report(tmp_path):
    """Raising from inside the merge meant --dry-run failed identically to a real run, so
    an operator who hit a cap could not see the decisions that would have been made. The
    write is still refused; the reasoning is still printable."""
    cfg = config_at(tmp_path, "[caps]\ntotal_bytes = 200\n")
    result = merge(state(), {"lane-1": state(origins=[origin("https://a.example",
                                                             {"k": "x" * 500})])}, cfg)
    assert not result.writable
    assert result.decisions, "the operator must still be able to read the decisions"
    with pytest.raises(MergeError, match="refusing to write"):
        write_root(tmp_path / "root.json", result)
    assert not (tmp_path / "root.json").exists()


def test_an_ipv6_origin_does_not_collapse_onto_every_other_one(tmp_path):
    """merge had its own host normaliser that returned "[" for any IPv6 literal, so a
    conflict on one IPv6 origin evicted them all. It now uses the one in config."""
    root = state(origins=[origin("http://[::1]:3000", {"t": "0"}),
                          origin("http://[::2]:3000", {"t": "0"})])
    result = merge(root, {
        "lane-1": state(origins=[origin("http://[::1]:3000", {"t": "x"})]),
        "lane-2": state(origins=[origin("http://[::1]:3000", {"t": "y"})]),
    }, rotates(tmp_path, "[::1]"))
    survivors = [o["origin"] for o in result.state["origins"]]
    assert survivors == ["http://[::2]:3000"]


# --- the defect the first real four-lane run found --------------------------
# These values were measured conflicting across four concurrent browsers reading LinkedIn
# and Google with no login action at all, on 2026-10-03. `li_at` and `JSESSIONID` were
# byte-identical in all four, so nothing had rotated - and the merge proposed evicting
# every origin, down to "0 origins". Writing it would have signed root.json out of
# LinkedIn as the direct result of a read-only run.
#
# The first fix was an allowlist of bot-management names that kept root's copy. It is
# gone: an allowlist fails open into a sign-out on every per-browser name it lacks. The
# same inputs now merge by recency, with zero evictions and one `resolved` line each.

LINKEDIN = ".linkedin.com"
GOOGLE = ".google.com"
PX_KEY = "PXdOjV695v_px-ff"


def measured_run():
    """The four-lane LinkedIn/Google run, reduced to the values that conflicted plus the
    session that must survive them. Lane n saved its file n seconds after T0."""
    root = state(
        cookies=[cookie("li_at", "SESSION", domain=LINKEDIN),
                 cookie("JSESSIONID", "ajax:1", domain=LINKEDIN),
                 cookie("__cf_bm", "root-mint", domain=LINKEDIN),
                 cookie("_px3", "root-mint", domain=LINKEDIN),
                 cookie("pxcts", "root-mint", domain=LINKEDIN),
                 cookie("__Secure-3PSIDCC", "root-mint", domain=GOOGLE)],
        origins=[origin("https://www.linkedin.com",
                        {"voyager": "payload", PX_KEY: "root"}),
                 origin("https://li.protechts.net", {PX_KEY: "root"}),
                 origin("https://accounts.google.com", {"k": "v"}),
                 origin("https://www.google.com", {"k": "v"})],
    )
    lanes = {
        f"lane-{n}": state(
            cookies=[cookie("li_at", "SESSION", domain=LINKEDIN),
                     cookie("JSESSIONID", "ajax:1", domain=LINKEDIN),
                     cookie("__cf_bm", f"browser-{n}", domain=LINKEDIN),
                     cookie("_px3", f"browser-{n}", domain=LINKEDIN),
                     cookie("pxcts", f"browser-{n}", domain=LINKEDIN),
                     cookie("__Secure-3PSIDCC", f"browser-{n}", domain=GOOGLE)],
            origins=[origin("https://www.linkedin.com",
                            {"voyager": "payload", PX_KEY: f"browser-{n}"}),
                     origin("https://li.protechts.net", {PX_KEY: f"browser-{n}"}),
                     origin("https://accounts.google.com", {"k": "v"}),
                     origin("https://www.google.com", {"k": "v"})],
        )
        for n in (1, 2, 3, 4)
    }
    return root, lanes, {f"lane-{n}": T0 + n for n in (1, 2, 3, 4)}


def test_the_measured_run_merges_with_no_eviction_and_says_what_it_picked():
    """Six values disagreed: four cookies and the PerimeterX key on two origins. Each is
    resolved to the last lane saved, and each pick is a line."""
    root, lanes, saved_at = measured_run()
    result = merge(root, lanes, saved_at=saved_at)

    assert result.evicted_origins == [], "a read-only run must not destroy the login"
    assert [o["origin"] for o in result.state["origins"]] == [
        "https://accounts.google.com", "https://li.protechts.net",
        "https://www.google.com", "https://www.linkedin.com"]
    survivors = {c["name"]: c["value"] for c in result.state["cookies"]}
    assert survivors["li_at"] == "SESSION"
    assert survivors["JSESSIONID"] == "ajax:1"
    for name in ("__cf_bm", "_px3", "pxcts", "__Secure-3PSIDCC"):
        assert survivors[name] == "browser-4", name
    assert len(result.resolved) == 6
    for line in result.resolved:
        assert "kept lane-4's value" in line
        assert line.endswith("over lane-1's, lane-2's, lane-3's")
    assert sorted(line.split(" kept ")[0] for line in result.resolved) == [
        "google.com cookie __Secure-3PSIDCC",
        "li.protechts.net localStorage[PXdOjV695v_px-ff]",
        "linkedin.com cookie __cf_bm",
        "linkedin.com cookie _px3",
        "linkedin.com cookie pxcts",
        "www.linkedin.com localStorage[PXdOjV695v_px-ff]",
    ]
    assert result.cookies_evicted == 0
    assert result.writable


def test_names_the_allowlist_never_had_merge_the_same_way():
    """The reason the allowlist went. Google's plain `SIDCC`, GA4's `_ga_<id>` and
    Datadog's `_dd_s` are per-browser too, were not on it, and would each have evicted a
    logged-in host. Case never mattered to recency, so `__CF_BM` is here as well."""
    for name in ("SIDCC", "_ga_ABC123XYZ", "_dd_s", "__CF_BM", "__Secure-3PSIDCC"):
        root = state(cookies=[cookie(name, "root"), cookie("sid", "keep")],
                     origins=[origin("https://www.a.example", {"k": "v"})])
        result = merge(root, {
            "lane-1": state(cookies=[cookie(name, "a"), cookie("sid", "keep")]),
            "lane-2": state(cookies=[cookie(name, "b"), cookie("sid", "keep")]),
        }, saved_at={"lane-1": T0, "lane-2": T0 + 1})
        assert result.evicted_origins == [], f"{name} must not evict"
        assert len(result.resolved) == 1, f"{name} must still be reported"
        values = {c["name"]: c["value"] for c in result.state["cookies"]}
        assert values == {name: "b", "sid": "keep"}, name


def test_a_real_session_cookie_resolves_too_unless_the_host_rotates(tmp_path):
    """A conflict on `li_at` is what the old rule existed for, and a value alone still
    cannot say whether it rotated. Without `rotates`, recency; with it, eviction."""
    root = state(cookies=[cookie("li_at", "SESSION", domain=LINKEDIN)],
                 origins=[origin("https://www.linkedin.com", {"k": "v"})])
    lanes = {
        "lane-1": state(cookies=[cookie("li_at", "ROTATED-A", domain=LINKEDIN,
                                        expires=1795000000)]),
        "lane-2": state(cookies=[cookie("li_at", "ROTATED-B", domain=LINKEDIN,
                                        expires=1794000000)]),
    }
    saved_at = {"lane-1": T0, "lane-2": T0 + 1}

    resolved = merge(root, lanes, saved_at=saved_at)
    assert resolved.evicted_origins == []
    assert [c["value"] for c in resolved.state["cookies"]] == ["ROTATED-A"]

    evicted = merge(root, lanes, rotates(tmp_path, "linkedin.com"), saved_at)
    assert evicted.evicted_origins == ["https://www.linkedin.com"]
    assert evicted.state["cookies"] == []


def test_an_eviction_notice_names_the_value_and_the_lanes(tmp_path):
    """The first run's dry run said only "two lanes changed the same value", which is
    what made a merge design bug look like token rotation. It must name the cookie.

    Also a regression on the lookup: the conflict is recorded against the cookie's host,
    `linkedin.com`, while the decision is reported against the origin's host,
    `www.linkedin.com`. An exact-key lookup found nothing and named no lane at all."""
    root = state(cookies=[cookie("li_at", "SESSION", domain=LINKEDIN)],
                 origins=[origin("https://www.linkedin.com", {"k": "v"})])
    result = merge(root, {
        "lane-1": state(cookies=[cookie("li_at", "A", domain=LINKEDIN)]),
        "lane-2": state(cookies=[cookie("li_at", "B", domain=LINKEDIN)]),
    }, rotates(tmp_path, "linkedin.com"))
    evicted = [d for d in result.decisions if d.action == "evicted"]
    assert len(evicted) == 1
    assert "cookie li_at" in evicted[0].reason
    assert evicted[0].changed_by == ["lane-1", "lane-2"]


def test_a_localstorage_eviction_names_the_key(tmp_path):
    root = state(origins=[origin("https://a.example", {"token": "0"})])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://a.example", {"token": "1"})]),
        "lane-2": state(origins=[origin("https://a.example", {"token": "2"})]),
    }, rotates(tmp_path))
    reason = next(d.reason for d in result.decisions if d.action == "evicted")
    assert "localStorage[token]" in reason


def test_an_expiry_only_refresh_is_counted_separately():
    """"1 added, 0 updated" across four real logged-in sessions read like the merge had
    done nothing. It had: the servers extended cookies without rotating them, which is a
    kept cookie with a later expiry. The report now says so."""
    root = state(cookies=[cookie("sid", "same", expires=1759000000)])
    result = merge(root, {
        "lane-1": state(cookies=[cookie("sid", "same", expires=1760000000)]),
    })
    assert result.cookies_updated == 0
    assert result.cookies_kept == 1
    assert result.cookies_refreshed == 1
    assert result.state["cookies"][0]["expires"] == 1760000000


def test_an_unchanged_cookie_is_not_counted_as_refreshed():
    root = state(cookies=[cookie("sid", "same", expires=1759000000)])
    result = merge(root, {"lane-1": state(cookies=[cookie("sid", "same")])})
    assert result.cookies_refreshed == 0


def test_a_cookie_absent_from_root_that_two_lanes_minted_is_added_once():
    """With the allowlist, a bot-management cookie absent from root was left absent
    rather than crowning a lane. Recency crowns one and says which, counted as one add."""
    result = merge(state(), {
        "lane-1": state(cookies=[cookie("__cf_bm", "a")]),
        "lane-2": state(cookies=[cookie("__cf_bm", "b")]),
    }, saved_at={"lane-1": T0 + 1, "lane-2": T0})
    assert [c["value"] for c in result.state["cookies"]] == ["a"]
    assert result.cookies_added == 1
    assert result.evicted_origins == []
    assert len(result.resolved) == 1


def test_a_perimeterx_storage_key_does_not_evict_the_site_it_sits_on():
    """Measured: with the cookie rule in place, `li.protechts.net` was still evicted over
    `localStorage[PXdOjV695v_px-ff]`. The case that matters is the identical key under an
    origin somebody is logged in to, and it must leave that origin's real keys alone."""
    root = state(
        cookies=[cookie("li_at", "SESSION", domain=LINKEDIN)],
        origins=[origin("https://www.linkedin.com",
                        {PX_KEY: "root", "voyager:badges": "3"})],
    )
    result = merge(root, {
        "lane-1": state(origins=[origin("https://www.linkedin.com", {PX_KEY: "a"})]),
        "lane-2": state(origins=[origin("https://www.linkedin.com", {PX_KEY: "b"})]),
    }, saved_at={"lane-1": T0, "lane-2": T0 + 1})
    assert result.evicted_origins == []
    survivors = {i["name"]: i["value"]
                 for i in result.state["origins"][0]["localStorage"]}
    assert survivors[PX_KEY] == "b", "the lane saved last"
    assert survivors["voyager:badges"] == "3", "the real key must be untouched"
    assert [c["value"] for c in result.state["cookies"]] == ["SESSION"]
    assert result.resolved == [(
        "www.linkedin.com localStorage[PXdOjV695v_px-ff] kept lane-2's value "
        "(saved 2026-09-21T14:13:21Z) over lane-1's")]


def test_a_real_storage_token_on_a_rotates_host_still_evicts(tmp_path):
    root = state(origins=[origin("https://a.example", {"refresh_token": "0"})])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://a.example", {"refresh_token": "1"})]),
        "lane-2": state(origins=[origin("https://a.example", {"refresh_token": "2"})]),
    }, rotates(tmp_path))
    assert result.evicted_origins == ["https://a.example"]


# --- partitioned cookies: the first real merge lost 737 rows -----------------
def partitioned(name, value, site, domain=".a.example"):
    c = cookie(name, value, domain=domain)
    c["partitionKey"] = site
    return c


def test_two_partitions_of_one_cookie_both_survive():
    """Measured on the first merge ever written to a real root.json: 3903 cookie rows in,
    3167 out, and "0 evicted" in the report. 36 names appeared in several CHIPS partitions
    each and all but one copy of each was dropped. Under
    `Set-Cookie: ...; Partitioned` a cookie is scoped to the top-level site it was set
    under, so (name, domain, path) is not an identity."""
    root = state(cookies=[
        partitioned("__Secure-ROLLOUT_TOKEN", "x", "https://youtube.com"),
        partitioned("__Secure-ROLLOUT_TOKEN", "y", "https://google.com"),
    ])
    result = merge(root, {"lane-1": state()})
    assert result.cookie_rows == 2
    assert {c["partitionKey"] for c in result.state["cookies"]} == {
        "https://youtube.com", "https://google.com"}
    assert result.lost_cookies == []


def test_a_partitioned_and_an_unpartitioned_cookie_are_different_cookies():
    root = state(cookies=[cookie("sid", "plain"),
                          partitioned("sid", "scoped", "https://b.example")])
    result = merge(root, {"lane-1": state()})
    assert result.cookie_rows == 2
    assert result.cookies_kept == 2


def test_the_two_encodings_of_one_partition_key_are_not_two_cookies():
    """Playwright has carried partitionKey as a bare string and as an object, and Chrome
    adds `_crHasCrossSiteAncestor`. Canonicalising the dict stops two spellings of one
    partition from looking like a duplicate and tripping the lost-cookie invariant."""
    a = cookie("sid", "v")
    a["partitionKey"] = {"topLevelSite": "https://b.example",
                         "hasCrossSiteAncestor": False}
    b = cookie("sid", "v")
    b["partitionKey"] = {"hasCrossSiteAncestor": False,
                         "topLevelSite": "https://b.example"}
    from autoweb.merge import cookie_key
    assert cookie_key(a) == cookie_key(b)


def test_a_partitioned_cookie_a_lane_changed_updates_only_its_own_partition():
    root = state(cookies=[partitioned("t", "old", "https://x.example"),
                          partitioned("t", "old", "https://y.example")])
    result = merge(root, {
        "lane-1": state(cookies=[partitioned("t", "new", "https://x.example")]),
    })
    by_partition = {c["partitionKey"]: c["value"] for c in result.state["cookies"]}
    assert by_partition == {"https://x.example": "new", "https://y.example": "old"}
    assert result.cookies_updated == 1


def test_a_merge_that_would_lose_a_cookie_refuses_to_write(tmp_path, monkeypatch):
    """The invariant itself. It can only fire on a bug in merge.py, so it is provoked by
    making the result drop a cookie after the fact - a test of the guard, not of a path
    that exists. A silent loss is worse than a refusal: the lost root.json verified fine
    and looked healthy."""
    root = state(cookies=[cookie("sid", "keep"), cookie("other", "keep")])
    result = merge(root, {"lane-1": state()})
    assert result.writable and result.lost_cookies == []

    result.state["cookies"] = [c for c in result.state["cookies"]
                               if c["name"] != "other"]
    result.lost_cookies = ["other on .a.example/"]
    assert not result.writable
    with pytest.raises(MergeError, match="neither in the merged state nor counted"):
        write_root(tmp_path / "root.json", result)
    assert not (tmp_path / "root.json").exists()


def test_an_evicted_cookie_is_not_reported_as_lost(tmp_path):
    """Eviction is accounted for, so the invariant must not fire on it."""
    root = state(cookies=[cookie("li_at", "SESSION", domain=".linkedin.com")],
                 origins=[origin("https://www.linkedin.com", {"k": "v"})])
    result = merge(root, {
        "lane-1": state(cookies=[cookie("li_at", "A", domain=".linkedin.com")]),
        "lane-2": state(cookies=[cookie("li_at", "B", domain=".linkedin.com")]),
    }, rotates(tmp_path, "linkedin.com"))
    assert result.cookies_evicted == 1
    assert result.lost_cookies == []
    assert result.writable


# --- the cross-site-ancestor bit is half of the partition key ---------------
def test_two_rows_differing_only_in_the_ancestor_bit_both_survive():
    """Measured on the re-merge: two `tag_user_id` rows on `.trueclassictees.com`, one
    partition site, the bit True on one and False on the other, identical values, and
    only one survived. Chromium's CookiePartitionKey is the pair (top-level site,
    has-cross-site-ancestor), so these are two cookies and Chrome exported both.

    The previous key canonicalised an *object* partitionKey with sorted keys, which kept
    the bit, and ignored the sibling `_crHasCrossSiteAncestor` that arrives alongside a
    string one. So the encoding decided whether half the identity counted."""
    first = partitioned("tag_user_id", "same", "https://t.example")
    first["_crHasCrossSiteAncestor"] = False
    second = partitioned("tag_user_id", "same", "https://t.example")
    second["_crHasCrossSiteAncestor"] = True

    result = merge(state(cookies=[first, second]), {"lane-1": state()})
    assert result.lost_cookies == []
    assert result.cookie_rows == 2
    assert {c["_crHasCrossSiteAncestor"] for c in result.state["cookies"]} == {
        True, False}


def test_the_object_and_string_encodings_of_one_partition_still_agree():
    """Both halves must normalise to the same pair, or the fix for the collapse would
    manufacture a split instead."""
    from autoweb.merge import cookie_key
    as_object = cookie("sid", "v")
    as_object["partitionKey"] = {"topLevelSite": "https://b.example",
                                 "hasCrossSiteAncestor": True}
    as_string = cookie("sid", "v")
    as_string["partitionKey"] = "https://b.example"
    as_string["_crHasCrossSiteAncestor"] = True
    assert cookie_key(as_object) == cookie_key(as_string)


def test_an_absent_ancestor_bit_means_true_not_a_third_value():
    """Playwright's own semantics, read from the playwright-core bundles of 1.63 and of
    1.64, the version the lanes' MCP resolves: it writes the field for every partitioned
    cookie on export and reads it as
    `_crHasCrossSiteAncestor ?? true` on import.

    This was briefly a third value on the reasoning that splitting one cookie into two is
    the recoverable direction. It is not, here: absent and true land in the same browser
    partition on seed, so one overwrites the other and the merge would report two
    surviving identities where the browser keeps one. An invariant that disagrees with the
    browser is worse than none, because the browser is what it exists to check."""
    from autoweb.merge import cookie_key
    without = partitioned("sid", "v", "https://b.example")
    with_true = partitioned("sid", "v", "https://b.example")
    with_true["_crHasCrossSiteAncestor"] = True
    with_false = partitioned("sid", "v", "https://b.example")
    with_false["_crHasCrossSiteAncestor"] = False
    assert cookie_key(without) == cookie_key(with_true)
    assert cookie_key(without) != cookie_key(with_false)


def test_an_absent_bit_does_not_split_a_row_into_two_identities():
    """The failure the third value would have caused: two rows the browser would collapse
    counted as two surviving cookies by the merge."""
    without = partitioned("sid", "v", "https://b.example")
    with_true = partitioned("sid", "v", "https://b.example")
    with_true["_crHasCrossSiteAncestor"] = True
    result = merge(state(cookies=[without, with_true]), {"lane-1": state()})
    assert result.cookie_rows == 1
    # One row in two spellings is one cookie, so the invariant must see a real loss here
    # and refuse rather than claim both were kept.
    assert len(result.lost_cookies) == 1
    assert not result.writable


def test_a_key_that_arrives_twice_refuses_the_write(tmp_path):
    """The invariant counts rows rather than testing set membership. Two input rows this
    module cannot tell apart must refuse rather than silently keep one: that is the only
    shape the second CHIPS defect had, and membership could not see it because the
    surviving row's key was present in the output.

    It also fired by coincidence the first time. Rows in equalled rows out because one
    row collapsed and one unrelated cookie was added, so a total-only check balanced."""
    same = cookie("ambiguous", "v")
    root = state(cookies=[dict(same), dict(same)])
    result = merge(root, {"lane-1": state()})
    assert result.cookie_rows == 1
    assert len(result.lost_cookies) == 1
    assert "2 row(s) in, 1 out" in result.lost_cookies[0]
    assert not result.writable
    with pytest.raises(MergeError, match="cannot tell them apart"):
        write_root(tmp_path / "root.json", result)
    assert not (tmp_path / "root.json").exists()


def test_two_partitions_of_one_cookie_resolve_separately():
    """Recency must pick per cookie identity, partition included. Picking per name would
    put one partition's winner into the other and reopen the CHIPS collapse."""
    root = state(cookies=[partitioned("t", "old", "https://x.example"),
                          partitioned("t", "old", "https://y.example")])
    result = merge(root, {
        "lane-1": state(cookies=[partitioned("t", "x1", "https://x.example"),
                                 partitioned("t", "y1", "https://y.example")]),
        "lane-2": state(cookies=[partitioned("t", "x2", "https://x.example"),
                                 partitioned("t", "y2", "https://y.example")]),
    }, saved_at={"lane-1": T0 + 1, "lane-2": T0})
    by_partition = {c["partitionKey"]: c["value"] for c in result.state["cookies"]}
    assert by_partition == {"https://x.example": "x1", "https://y.example": "y1"}
    assert result.cookie_rows == 2 and result.lost_cookies == []
    assert len(result.resolved) == 2
    assert "[partition https://x.example|1]" in result.resolved[0]

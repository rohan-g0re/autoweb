"""Every test here is a defect that actually existed in `autoweb/merge.py`.

An independent review of the first version found three ways to produce a `root.json` that
was plausible, passed every test then present, and was wrong. All three were silent, and
the silence is what made them dangerous: a crash is visible, a half-identity is not.

They are kept in their own file so that nobody tidying up the main suite deletes one for
looking redundant. None of them is redundant. Each describes a path by which a real
person's logged-in sessions were destroyed or, worse, a retired OAuth refresh token was
written back where presenting it could revoke the whole family.
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


# --- the three criticals ----------------------------------------------------


def test_two_lanes_with_different_indexeddb_evicts_rather_than_last_writer_wins():
    """IndexedDB had no conflict detection at all, so the last contributor silently won,
    in the one store where OAuth refresh tokens actually live."""
    root = state(origins=[origin("https://www.a.example", {},
                                 idb=[{"name": "fb", "version": 1, "stores": []}])])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://www.a.example", {},
                                        idb=[{"name": "fb", "version": 2,
                                              "stores": [{"name": "A"}]}])]),
        "lane-2": state(origins=[origin("https://www.a.example", {},
                                        idb=[{"name": "fb", "version": 3,
                                              "stores": [{"name": "B"}]}])]),
    })
    assert result.state["origins"] == []
    assert "https://www.a.example" in result.evicted_origins


def test_an_unchanged_lane_cannot_revert_a_lane_that_refreshed_indexeddb():
    """The nastiest of the three.

    IndexedDB was compared against the running merged value instead of the ancestor, so a
    lane that merely opened the site and changed nothing overwrote a lane that had
    genuinely refreshed its token. That writes back the credential the server has already
    rotated away, and names the innocent lane as the one that changed it. The outcome also
    flipped with the order of the filenames on the command line.
    """
    stale = [{"name": "fb", "version": 1, "stores": [{"name": "old"}]}]
    fresh = [{"name": "fb", "version": 2, "stores": [{"name": "new"}]}]
    root = state(origins=[origin("https://www.a.example", {}, idb=stale)])
    lanes = {
        "lane-1": state(origins=[origin("https://www.a.example", {}, idb=fresh)]),
        "lane-2": state(origins=[origin("https://www.a.example", {}, idb=stale)]),
    }
    result = merge(root, lanes)
    assert result.state["origins"][0]["indexedDB"] == fresh, "the stale lane won"
    # And the answer must be a function of the inputs, not of their order.
    assert merge(root, dict(reversed(list(lanes.items())))).state == result.state


def test_a_local_storage_conflict_evicts_that_hosts_cookies_too():
    """Conflicts were detected in two passes with eviction in between, so a localStorage
    conflict found in the second pass had nothing left to act on. The site's cookie jar
    survived a decision that the site was unsafe to keep."""
    root = state([cookie("li_at", "V1")],
                 [origin("https://www.a.example", {"token": "T0"})])
    result = merge(root, {
        "lane-1": state([cookie("li_at", "V1")],
                        [origin("https://www.a.example", {"token": "T1"})]),
        "lane-2": state([cookie("li_at", "V1")],
                        [origin("https://www.a.example", {"token": "T2"})]),
    })
    assert result.state["origins"] == []
    assert result.state["cookies"] == [], "the cookie jar outlived its own origin"


def test_a_domain_scoped_cookie_conflict_evicts_the_hosts_origins():
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
    })
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
    `rule_for` and `sticky` could not protect it."""
    cfg = config_at(tmp_path, '[origins."auth.example"]\nsticky = true\n')
    result = merge(state(), {
        "lane-1": state([cookie("sid", "A", "auth.example")]),
        "lane-2": state([cookie("sid", "B", "auth.example")]),
    }, cfg)
    assert result.state["cookies"], "sticky did not protect a lane-only host"
    assert result.evicted_origins == []


def test_a_rotating_cookie_only_site_is_still_reported(tmp_path):
    """The rotating-site check counted lane origins only. A session living purely in
    cookies has no entry in `origins` at all, and that is exactly the kind of site whose
    token family gets revoked."""
    cfg = config_at(tmp_path, '[origins."a.example"]\nrotates = true\n')
    result = merge(state(), {
        "lane-1": state([cookie("sid", "A")]),
        "lane-2": state([cookie("sid", "A")]),
    }, cfg)
    assert result.rotating_violations
    assert "a.example" in result.rotating_violations[0]


def test_a_cookie_only_conflict_appears_in_the_report():
    """A conflicted host with no stored origin produced no decision at all, so cookies
    vanished and the per-origin report showed nothing. The audit trail is the only human
    check standing between this and a real identity."""
    result = merge(state([cookie("sid", "old", "cookies-only.example")]), {
        "lane-1": state([cookie("sid", "x", "cookies-only.example")]),
        "lane-2": state([cookie("sid", "y", "cookies-only.example")]),
    })
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


def test_an_ipv6_origin_does_not_collapse_onto_every_other_one():
    """merge had its own host normaliser that returned "[" for any IPv6 literal, so a
    conflict on one IPv6 origin evicted them all. It now uses the one in config."""
    root = state(origins=[origin("http://[::1]:3000", {"t": "0"}),
                          origin("http://[::2]:3000", {"t": "0"})])
    result = merge(root, {
        "lane-1": state(origins=[origin("http://[::1]:3000", {"t": "x"})]),
        "lane-2": state(origins=[origin("http://[::1]:3000", {"t": "y"})]),
    })
    survivors = [o["origin"] for o in result.state["origins"]]
    assert survivors == ["http://[::2]:3000"]


# --- the defect the first real four-lane run found --------------------------
# These four cookies were measured conflicting across four concurrent browsers reading
# LinkedIn and Google with no login action at all, on 2026-10-03. `li_at` and
# `JSESSIONID` were byte-identical in all four, so nothing had rotated - and the merge
# proposed evicting every origin, down to "0 origins". Writing it would have signed
# root.json out of LinkedIn as the direct result of a read-only run.

def test_bot_management_cookies_do_not_evict_a_logged_in_site():
    """The reported case, reduced: four lanes, four different `__cf_bm` values, an
    untouched session cookie, and a stored origin that must survive."""
    root = state(
        cookies=[cookie("li_at", "SESSION", domain=".linkedin.com"),
                 cookie("__cf_bm", "root-mint", domain=".linkedin.com")],
        origins=[origin("https://www.linkedin.com", {"voyager": "payload"})],
    )
    lanes = {
        f"lane-{n}": state(
            cookies=[cookie("li_at", "SESSION", domain=".linkedin.com"),
                     cookie("__cf_bm", f"browser-{n}", domain=".linkedin.com")],
            origins=[origin("https://www.linkedin.com", {"voyager": "payload"})],
        )
        for n in (1, 2, 3, 4)
    }
    result = merge(root, lanes)

    assert result.evicted_origins == [], "a read-only run must not destroy the login"
    assert [o["origin"] for o in result.state["origins"]] == ["https://www.linkedin.com"]
    survivors = {c["name"]: c["value"] for c in result.state["cookies"]}
    assert survivors["li_at"] == "SESSION"
    # Root's copy, not any lane's: a lane's value is that browser's fingerprint, and
    # importing it into the shared identity is what these cookies exist to prevent.
    assert survivors["__cf_bm"] == "root-mint"
    assert result.volatile_conflicts == {"linkedin.com": ["__cf_bm"]}


def test_the_other_measured_names_are_covered_whatever_their_case():
    """`__Secure-3PSIDCC` arrives from Chrome in that spelling; `_px3` and `pxcts` are
    PerimeterX. All three conflicted on the same run."""
    for name in ("__Secure-3PSIDCC", "_px3", "pxcts", "__CF_BM"):
        root = state(cookies=[cookie(name, "root"), cookie("sid", "keep")])
        result = merge(root, {
            "lane-1": state(cookies=[cookie(name, "a"), cookie("sid", "keep")]),
            "lane-2": state(cookies=[cookie(name, "b"), cookie("sid", "keep")]),
        })
        assert result.evicted_origins == [], f"{name} must not evict"
        assert result.volatile_conflicts, f"{name} must still be reported"


def test_a_real_session_cookie_still_evicts():
    """The safety rule is narrowed, not removed. A conflict on anything not on the list
    is still treated as a rotated credential, because it may be one."""
    root = state(cookies=[cookie("li_at", "SESSION", domain=".linkedin.com")],
                 origins=[origin("https://www.linkedin.com", {"k": "v"})])
    result = merge(root, {
        "lane-1": state(cookies=[cookie("li_at", "ROTATED-A", domain=".linkedin.com")]),
        "lane-2": state(cookies=[cookie("li_at", "ROTATED-B", domain=".linkedin.com")]),
    })
    assert result.evicted_origins == ["https://www.linkedin.com"]
    assert result.state["cookies"] == []


def test_jsessionid_is_not_treated_as_volatile():
    """Deliberate, and worth a test so nobody adds it for looking per-connection.
    LinkedIn uses JSESSIONID as its CSRF token, paired with `li_at`."""
    root = state(cookies=[cookie("JSESSIONID", "ajax:1", domain=".linkedin.com")],
                 origins=[origin("https://www.linkedin.com", {"k": "v"})])
    result = merge(root, {
        "lane-1": state(cookies=[cookie("JSESSIONID", "ajax:2", domain=".linkedin.com")]),
        "lane-2": state(cookies=[cookie("JSESSIONID", "ajax:3", domain=".linkedin.com")]),
    })
    assert result.evicted_origins == ["https://www.linkedin.com"]


def test_an_eviction_notice_names_the_value_and_the_lanes():
    """The first run's dry run said only "two lanes changed the same value", which is
    what made a merge design bug look like token rotation. It must name the cookie.

    Also a regression on the lookup: the conflict is recorded against the cookie's host,
    `linkedin.com`, while the decision is reported against the origin's host,
    `www.linkedin.com`. An exact-key lookup found nothing and named no lane at all."""
    root = state(cookies=[cookie("li_at", "SESSION", domain=".linkedin.com")],
                 origins=[origin("https://www.linkedin.com", {"k": "v"})])
    result = merge(root, {
        "lane-1": state(cookies=[cookie("li_at", "A", domain=".linkedin.com")]),
        "lane-2": state(cookies=[cookie("li_at", "B", domain=".linkedin.com")]),
    })
    evicted = [d for d in result.decisions if d.action == "evicted"]
    assert len(evicted) == 1
    assert "cookie li_at" in evicted[0].reason
    assert evicted[0].changed_by == ["lane-1", "lane-2"]


def test_a_localstorage_eviction_names_the_key():
    root = state(origins=[origin("https://a.example", {"token": "0"})])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://a.example", {"token": "1"})]),
        "lane-2": state(origins=[origin("https://a.example", {"token": "2"})]),
    })
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


def test_a_volatile_cookie_absent_from_root_is_not_invented():
    """With no ancestor there is no stale-but-neutral copy to keep, and crowning a lane
    would write one browser's fingerprint into the shared identity."""
    result = merge(state(), {
        "lane-1": state(cookies=[cookie("__cf_bm", "a")]),
        "lane-2": state(cookies=[cookie("__cf_bm", "b")]),
    })
    assert result.state["cookies"] == []
    assert result.cookies_added == 0
    assert result.evicted_origins == []


def test_a_perimeterx_storage_key_does_not_evict_the_site_it_sits_on():
    """Measured: with the cookie rule in place, `li.protechts.net` was still evicted over
    `localStorage[PXdOjV695v_px-ff]`. Exempting the host would have been the easy fix and
    the wrong one - nobody has an account on protechts.net, and the case that matters is
    the identical key under an origin somebody is logged in to."""
    root = state(
        cookies=[cookie("li_at", "SESSION", domain=".linkedin.com")],
        origins=[origin("https://www.linkedin.com",
                        {"PXdOjV695v_px-ff": "root", "voyager:badges": "3"})],
    )
    result = merge(root, {
        "lane-1": state(origins=[origin("https://www.linkedin.com",
                                        {"PXdOjV695v_px-ff": "a"})]),
        "lane-2": state(origins=[origin("https://www.linkedin.com",
                                        {"PXdOjV695v_px-ff": "b"})]),
    })
    assert result.evicted_origins == []
    survivors = {i["name"]: i["value"]
                 for i in result.state["origins"][0]["localStorage"]}
    assert survivors["PXdOjV695v_px-ff"] == "root", "root's copy, not a lane's"
    assert survivors["voyager:badges"] == "3", "the real key must be untouched"
    assert result.volatile_conflicts == {
        "www.linkedin.com": ["localStorage[PXdOjV695v_px-ff]"]}


def test_a_real_storage_token_still_evicts():
    root = state(origins=[origin("https://a.example", {"refresh_token": "0"})])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://a.example", {"refresh_token": "1"})]),
        "lane-2": state(origins=[origin("https://a.example", {"refresh_token": "2"})]),
    })
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


def test_an_evicted_cookie_is_not_reported_as_lost():
    """Eviction is accounted for, so the invariant must not fire on it."""
    root = state(cookies=[cookie("li_at", "SESSION", domain=".linkedin.com")],
                 origins=[origin("https://www.linkedin.com", {"k": "v"})])
    result = merge(root, {
        "lane-1": state(cookies=[cookie("li_at", "A", domain=".linkedin.com")]),
        "lane-2": state(cookies=[cookie("li_at", "B", domain=".linkedin.com")]),
    })
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


def test_an_absent_ancestor_bit_is_its_own_value():
    """Not False. Whether Playwright omits the field when it is False has not been
    checked, and guessing re-introduces the collapse for the rows hardest to notice.
    Splitting one cookie into two is the recoverable direction; merging two into one is
    the silent data loss."""
    from autoweb.merge import cookie_key
    without = partitioned("sid", "v", "https://b.example")
    with_false = partitioned("sid", "v", "https://b.example")
    with_false["_crHasCrossSiteAncestor"] = False
    assert cookie_key(without) != cookie_key(with_false)


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

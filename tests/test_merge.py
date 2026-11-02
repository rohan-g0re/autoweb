"""The merge decides what happens to a real person's logged-in sessions.

So these tests are weighted towards what must NOT happen. A merge that loses a login
costs a manual re-login, and the old rule - evict a host on any conflict - paid that cost
on every multi-lane run, because lanes are separate browsers and disagree about values
the browser mints for itself. A conflict now keeps the most recently issued value and
says so. Eviction survives for a host marked `rotates = true`, where the user has said a
forced re-login is the price they want.

Every test states the damage it prevents, because in six months the rules will look
over-cautious and somebody will be tempted to simplify them.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

from autoweb import cli
from autoweb.config import Config
from autoweb.merge import MergeError, merge, merge_files, write_root


def cookie(name, value, domain=".a.example", path="/", expires=1759000000):
    """A realistically shaped cookie.

    Domain-scoped with a leading dot, and carrying the attributes a real Playwright
    export carries. Both details matter: a review found two defects that were invisible
    because every fixture used a bare apex domain (where a cookie's `domain` and an
    origin's host are the same string) and none carried `expires`.
    """
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


ROTATES = '[origins."a.example"]\nrotates = true\n'
T0 = 1790000000.0          # a lane file's save time; later lanes add seconds to it


def iso(seconds):
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the clean cases --------------------------------------------------------


def test_disjoint_lanes_union_cleanly():
    """Four lanes on four sites is the normal case and must simply work."""
    root = state([cookie("sid", "0")], [origin("https://www.a.example")])
    result = merge(root, {
        "lane-1": state([cookie("sid", "0"), cookie("t", "1", ".b.example")],
                        [origin("https://b.example", {"k": "1"})]),
        "lane-2": state([cookie("sid", "0"), cookie("t", "2", ".c.example")],
                        [origin("https://c.example", {"k": "2"})]),
    })
    names = [o["origin"] for o in result.state["origins"]]
    assert names == ["https://b.example", "https://c.example", "https://www.a.example"]
    assert result.cookies_added == 2
    assert result.evicted_origins == []


def test_an_untouched_origin_survives_untouched():
    """A lane that never visited a site says nothing about it."""
    root = state([cookie("sid", "keep")],
                 [origin("https://www.a.example", {"token": "keep"})])
    result = merge(root, {"lane-1": state([], [])})
    assert result.state["cookies"] == [cookie("sid", "keep")]
    assert result.state["origins"][0]["localStorage"] == [{"name": "token",
                                                           "value": "keep"}]


def test_one_lane_changing_a_value_wins():
    root = state([cookie("sid", "old")])
    result = merge(root, {"lane-1": state([cookie("sid", "new")])})
    assert result.state["cookies"][0]["value"] == "new"
    assert result.cookies_updated == 1


def test_two_lanes_agreeing_is_not_a_conflict():
    """Both lanes saw the same rotation. Identical values are one fact, not two."""
    root = state([cookie("sid", "old")])
    result = merge(root, {"lane-1": state([cookie("sid", "new")]),
                          "lane-2": state([cookie("sid", "new")])})
    assert result.state["cookies"][0]["value"] == "new"
    assert result.evicted_origins == []


# --- what must not happen --------------------------------------------------


def test_a_deletion_is_never_propagated():
    """The damage prevented: every run quietly eroding the identity. A lane that lacks a
    cookie usually never visited that site, rather than having logged out of it."""
    root = state([cookie("sid", "keep"),
                  cookie("other", "keep", ".b.example")])
    result = merge(root, {"lane-1": state([cookie("sid", "keep")])})
    values = {(c["name"], c["value"]) for c in result.state["cookies"]}
    assert ("other", "keep") in values


def test_a_conflict_keeps_one_value_instead_of_evicting_the_host():
    """The old rule evicted here, and on a real run that signed the identity out over
    values no server had rotated. A value alone cannot say which kind it is, so the
    host stays and the most recent value is kept."""
    root = state([cookie("sid", "old")],
                 [origin("https://www.a.example", {"t": "old"})])
    result = merge(root, {
        "lane-1": state([cookie("sid", "rotated-by-1")]),
        "lane-2": state([cookie("sid", "rotated-by-2")]),
    }, saved_at={"lane-1": T0, "lane-2": T0 + 5})
    assert result.evicted_origins == []
    assert [c["value"] for c in result.state["cookies"]] == ["rotated-by-2"]
    assert [o["origin"] for o in result.state["origins"]] == ["https://www.a.example"]
    assert len(result.resolved) == 1


def test_the_higher_expiry_wins_whatever_the_save_order():
    """A server that keeps only its latest token issued that token last, and the latest
    issue carries the latest expiry. Expiry outranks save time, so a lane that saved
    late but holds the older token does not win."""
    root = state([cookie("sid", "old", expires=1759000000)])
    result = merge(root, {
        "lane-1": state([cookie("sid", "newer", expires=1795000000)]),
        "lane-2": state([cookie("sid", "older", expires=1794000000)]),
    }, saved_at={"lane-1": T0, "lane-2": T0 + 60})
    assert result.state["cookies"][0]["value"] == "newer"
    assert result.state["cookies"][0]["expires"] == 1795000000
    assert result.resolved == [(
        f"a.example cookie sid kept lane-1's value (expires {iso(1795000000)} / "
        f"saved {iso(T0)}) over lane-2's")]


def test_equal_expiries_fall_back_to_the_later_save():
    root = state([cookie("sid", "old")])
    result = merge(root, {
        "lane-1": state([cookie("sid", "first")]),
        "lane-2": state([cookie("sid", "second")]),
    }, saved_at={"lane-1": T0 + 9, "lane-2": T0})
    assert result.state["cookies"][0]["value"] == "first"
    assert "kept lane-1's value" in result.resolved[0]
    assert "over lane-2's" in result.resolved[0]


def test_session_cookies_fall_back_to_the_later_save():
    """`expires: -1` is a session cookie: no issue time to compare, so the save time
    decides, and the report says `session` rather than printing 1969."""
    root = state([cookie("sid", "old", expires=-1)])
    result = merge(root, {
        "lane-1": state([cookie("sid", "a", expires=-1)]),
        "lane-2": state([cookie("sid", "b", expires=-1)]),
    }, saved_at={"lane-1": T0, "lane-2": T0 + 1})
    assert result.state["cookies"][0]["value"] == "b"
    assert result.resolved == [(
        f"a.example cookie sid kept lane-2's value (expires session / "
        f"saved {iso(T0 + 1)}) over lane-1's")]


def test_a_dated_cookie_outranks_a_session_cookie():
    root = state([cookie("sid", "old")])
    result = merge(root, {
        "lane-1": state([cookie("sid", "dated", expires=1795000000)]),
        "lane-2": state([cookie("sid", "session", expires=-1)]),
    }, saved_at={"lane-1": T0, "lane-2": T0 + 60})
    assert result.state["cookies"][0]["value"] == "dated"


def test_a_full_tie_is_decided_by_lane_name_not_argument_order():
    root = state([cookie("sid", "old")])
    lanes = {"lane-2": state([cookie("sid", "two")]),
             "lane-1": state([cookie("sid", "one")])}
    result = merge(root, lanes)
    assert result.state["cookies"][0]["value"] == "one"
    assert "saved unknown" in result.resolved[0]
    assert merge(root, dict(reversed(list(lanes.items())))).state == result.state


def test_a_rotates_host_still_evicts_on_conflict(tmp_path):
    """The declared exception. The user has said this site uses reuse detection and
    wants the re-login forced now, so a conflict drops the host rather than picking."""
    cfg = config_at(tmp_path, ROTATES)
    root = state([cookie("sid", "old")],
                 [origin("https://www.a.example", {"t": "old"})])
    result = merge(root, {
        "lane-1": state([cookie("sid", "rotated-by-1")]),
        "lane-2": state([cookie("sid", "rotated-by-2")]),
    }, cfg, saved_at={"lane-1": T0, "lane-2": T0 + 5})
    assert result.state["cookies"] == []
    assert result.state["origins"] == []
    assert result.evicted_origins == ["https://www.a.example"]
    assert result.resolved == []
    assert "rotates = true" in result.decisions[0].reason


def test_eviction_takes_the_whole_origin_not_just_the_conflicting_cookie(tmp_path):
    """Half a session is not a session. Leaving the other cookies behind would produce a
    browser that looks signed in and is not, which is harder to diagnose than being out."""
    root = state([cookie("sid", "old"),
                  cookie("csrf", "same")],
                 [origin("https://www.a.example", {"t": "old"})])
    result = merge(root, {
        "lane-1": state([cookie("sid", "x"),
                         cookie("csrf", "same")]),
        "lane-2": state([cookie("sid", "y"),
                         cookie("csrf", "same")]),
    }, config_at(tmp_path, ROTATES))
    assert result.state["cookies"] == []


def test_a_conflict_on_one_site_does_not_touch_another(tmp_path):
    """Eviction is per host. A blast radius bigger than the conflict would make the
    merge worse than not merging."""
    root = state([cookie("sid", "old"), cookie("sid", "fine", ".b.example")])
    result = merge(root, {
        "lane-1": state([cookie("sid", "x")]),
        "lane-2": state([cookie("sid", "y")]),
    }, config_at(tmp_path, ROTATES))
    survivors = {(c["domain"], c["value"]) for c in result.state["cookies"]}
    assert survivors == {(".b.example", "fine")}


def test_an_empty_indexeddb_never_overwrites_a_real_one():
    """`indexedDB: []` means "nobody harvested it" as often as it means "it is empty",
    and the two are indistinguishable in the file. Treating it as empty would destroy
    Firebase, Supabase and Auth0 logins on every merge, which is where most real session
    state lives."""
    root = state(origins=[origin("https://www.a.example", {},
                                 idb=[{"name": "firebase", "version": 1,
                                       "stores": [{"name": "tokens"}]}])])
    result = merge(root, {"lane-1": state(origins=[origin("https://www.a.example", {},
                                                          idb=[])])})
    assert result.state["origins"][0]["indexedDB"][0]["name"] == "firebase"


def test_a_real_indexeddb_change_does_propagate():
    root = state(origins=[origin("https://www.a.example", {}, idb=[])])
    fresh = [{"name": "firebase", "version": 2, "stores": [{"name": "tokens"}]}]
    result = merge(root, {"lane-1": state(origins=[origin("https://www.a.example", {},
                                                          idb=fresh)])})
    assert result.state["origins"][0]["indexedDB"] == fresh


def test_conflicting_local_storage_resolves_by_save_time():
    """localStorage has no expiry, so the lane that saved last wins, and says so."""
    root = state(origins=[origin("https://www.a.example", {"token": "old"})])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://www.a.example", {"token": "x"})]),
        "lane-2": state(origins=[origin("https://www.a.example", {"token": "y"})]),
    }, saved_at={"lane-1": T0 + 3, "lane-2": T0})
    assert result.state["origins"][0]["localStorage"] == [{"name": "token",
                                                           "value": "x"}]
    assert result.resolved == [(
        f"www.a.example localStorage[token] kept lane-1's value (saved {iso(T0 + 3)}) "
        f"over lane-2's")]


def test_conflicting_local_storage_on_a_rotates_host_evicts(tmp_path):
    """Tokens live in localStorage at least as often as in cookies, so the declared
    exception has to apply there or it is half a protection."""
    root = state(origins=[origin("https://www.a.example", {"token": "old"})])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://www.a.example", {"token": "x"})]),
        "lane-2": state(origins=[origin("https://www.a.example", {"token": "y"})]),
    }, config_at(tmp_path, ROTATES))
    assert result.state["origins"] == []
    assert "https://www.a.example" in result.evicted_origins


# --- IndexedDB, per database and per store ----------------------------------


def db(name, *stores, version=1):
    return {"name": name, "version": version,
            "stores": [{"name": s, "records": [{"key": "k", "value": v}]}
                       for s, v in stores]}


def test_two_lanes_changing_two_databases_both_survive():
    """One blob per origin made these two a conflict, though neither lane touched the
    other's database. Compared per database, both changes land and nothing is resolved."""
    root = state(origins=[origin("https://www.a.example", {},
                                 idb=[db("auth", ("tokens", "0")),
                                      db("cache", ("pages", "0"))])])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://www.a.example", {},
                                        idb=[db("auth", ("tokens", "1")),
                                             db("cache", ("pages", "0"))])]),
        "lane-2": state(origins=[origin("https://www.a.example", {},
                                        idb=[db("auth", ("tokens", "0")),
                                             db("cache", ("pages", "2"))])]),
    })
    assert result.state["origins"][0]["indexedDB"] == [db("auth", ("tokens", "1")),
                                                       db("cache", ("pages", "2"))]
    assert result.resolved == []
    assert result.decisions[0].changed_by == ["lane-1", "lane-2"]


def test_one_store_changed_by_two_lanes_resolves_by_save_time():
    root = state(origins=[origin("https://www.a.example", {},
                                 idb=[db("auth", ("tokens", "0"), ("prefs", "0"))])])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://www.a.example", {},
                                        idb=[db("auth", ("tokens", "1"),
                                                ("prefs", "0"))])]),
        "lane-2": state(origins=[origin("https://www.a.example", {},
                                        idb=[db("auth", ("tokens", "2"),
                                                ("prefs", "0"))])]),
    }, saved_at={"lane-1": T0, "lane-2": T0 + 7})
    assert result.state["origins"][0]["indexedDB"] == [
        db("auth", ("tokens", "2"), ("prefs", "0"))]
    assert result.resolved == [(
        f"www.a.example indexedDB[auth/tokens] kept lane-2's value "
        f"(saved {iso(T0 + 7)}) over lane-1's")]


def test_a_store_a_lane_did_not_export_is_not_deleted():
    """Never propagate a deletion, one level down: a lane whose export lacks a store says
    nothing about it."""
    root = state(origins=[origin("https://www.a.example", {},
                                 idb=[db("auth", ("tokens", "0"), ("prefs", "0"))])])
    result = merge(root, {"lane-1": state(origins=[origin(
        "https://www.a.example", {}, idb=[db("auth", ("tokens", "1"))])])})
    assert result.state["origins"][0]["indexedDB"] == [
        db("auth", ("tokens", "1"), ("prefs", "0"))]


# --- config-driven behaviour ----------------------------------------------


def test_sticky_wins_over_rotates(tmp_path):
    """Some apps keep cryptographic keys client-side, where eviction does not log you out
    but unpairs the device. The operator gets to say that is worse than the risk, and
    sticky only matters on a rotates host now, since nothing else is ever evicted."""
    cfg = config_at(tmp_path, '[origins."a.example"]\nsticky = true\nrotates = true\n')
    root = state([cookie("sid", "old")],
                 [origin("https://www.a.example", {"t": "old"})])
    result = merge(root, {
        "lane-1": state([cookie("sid", "x")]),
        "lane-2": state([cookie("sid", "y")]),
    }, cfg, saved_at={"lane-1": T0, "lane-2": T0 + 1})
    assert result.evicted_origins == []
    assert [c["value"] for c in result.state["cookies"]] == ["y"]
    assert len(result.resolved) == 1


def test_a_rotating_site_two_lanes_agree_on_is_merged_without_a_warning(tmp_path):
    """The old "touched by N lanes" warning fired on every multi-lane run, because an
    isolated lane exports every cookie root gave it. No conflict, nothing to say."""
    cfg = config_at(tmp_path, ROTATES)
    root = state([cookie("sid", "old")], [origin("https://www.a.example")])
    result = merge(root, {
        "lane-1": state([cookie("sid", "old")],
                        [origin("https://www.a.example", {"k": "1"})]),
        "lane-2": state([cookie("sid", "old")],
                        [origin("https://www.a.example", {"k": "1"})]),
    }, cfg)
    assert result.evicted_origins == []
    assert result.resolved == []
    assert not hasattr(result, "rotating_violations")


def test_one_lane_on_a_rotating_site_is_fine(tmp_path):
    cfg = config_at(tmp_path, ROTATES)
    root = state(origins=[origin("https://www.a.example")])
    result = merge(root, {"lane-1": state(origins=[origin("https://www.a.example",
                                                           {"k": "1"})])}, cfg)
    assert result.evicted_origins == []
    assert result.state["origins"][0]["localStorage"] == [{"name": "k", "value": "1"}]


# --- caps refuse rather than truncate --------------------------------------


def test_exceeding_the_byte_cap_is_reported_and_blocks_the_write(tmp_path):
    """Truncating to fit would make which login survives depend on argument order. But
    raising from inside the merge meant --dry-run failed identically to a real run, so an
    operator who hit a cap could not see the decisions. Report, then refuse the write."""
    cfg = config_at(tmp_path, "[caps]\ntotal_bytes = 200\n")
    result = merge(state(), {"lane-1": state(origins=[origin("https://a.example",
                                                             {"k": "x" * 500})])}, cfg)
    assert result.cap_violations and "total_bytes" in result.cap_violations[0]
    assert not result.writable
    assert result.decisions, "the operator must still be able to read the decisions"
    with pytest.raises(MergeError, match="refusing to write"):
        write_root(tmp_path / "root.json", result)
    assert not (tmp_path / "root.json").exists()


def test_exceeding_the_origin_cap_is_reported(tmp_path):
    cfg = config_at(tmp_path, "[caps]\nmax_origins = 2\n")
    result = merge(state(), {"lane-1": state(origins=[origin(f"https://s{n}.example")
                                                     for n in range(5)])}, cfg)
    assert any("max_origins" in v for v in result.cap_violations)


def test_exceeding_the_indexeddb_store_cap_is_reported(tmp_path):
    cfg = config_at(tmp_path, "[caps]\nmax_indexeddb_per_origin = 2\n")
    idb = [{"name": "db", "version": 1,
            "stores": [{"name": f"s{n}"} for n in range(6)]}]
    result = merge(state(), {"lane-1": state(origins=[origin("https://a.example", {},
                                                             idb=idb)])}, cfg)
    assert any("max_indexeddb_per_origin" in v for v in result.cap_violations)


# --- files and the write ---------------------------------------------------


def test_merging_no_lanes_is_an_error(tmp_path):
    (tmp_path / "root.json").write_text(json.dumps(state()), encoding="utf-8")
    with pytest.raises(MergeError, match="no lane files"):
        merge_files(tmp_path / "root.json", [])


def test_a_malformed_lane_fails_the_whole_merge(tmp_path):
    """Every lane is read before the root is touched, so a bad file cannot leave a
    half-applied identity behind."""
    (tmp_path / "root.json").write_text(json.dumps(state()), encoding="utf-8")
    bad = tmp_path / "lane-1.json"
    bad.write_text('{"not": "a storage state"}', encoding="utf-8")
    with pytest.raises(MergeError):
        merge_files(tmp_path / "root.json", [bad])


def test_the_previous_root_is_kept_as_a_backup(tmp_path):
    """For the failure the atomic write cannot catch: a merge that was valid and wrong."""
    root_path = tmp_path / "root.json"
    before = state([cookie("sid", "before")])
    root_path.write_text(json.dumps(before), encoding="utf-8")
    lane = tmp_path / "lane-1.json"
    lane.write_text(json.dumps(state([cookie("sid", "after")])), encoding="utf-8")

    result = merge_files(root_path, [lane])
    write_root(root_path, result)

    assert json.loads(root_path.read_text())["cookies"][0]["value"] == "after"
    assert json.loads((tmp_path / "root.json.bak").read_text()) == before


def test_a_lane_file_is_named_in_the_decisions(tmp_path):
    """When two lanes conflict, which two is the thing the operator needs."""
    root_path = tmp_path / "root.json"
    root_path.write_text(json.dumps(state()), encoding="utf-8")
    lane = tmp_path / "lane-7.json"
    lane.write_text(json.dumps(state(origins=[origin("https://www.a.example",
                                                      {"k": "1"})])), encoding="utf-8")
    result = merge_files(root_path, [lane])
    assert any("lane-7" in d.changed_by for d in result.decisions)


def test_merge_files_ranks_lanes_by_when_their_file_was_saved(tmp_path):
    """The save time comes from the lane file's mtime, so the pure function gets it as
    an argument and the file loader is what reads it."""
    root_path = tmp_path / "root.json"
    root_path.write_text(json.dumps(state([cookie("sid", "old")])), encoding="utf-8")
    early, late = tmp_path / "lane-1.json", tmp_path / "lane-2.json"
    late.write_text(json.dumps(state([cookie("sid", "late")])), encoding="utf-8")
    early.write_text(json.dumps(state([cookie("sid", "early")])), encoding="utf-8")
    os.utime(early, (T0, T0))
    os.utime(late, (T0 + 30, T0 + 30))
    result = merge_files(root_path, [early, late])
    assert result.state["cookies"][0]["value"] == "late"
    assert f"saved {iso(T0 + 30)}" in result.resolved[0]


def test_the_cli_prints_every_resolved_line(tmp_path, capsys):
    (tmp_path / "autoweb.toml").write_text("", encoding="utf-8")
    (tmp_path / "root.json").write_text(json.dumps(state(
        [cookie("sid", "old")], [origin("https://www.a.example", {"t": "0"})])),
        encoding="utf-8")
    paths = []
    for n in (1, 2):
        lane = tmp_path / f"lane-{n}.json"
        lane.write_text(json.dumps(state(
            [cookie("sid", f"v{n}")], [origin("https://www.a.example", {"t": str(n)})])),
            encoding="utf-8")
        os.utime(lane, (T0 + n, T0 + n))
        paths.append(str(lane))
    assert cli.main(["-C", str(tmp_path), "merge", "--dry-run", *paths]) == 0
    out = capsys.readouterr().out
    assert (f"  resolved: a.example cookie sid kept lane-2's value "
            f"(expires {iso(1759000000)} / saved {iso(T0 + 2)}) over lane-1's") in out
    assert (f"  resolved: www.a.example localStorage[t] kept lane-2's value "
            f"(saved {iso(T0 + 2)}) over lane-1's") in out
    assert "need a fresh login" not in out


def test_the_cli_still_lists_a_rotates_eviction_as_needing_a_login(tmp_path, capsys):
    (tmp_path / "autoweb.toml").write_text(ROTATES, encoding="utf-8")
    (tmp_path / "root.json").write_text(json.dumps(state(
        [cookie("sid", "old")], [origin("https://www.a.example")])), encoding="utf-8")
    paths = []
    for n in (1, 2):
        lane = tmp_path / f"lane-{n}.json"
        lane.write_text(json.dumps(state([cookie("sid", f"v{n}")])), encoding="utf-8")
        paths.append(str(lane))
    assert cli.main(["-C", str(tmp_path), "merge", "--dry-run", *paths]) == 0
    captured = capsys.readouterr()
    assert "these need a fresh login:\n    https://www.a.example" in captured.out
    assert "resolved:" not in captured.out
    assert "warning:" not in captured.out + captured.err

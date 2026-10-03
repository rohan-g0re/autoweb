"""The merge decides what happens to a real person's logged-in sessions.

So these tests are weighted towards what must NOT happen. A merge that loses a login
costs a manual re-login; a merge that writes back a rotated refresh token can get a whole
token family revoked and sign the user out everywhere, which is the expensive failure and
the one the eviction rule exists for.

Every test states the damage it prevents, because in six months the rules will look
over-cautious and somebody will be tempted to simplify them.
"""

from __future__ import annotations

import json

import pytest

from autoweb.config import Config
from autoweb.merge import MergeError, merge, merge_files, write_root


def cookie(name, value, domain="example.com", path="/"):
    return {"name": name, "value": value, "domain": domain, "path": path}


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


# --- the clean cases --------------------------------------------------------


def test_disjoint_lanes_union_cleanly():
    """Four lanes on four sites is the normal case and must simply work."""
    root = state([cookie("sid", "0", "a.example")], [origin("https://a.example")])
    result = merge(root, {
        "lane-1": state([cookie("sid", "0", "a.example"), cookie("t", "1", "b.example")],
                        [origin("https://b.example", {"k": "1"})]),
        "lane-2": state([cookie("sid", "0", "a.example"), cookie("t", "2", "c.example")],
                        [origin("https://c.example", {"k": "2"})]),
    })
    names = [o["origin"] for o in result.state["origins"]]
    assert names == ["https://a.example", "https://b.example", "https://c.example"]
    assert result.cookies_added == 2
    assert result.evicted_origins == []


def test_an_untouched_origin_survives_untouched():
    """A lane that never visited a site says nothing about it."""
    root = state([cookie("sid", "keep", "a.example")],
                 [origin("https://a.example", {"token": "keep"})])
    result = merge(root, {"lane-1": state([], [])})
    assert result.state["cookies"] == [cookie("sid", "keep", "a.example")]
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
    root = state([cookie("sid", "keep", "a.example"),
                  cookie("other", "keep", "b.example")])
    result = merge(root, {"lane-1": state([cookie("sid", "keep", "a.example")])})
    values = {(c["name"], c["value"]) for c in result.state["cookies"]}
    assert ("other", "keep") in values


def test_a_conflict_evicts_the_origin_instead_of_picking_a_winner():
    """The expensive failure. One of the two values is a refresh token the server has
    already rotated away, and presenting it can revoke the whole family."""
    root = state([cookie("sid", "old", "a.example")],
                 [origin("https://a.example", {"t": "old"})])
    result = merge(root, {
        "lane-1": state([cookie("sid", "rotated-by-1", "a.example")]),
        "lane-2": state([cookie("sid", "rotated-by-2", "a.example")]),
    })
    assert result.state["cookies"] == []
    assert result.state["origins"] == []
    assert "https://a.example" in result.evicted_origins


def test_eviction_takes_the_whole_origin_not_just_the_conflicting_cookie():
    """Half a session is not a session. Leaving the other cookies behind would produce a
    browser that looks signed in and is not, which is harder to diagnose than being out."""
    root = state([cookie("sid", "old", "a.example"),
                  cookie("csrf", "same", "a.example")],
                 [origin("https://a.example", {"t": "old"})])
    result = merge(root, {
        "lane-1": state([cookie("sid", "x", "a.example"),
                         cookie("csrf", "same", "a.example")]),
        "lane-2": state([cookie("sid", "y", "a.example"),
                         cookie("csrf", "same", "a.example")]),
    })
    assert result.state["cookies"] == []


def test_a_conflict_on_one_site_does_not_touch_another():
    """Eviction is per origin. A blast radius bigger than the conflict would make the
    merge worse than not merging."""
    root = state([cookie("sid", "old", "a.example"), cookie("sid", "fine", "b.example")])
    result = merge(root, {
        "lane-1": state([cookie("sid", "x", "a.example")]),
        "lane-2": state([cookie("sid", "y", "a.example")]),
    })
    survivors = {(c["domain"], c["value"]) for c in result.state["cookies"]}
    assert survivors == {("b.example", "fine")}


def test_an_empty_indexeddb_never_overwrites_a_real_one():
    """`indexedDB: []` means "nobody harvested it" as often as it means "it is empty",
    and the two are indistinguishable in the file. Treating it as empty would destroy
    Firebase, Supabase and Auth0 logins on every merge, which is where most real session
    state lives."""
    root = state(origins=[origin("https://a.example", {},
                                 idb=[{"name": "firebase", "version": 1,
                                       "stores": [{"name": "tokens"}]}])])
    result = merge(root, {"lane-1": state(origins=[origin("https://a.example", {},
                                                          idb=[])])})
    assert result.state["origins"][0]["indexedDB"][0]["name"] == "firebase"


def test_a_real_indexeddb_change_does_propagate():
    root = state(origins=[origin("https://a.example", {}, idb=[])])
    fresh = [{"name": "firebase", "version": 2, "stores": [{"name": "tokens"}]}]
    result = merge(root, {"lane-1": state(origins=[origin("https://a.example", {},
                                                          idb=fresh)])})
    assert result.state["origins"][0]["indexedDB"] == fresh


def test_conflicting_local_storage_also_evicts():
    """Tokens live in localStorage at least as often as in cookies, so the same rule has
    to apply there or the protection is half a protection."""
    root = state(origins=[origin("https://a.example", {"token": "old"})])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://a.example", {"token": "x"})]),
        "lane-2": state(origins=[origin("https://a.example", {"token": "y"})]),
    })
    assert result.state["origins"] == []
    assert "https://a.example" in result.evicted_origins


# --- config-driven behaviour ----------------------------------------------


def test_a_sticky_origin_is_not_evicted(tmp_path):
    """Some apps keep cryptographic keys client-side, where eviction does not log you out
    but unpairs the device. The operator gets to say that is worse than the risk."""
    cfg = config_at(tmp_path, '[origins."a.example"]\nsticky = true\n')
    root = state([cookie("sid", "old", "a.example")],
                 [origin("https://a.example", {"t": "old"})])
    result = merge(root, {
        "lane-1": state([cookie("sid", "x", "a.example")]),
        "lane-2": state([cookie("sid", "y", "a.example")]),
    }, cfg)
    assert result.evicted_origins == []
    assert result.state["cookies"], "a sticky origin kept nothing"


def test_a_rotating_site_touched_by_two_lanes_is_reported(tmp_path):
    """Not fatal, but it must never pass silently: this is the arrangement that gets a
    token family revoked, and the operator asked to be told by marking it."""
    cfg = config_at(tmp_path, '[origins."a.example"]\nrotates = true\n')
    root = state(origins=[origin("https://a.example")])
    result = merge(root, {
        "lane-1": state(origins=[origin("https://a.example", {"k": "1"})]),
        "lane-2": state(origins=[origin("https://a.example", {"k": "1"})]),
    }, cfg)
    assert result.rotating_violations
    assert "a.example" in result.rotating_violations[0]
    assert "lane-1" in result.rotating_violations[0]


def test_one_lane_on_a_rotating_site_is_fine(tmp_path):
    cfg = config_at(tmp_path, '[origins."a.example"]\nrotates = true\n')
    root = state(origins=[origin("https://a.example")])
    result = merge(root, {"lane-1": state(origins=[origin("https://a.example",
                                                           {"k": "1"})])}, cfg)
    assert result.rotating_violations == []


# --- caps refuse rather than truncate --------------------------------------


def test_exceeding_the_byte_cap_refuses_to_write(tmp_path):
    """Truncating to fit would make which login survives depend on dictionary ordering,
    and that shows up later as one site being logged out on Tuesdays."""
    cfg = config_at(tmp_path, "[caps]\ntotal_bytes = 200\n")
    root = state()
    big = {"lane-1": state(origins=[origin("https://a.example",
                                           {"k": "x" * 500})])}
    with pytest.raises(MergeError, match="total_bytes"):
        merge(root, big, cfg)


def test_exceeding_the_origin_cap_refuses_to_write(tmp_path):
    cfg = config_at(tmp_path, "[caps]\nmax_origins = 2\n")
    root = state()
    lanes = {"lane-1": state(origins=[origin(f"https://s{n}.example")
                                      for n in range(5)])}
    with pytest.raises(MergeError, match="max_origins"):
        merge(root, lanes, cfg)


def test_exceeding_the_indexeddb_store_cap_refuses_to_write(tmp_path):
    cfg = config_at(tmp_path, "[caps]\nmax_indexeddb_per_origin = 2\n")
    root = state()
    idb = [{"name": "db", "version": 1,
            "stores": [{"name": f"s{n}"} for n in range(6)]}]
    lanes = {"lane-1": state(origins=[origin("https://a.example", {}, idb=idb)])}
    with pytest.raises(MergeError, match="max_indexeddb_per_origin"):
        merge(root, lanes, cfg)


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
    lane.write_text(json.dumps(state(origins=[origin("https://a.example",
                                                      {"k": "1"})])), encoding="utf-8")
    result = merge_files(root_path, [lane])
    assert any("lane-7" in d.changed_by for d in result.decisions)

"""State file tests.

Browser-driven paths (``export_interactive``, ``seeded_context_check``) are not tested
here: a test that launches Chrome and asserts "still logged in" is an integration test
against somebody else's website, and it belongs in the Fable gate, not in a unit suite
that has to pass offline. What *is* tested here is everything that can silently corrupt
or misreport an identity file.
"""

from __future__ import annotations

import json

import pytest

from autoweb.state import StateError, load, save, summarise

# A storageState with every shape that matters: a cookie-only host, an origin with
# localStorage, and an origin with IndexedDB.
SAMPLE = {
    "cookies": [
        {"name": "sid", "value": "x", "domain": ".example.com", "path": "/"},
        {"name": "other", "value": "y", "domain": "example.com", "path": "/"},
        {"name": "only", "value": "z", "domain": "cookies-only.test", "path": "/"},
    ],
    "origins": [
        {"origin": "https://example.com",
         "localStorage": [{"name": "k", "value": "v"}]},
        {"origin": "https://idb.test",
         "localStorage": [],
         "indexedDB": [{"name": "firebaseLocalStorageDb", "stores": []}]},
        {"origin": "https://empty.test", "localStorage": []},
    ],
}


def write_state(tmp_path, data):
    path = tmp_path / "root.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- loading ----------------------------------------------------------------


def test_missing_file_says_how_to_make_one(tmp_path):
    """The error should point at the fix, not just report absence."""
    with pytest.raises(StateError, match="autoweb state export"):
        load(tmp_path / "root.json")


def test_invalid_json_names_the_file(tmp_path):
    path = tmp_path / "root.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError, match="not valid JSON"):
        load(path)


def test_non_object_rejected(tmp_path):
    with pytest.raises(StateError, match="storageState object"):
        load(write_state(tmp_path, ["cookies"]))


def test_wrong_shape_is_called_out(tmp_path):
    """A JSON file that is not storageState should say so, not fail later."""
    with pytest.raises(StateError, match="does not look like"):
        load(write_state(tmp_path, {"cookies": "nope", "origins": []}))


def test_bom_tolerated(tmp_path):
    path = tmp_path / "root.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(SAMPLE).encode())
    assert len(load(path)["cookies"]) == 3


# --- saving -----------------------------------------------------------------


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "root.json"
    save(path, SAMPLE)
    assert load(path) == SAMPLE


def test_save_keeps_previous_as_backup(tmp_path):
    """Identity costs a human typing passwords. Never destroy the old one silently."""
    path = tmp_path / "root.json"
    save(path, {"cookies": [{"name": "first"}], "origins": []})
    save(path, {"cookies": [{"name": "second"}], "origins": []})

    assert load(path)["cookies"][0]["name"] == "second"
    backup = json.loads(path.with_suffix(".json.bak").read_text())
    assert backup["cookies"][0]["name"] == "first"


def test_save_leaves_no_temp_file(tmp_path):
    path = tmp_path / "root.json"
    save(path, SAMPLE)
    assert not path.with_suffix(".json.tmp").exists()


def test_save_failure_leaves_no_debris(tmp_path, monkeypatch):
    path = tmp_path / "root.json"

    def boom(self, target):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.replace", boom)
    with pytest.raises(StateError, match="cannot write"):
        save(path, SAMPLE)
    assert not path.with_suffix(".json.tmp").exists()


def test_save_creates_missing_directories(tmp_path):
    path = tmp_path / "deep" / "nested" / "root.json"
    save(path, SAMPLE)
    assert path.is_file()


# --- measuring --------------------------------------------------------------


def test_summary_counts_cookies_and_origins(tmp_path):
    summary = summarise(write_state(tmp_path, SAMPLE))
    assert summary.cookies == 3
    assert summary.total_bytes > 0


def test_cookie_only_hosts_are_listed(tmp_path):
    """A host with cookies and no storage never appears in `origins`, but it is often
    the entire session. Omitting it would make the origin count a lie."""
    summary = summarise(write_state(tmp_path, SAMPLE))
    assert any(o.origin == "cookies-only.test" for o in summary.origins)


def test_leading_dot_domains_match_their_origin(tmp_path):
    """`.example.com` and `example.com` are the same host for counting purposes."""
    summary = summarise(write_state(tmp_path, SAMPLE))
    example = next(o for o in summary.origins if o.origin == "https://example.com")
    assert example.cookies == 2


def test_indexeddb_stores_are_counted(tmp_path):
    summary = summarise(write_state(tmp_path, SAMPLE))
    idb = next(o for o in summary.origins if o.origin == "https://idb.test")
    assert idb.indexeddb_stores == 1


def test_empty_origin_does_not_carry_a_session(tmp_path):
    summary = summarise(write_state(tmp_path, SAMPLE))
    empty = next(o for o in summary.origins if o.origin == "https://empty.test")
    assert empty.carries_session is False
    assert empty not in summary.origins_with_session


def test_origins_with_session_excludes_noise(tmp_path):
    summary = summarise(write_state(tmp_path, SAMPLE))
    assert len(summary.origins) == 4            # 3 origins + 1 cookie-only host
    assert len(summary.origins_with_session) == 3


def test_empty_state_measures_cleanly(tmp_path):
    summary = summarise(write_state(tmp_path, {"cookies": [], "origins": []}))
    assert summary.cookies == 0
    assert summary.origins == ()


def test_malformed_entries_are_skipped_not_fatal(tmp_path):
    """A junk entry should not stop you inspecting the rest of a real file."""
    data = {"cookies": ["not-a-dict", {"name": "a", "domain": "x.test"}],
            "origins": ["not-a-dict", {"origin": "https://x.test"}]}
    summary = summarise(write_state(tmp_path, data))
    assert summary.cookies == 1
    assert len(summary.origins) == 1

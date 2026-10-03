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


# --- regressions from the Phase 2 Fable gate --------------------------------


def test_duplicate_origins_on_one_host_collapse(tmp_path):
    """http and https on one site is one identity, not two.

    Counting them separately inflated the max_origins cap and gave every row the
    host's full cookie count, so per-row totals exceeded the actual total.
    """
    data = {
        "cookies": [{"name": "a", "value": "1", "domain": "dup.test", "path": "/"}],
        "origins": [
            {"origin": "https://dup.test", "localStorage": [{"name": "k", "value": "v"}]},
            {"origin": "http://dup.test", "localStorage": []},
            {"origin": "https://dup.test:8443", "localStorage": []},
        ],
    }
    summary = summarise(write_state(tmp_path, data))
    assert len(summary.origins) == 1
    assert sum(o.cookies for o in summary.origins) == summary.cookies


def test_ipv6_origin_is_parsed_not_mangled(tmp_path):
    """`http://[::1]:8080` used to parse to the host `[`, orphaning its cookie."""
    data = {
        "cookies": [{"name": "a", "value": "1", "domain": "[::1]", "path": "/"}],
        "origins": [{"origin": "http://[::1]:8080", "localStorage": []}],
    }
    summary = summarise(write_state(tmp_path, data))
    assert len(summary.origins) == 1
    assert summary.origins[0].cookies == 1


def test_cookie_with_non_string_domain_is_ignored(tmp_path):
    """A null domain became an origin literally named 'none'."""
    data = {"cookies": [{"name": "a", "value": "1", "domain": None, "path": "/"}],
            "origins": []}
    summary = summarise(write_state(tmp_path, data))
    assert summary.origins == ()


def test_origin_entry_without_an_origin_is_ignored(tmp_path):
    """A missing `origin` key rendered as a blank row."""
    summary = summarise(write_state(tmp_path, {"cookies": [], "origins": [{"ls": []}]}))
    assert summary.origins == ()


def test_total_bytes_is_the_file_size(tmp_path):
    """caps.total_bytes is documented as the size of root.json, so measure the file.

    It previously reported compact-JSON length, which is smaller than the indented
    file actually written - the cap and the label described different numbers.
    """
    path = tmp_path / "root.json"
    save(path, SAMPLE)
    assert summarise(path).total_bytes == path.stat().st_size


def test_seeded_check_rejects_a_non_state_file_before_launching(tmp_path):
    """`verify` accepted files that `inspect` rejected, then failed inside Playwright.

    The two commands must agree on what a state file is, and the rejection must not
    cost a browser launch.
    """
    from autoweb.state import seeded_context_check

    path = tmp_path / "root.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(StateError, match="storageState object"):
        seeded_context_check(path, "https://example.com")


def test_export_without_a_terminal_explains_itself(tmp_path, monkeypatch):
    """Non-interactive stdin raised EOFError from input() with a traceback.

    There is no human to log in, so there is nothing to capture; say that.
    """
    from autoweb.state import export_interactive

    class FakePage:
        url = "https://example.com"
        def goto(self, *a, **k): return None
        def title(self): return "x"

    class FakeContext:
        def new_page(self): return FakePage()
        def set_default_timeout(self, *a): pass
        def close(self): pass

    class FakeBrowser:
        def new_context(self, **k): return FakeContext()
        def close(self): pass

    class FakePW:
        class chromium:
            @staticmethod
            def launch(**k): return FakeBrowser()

    class FakeSync:
        def __enter__(self): return FakePW()
        def __exit__(self, *a): return False

    monkeypatch.setattr("autoweb.state._playwright", lambda: (lambda: FakeSync()))

    def eof(_prompt):
        raise EOFError

    with pytest.raises(StateError, match="interactive terminal"):
        export_interactive(tmp_path / "root.json", "https://example.com",
                           wait_for_enter=eof)

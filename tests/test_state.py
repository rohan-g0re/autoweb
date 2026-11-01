"""State file tests.

Browser-driven paths (``export_interactive``, ``seeded_context_check``) are not tested
here: a test that launches Chrome and asserts "still logged in" is an integration test
against somebody else's website, and it belongs in the Fable gate, not in a unit suite
that has to pass offline. What *is* tested here is everything that can silently corrupt
or misreport an identity file.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from autoweb.state import StateError, git_ignore_status, load, save, summarise

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


# --- regressions from the Phase 2 re-gate -----------------------------------


def test_unwritable_destination_is_caught_before_the_browser_opens(tmp_path):
    """A login that cannot be saved is a login the human has to do again.

    Previously `export --out` whose parent was a file raised FileExistsError from
    `save()` *after* the human had logged in, losing the session.
    """
    from autoweb.state import ensure_writable

    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(StateError, match="is a file, not a directory"):
        ensure_writable(blocker / "root.json")


def test_ensure_writable_accepts_a_path_needing_new_directories(tmp_path):
    from autoweb.state import ensure_writable

    ensure_writable(tmp_path / "a" / "b" / "root.json")
    assert (tmp_path / "a" / "b").is_dir()


def test_ensure_writable_rejects_a_directory_in_the_files_place(tmp_path):
    from autoweb.state import ensure_writable

    target = tmp_path / "root.json"
    target.mkdir()
    with pytest.raises(StateError, match="not a file"):
        ensure_writable(target)


def test_seed_result_carries_body_text():
    """`--expect-text` matched the title only, so a genuinely logged-in page failed.

    The test site serves the title 'The Internet' on both its login page and its
    secure area; only the body says which one you are on.
    """
    from autoweb.state import SeedResult

    result = SeedResult(title="The Internet", final_url="https://x/secure",
                        status=200, body_text="Welcome to the Secure Area.")
    haystack = (result.title + "\n" + result.body_text).lower()
    assert "secure area" in haystack


def test_a_json_object_that_is_not_a_storage_state_is_rejected(tmp_path):
    """`raw.get(key, [])` used to let any JSON object through, so an unrelated file was
    reported as a successful export of "0 cookies, 0 origins". Playwright always writes
    both keys, so a file missing either one is not a storageState."""
    path = tmp_path / "root.json"
    for body in ('{"hello": "world"}', '{"cookies": []}', '{"origins": []}', "{}"):
        path.write_text(body, encoding="utf-8")
        with pytest.raises(StateError, match="not a Playwright storageState"):
            load(path)


def test_an_empty_but_real_storage_state_is_accepted(tmp_path):
    """A fresh profile with nothing in it is a legitimate export, and rejecting it would
    make `state export` fail on the one case a user is most likely to hit first."""
    path = tmp_path / "root.json"
    path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    assert load(path) == {"cookies": [], "origins": []}


# --- would git commit the identity? ------------------------------------------
#
# Against a real git, in a real repository, because the thing under test is what git
# says. A fake git would only test that the code believes its own assumptions.

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")


@pytest.fixture
def hermetic_git(tmp_path, monkeypatch):
    """No global or system git config, and no repository discovery above tmp_path.

    A developer's global excludes file could list root.json and turn "unignored" into
    "ignored"; a temp directory inside some checkout would turn "not-a-repo" into a
    repository. Both would make these tests about the machine rather than the code.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return tmp_path


def git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run([GIT, "init", "-q", str(path)], check=True, capture_output=True)
    return path


@needs_git
def test_an_ignored_identity_is_reported_ignored(hermetic_git):
    repo = git_repo(hermetic_git / "repo")
    (repo / ".gitignore").write_text("root.json\n", encoding="utf-8")
    save(repo / "root.json", SAMPLE)
    assert git_ignore_status(repo / "root.json") == "ignored"


@needs_git
def test_info_exclude_counts_as_ignored(hermetic_git):
    """`aw-setup --init-project` may write its rules to .git/info/exclude."""
    repo = git_repo(hermetic_git / "repo")
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("root.json\n", encoding="utf-8")
    save(repo / "root.json", SAMPLE)
    assert git_ignore_status(repo / "root.json") == "ignored"


@needs_git
def test_an_unignored_identity_is_reported_unignored(hermetic_git):
    """The case the check exists for: setup never ran, and `git add .` would take it."""
    repo = git_repo(hermetic_git / "repo")
    save(repo / "root.json", SAMPLE)
    assert git_ignore_status(repo / "root.json") == "unignored"


@needs_git
def test_a_tracked_identity_is_reported_tracked_even_when_a_rule_matches(hermetic_git):
    """An ignore rule does nothing for a file already in the index, so a tracked file
    that a rule matches is still committed on the next change. Tracked wins."""
    repo = git_repo(hermetic_git / "repo")
    save(repo / "root.json", SAMPLE)
    subprocess.run([GIT, "-C", str(repo), "add", "root.json"], check=True,
                   capture_output=True)
    (repo / ".gitignore").write_text("root.json\n", encoding="utf-8")
    assert git_ignore_status(repo / "root.json") == "tracked"


@needs_git
def test_a_file_in_a_subdirectory_of_a_repo_is_judged_by_that_repo(hermetic_git):
    repo = git_repo(hermetic_git / "repo")
    (repo / ".gitignore").write_text("/state/root.json\n", encoding="utf-8")
    save(repo / "state" / "root.json", SAMPLE)
    save(repo / "other" / "root.json", SAMPLE)
    assert git_ignore_status(repo / "state" / "root.json") == "ignored"
    assert git_ignore_status(repo / "other" / "root.json") == "unignored"


@needs_git
def test_outside_a_repository_is_not_a_repo(hermetic_git):
    plain = hermetic_git / "plain"
    save(plain / "root.json", SAMPLE)
    assert git_ignore_status(plain / "root.json") == "not-a-repo"


@needs_git
def test_a_git_dir_in_the_environment_does_not_redirect_the_answer(hermetic_git,
                                                                  monkeypatch):
    """GIT_DIR from a git hook or an editor overrides `-C`. Without stripping it the
    answer would be about some other repository."""
    elsewhere = git_repo(hermetic_git / "elsewhere")
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    plain = hermetic_git / "plain"
    save(plain / "root.json", SAMPLE)
    assert git_ignore_status(plain / "root.json") == "not-a-repo"


def test_no_git_executable_is_no_git(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    save(tmp_path / "root.json", SAMPLE)
    assert git_ignore_status(tmp_path / "root.json") == "no-git"


def test_a_git_that_cannot_be_run_is_no_git(tmp_path):
    save(tmp_path / "root.json", SAMPLE)
    assert git_ignore_status(tmp_path / "root.json",
                             git=str(tmp_path / "no-such-git")) == "no-git"


def test_a_git_that_hangs_is_unknown_not_a_hang(tmp_path, monkeypatch):
    """The budget covers every call together; a stuck git gives up, it never raises."""
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))
    monkeypatch.setattr(subprocess, "run", hang)
    save(tmp_path / "root.json", SAMPLE)
    assert git_ignore_status(tmp_path / "root.json", git="git") == "unknown"


def test_a_spent_budget_runs_nothing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    save(tmp_path / "root.json", SAMPLE)
    assert git_ignore_status(tmp_path / "root.json", git="git", timeout=0) == "unknown"
    assert calls == []

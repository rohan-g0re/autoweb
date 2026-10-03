"""The CLI's contract is its exit codes.

Anything that reads `autoweb` from a script or a CI job reads the exit code, not the
prose, so that is what these tests pin. `state verify` gets the most attention because
it is the only command meant to be used as a gate, and a gate that cannot fail is worse
than no gate: an earlier version printed the page title and exited 0 unconditionally,
while the site under test served the same title on its login page and its secure page.

Nothing here opens a browser. The one command that would is reached through a stub, so
the suite stays offline and runs in under two seconds.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from autoweb import cli, state


@pytest.fixture
def project(tmp_path):
    """A minimal project directory, with no autoweb.toml so defaults apply."""
    (tmp_path / "autoweb.toml").write_text("", encoding="utf-8")
    return tmp_path


def run(argv, project=None):
    """Invoke the CLI the way a shell does, returning its exit code."""
    if project is not None:
        argv = ["-C", str(project), *argv]
    return cli.main(argv)


def write_state(path, origins=None, cookies=None):
    path.write_text(json.dumps({
        "cookies": cookies if cookies is not None else [],
        "origins": origins if origins is not None else [],
    }), encoding="utf-8")


# --- the shape of the command tree ------------------------------------------


def test_no_subcommand_prints_help_and_fails(capsys):
    """Bare `autoweb` is a mistake, not a no-op, so it must not exit 0."""
    assert run([]) == 1
    assert "usage" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("argv", [
    ["config", "show"],
    ["config", "check"],
    ["state", "inspect"],
    ["lanes", "list"],
    ["lanes", "sync"],
    ["trace", "missing.json"],
    ["merge", "missing-lane.json"],
])
def test_every_documented_command_resolves(argv, project):
    """A command named in the README that argparse rejects is a broken promise. This
    asserts only that it runs and returns an int, not what it prints."""
    assert isinstance(run(argv, project), int)


def test_version_is_reported(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "autoweb" in capsys.readouterr().out


# --- config -----------------------------------------------------------------


def test_config_check_passes_on_a_valid_file(project, capsys):
    (project / "autoweb.toml").write_text(
        "[lanes]\nmax = 3\n[state]\nindexeddb = true\n", encoding="utf-8")
    assert run(["config", "check"], project) == 0
    assert "ok" in capsys.readouterr().out.lower()


def test_a_malformed_config_exits_2_and_names_the_key(project, capsys):
    """Exit 2 is reserved for the user's own files being wrong, and the message has to
    say which key, or the user is left bisecting their own config."""
    (project / "autoweb.toml").write_text("[lanes]\nmax = 'three'\n", encoding="utf-8")
    assert run(["config", "check"], project) == 2
    assert "max" in capsys.readouterr().err


def test_an_unparseable_config_exits_2_rather_than_tracebacking(project, capsys):
    (project / "autoweb.toml").write_text("[lanes\nmax = 3\n", encoding="utf-8")
    assert run(["config", "show"], project) == 2
    assert capsys.readouterr().err.startswith("error:")


# --- state inspect ----------------------------------------------------------


def test_inspect_reports_a_missing_file_without_a_traceback(project, capsys):
    assert run(["state", "inspect"], project) == 2
    assert "error:" in capsys.readouterr().err


def test_inspect_separates_cookie_origins_from_storage_origins(project, capsys):
    """The split is the whole point of the command: it is what you tune caps against,
    and a count of origins alone does not tell you where the identity lives."""
    write_state(
        project / "root.json",
        origins=[
            {"origin": "https://a.example", "localStorage": [{"name": "t", "value": "1"}]},
            {"origin": "https://b.example", "localStorage": []},
        ],
        cookies=[{"name": "sid", "value": "x", "domain": "a.example", "path": "/"}],
    )
    assert run(["state", "inspect"], project) == 0
    out = capsys.readouterr().out
    assert "a.example" in out
    assert "2" in out          # two origins


def test_inspect_separates_stored_origins_from_cookie_only_hosts(project, capsys):
    """On a real profile these differ by two orders of magnitude: 819 hosts set a cookie
    and four hold any storage. Reporting one number read as though the identity were
    enormous, and it also tripped the max_origins cap on every real export."""
    write_state(
        project / "root.json",
        origins=[{"origin": "https://real.example",
                  "localStorage": [{"name": "t", "value": "1"}]}],
        cookies=[{"name": "a", "value": "1", "domain": "adtech1.example", "path": "/"},
                 {"name": "b", "value": "1", "domain": "adtech2.example", "path": "/"}],
    )
    assert run(["state", "inspect"], project) == 0
    out = capsys.readouterr().out
    assert "1 origins hold localStorage or IndexedDB" in out
    assert "2 are cookies only" in out


def test_inspect_does_not_trip_the_origin_cap_on_cookie_only_hosts(project, capsys):
    """The cap exists to bound stored origins. Counting ad-tech cookie domains against it
    reported OVER CAP on every real identity while saying nothing about what it bounds."""
    (project / "autoweb.toml").write_text("[caps]\nmax_origins = 5\n", encoding="utf-8")
    write_state(
        project / "root.json",
        origins=[{"origin": "https://real.example",
                  "localStorage": [{"name": "t", "value": "1"}]}],
        cookies=[{"name": f"c{n}", "value": "1", "domain": f"ad{n}.example", "path": "/"}
                 for n in range(20)],
    )
    assert run(["state", "inspect"], project) == 0
    assert "max_origins" not in capsys.readouterr().out


def test_inspect_rejects_a_json_file_that_is_not_a_storage_state(project, capsys):
    """A plausible-looking JSON file that is not a storageState would otherwise be
    discovered only when a lane failed to log in."""
    (project / "root.json").write_text('{"hello": "world"}', encoding="utf-8")
    assert run(["state", "inspect"], project) == 2
    assert "error:" in capsys.readouterr().err


# --- state verify, the gate -------------------------------------------------


def _stub_check(monkeypatch, *, final_url, title="", status=200, body=""):
    """Replace the browser round trip. The assertion logic is what is under test."""
    def fake(path, url, *, browser="chrome", timeout_seconds=60):
        return state.SeedResult(title=title, final_url=final_url, status=status,
                               body_text=body)
    monkeypatch.setattr(state, "seeded_context_check", fake)


def test_verify_passes_when_the_browser_stayed_put(project, monkeypatch):
    write_state(project / "root.json")
    _stub_check(monkeypatch, final_url="https://x.example/secure", title="Secure")
    assert run(["state", "verify", "https://x.example/secure"], project) == 0


def test_verify_fails_on_a_redirect_with_no_explicit_assertion(project, monkeypatch):
    """The default signal. A site that bounced you elsewhere logged you out, and this
    is the case that matters most because it needs no flags to catch."""
    write_state(project / "root.json")
    _stub_check(monkeypatch, final_url="https://x.example/login", title="Sign in")
    assert run(["state", "verify", "https://x.example/secure"], project) == 1


def test_verify_ignores_a_trailing_slash_difference(project, monkeypatch):
    """Otherwise every second site fails the gate for no reason."""
    write_state(project / "root.json")
    _stub_check(monkeypatch, final_url="https://x.example/secure/")
    assert run(["state", "verify", "https://x.example/secure"], project) == 0


def test_verify_fails_when_expected_text_is_absent(project, monkeypatch):
    write_state(project / "root.json")
    _stub_check(monkeypatch, final_url="https://x.example/secure",
                title="Secure", body="please sign in")
    assert run(["state", "verify", "https://x.example/secure",
                "--expect-text", "my account"], project) == 1


def test_verify_matches_expected_text_in_the_body_not_only_the_title(project,
                                                                    monkeypatch):
    """A title is a weak signal: many sites serve one title across login and secure
    pages alike, which is how a verify that only read titles passed while logged out."""
    write_state(project / "root.json")
    _stub_check(monkeypatch, final_url="https://x.example/secure",
                title="Example", body="Welcome back, My Account")
    assert run(["state", "verify", "https://x.example/secure",
                "--expect-text", "my account"], project) == 0


def test_verify_fails_on_a_non_2xx_even_without_a_redirect(project, monkeypatch):
    """"OK" on a 404 with an empty title is a false pass, and this command is a gate."""
    write_state(project / "root.json")
    _stub_check(monkeypatch, final_url="https://x.example/secure", status=404)
    assert run(["state", "verify", "https://x.example/secure"], project) == 1


def test_verify_fails_when_the_url_assertion_is_not_met(project, monkeypatch):
    write_state(project / "root.json")
    _stub_check(monkeypatch, final_url="https://x.example/elsewhere")
    assert run(["state", "verify", "https://x.example/elsewhere",
                "--expect-url", "/secure"], project) == 1


def test_verify_reports_a_missing_state_file_as_the_user_s_problem(project):
    assert run(["state", "verify", "https://x.example/"], project) == 2


# --- lanes ------------------------------------------------------------------


def test_lanes_sync_creates_the_ceiling_and_lists_them(project, capsys):
    (project / "autoweb.toml").write_text("[lanes]\nmax = 3\n", encoding="utf-8")
    assert run(["lanes", "sync"], project) == 0
    assert run(["lanes", "list"], project) == 0
    out = capsys.readouterr().out
    for name in ("lane-1.md", "lane-2.md", "lane-3.md"):
        assert name in out


def test_lanes_sync_says_a_restart_is_needed(project, capsys):
    """Agent files are read once at startup. A session that is already running keeps the
    old ones and its lanes share a browser without raising anything, so the command that
    creates that condition has to say so."""
    assert run(["lanes", "sync"], project) == 0
    assert "restart" in capsys.readouterr().out.lower()


def test_lanes_sync_warns_when_the_identity_file_is_missing(project, capsys):
    """An isolated lane without root.json does not start logged out. Every browser call
    fails with ENOENT, which is worth saying out loud."""
    assert run(["lanes", "sync"], project) == 0
    out = capsys.readouterr().out
    assert "root.json" in out
    assert "state export" in out


def test_lanes_sync_does_not_warn_about_the_identity_file_in_persistent_mode(project,
                                                                            capsys):
    """A persistent lane never reads root.json, so warning about it would send the user
    to fix something that is not wrong."""
    (project / "autoweb.toml").write_text(
        "[lanes]\nisolated = false\n", encoding="utf-8")
    write_state(project / "unused.json")
    assert run(["lanes", "sync"], project) == 0
    assert "ENOENT" not in capsys.readouterr().out


def test_lanes_list_before_sync_points_at_sync(project, capsys):
    assert run(["lanes", "list"], project) == 0
    assert "lanes sync" in capsys.readouterr().out


def test_lanes_sync_leaves_a_hand_written_lane_alone_and_says_so(project, capsys):
    """The protection exists so that raising lanes.max never destroys an agent somebody
    wrote by hand, and the user has to be told which file was skipped and why."""
    agents = project / ".claude" / "agents"
    agents.mkdir(parents=True)
    mine = agents / "lane-1.md"
    mine.write_text("---\nname: lane-1\n---\nmine\n", encoding="utf-8")
    assert run(["lanes", "sync"], project) == 0
    assert mine.read_text(encoding="utf-8") == "---\nname: lane-1\n---\nmine\n"
    out = capsys.readouterr().out
    assert "lane-1.md" in out
    assert "hand" in out.lower()


# --- state export --from-profile --------------------------------------------


def test_export_from_profile_demands_an_origin_to_walk(project, capsys):
    """The failure this prevents is silent and expensive. A storageState collects
    localStorage and IndexedDB only for origins the context has visited, so an export
    with nothing to walk returns cookies and no origin storage, which looks like a
    successful export of a working identity and is not one."""
    (project / "fake-profile").mkdir()
    assert run(["state", "export", "--from-profile",
                str(project / "fake-profile")], project) == 2
    assert "--visit" in capsys.readouterr().err


def test_export_needs_either_a_url_or_a_profile(project, capsys):
    assert run(["state", "export"], project) == 2
    assert "--from-profile" in capsys.readouterr().err


def test_export_from_a_missing_profile_directory_says_so(project, capsys):
    assert run(["state", "export", "--from-profile", str(project / "nope"),
                "--visit", "https://x.example/"], project) == 2
    assert "profile" in capsys.readouterr().err.lower()


def test_a_copied_profile_at_the_repo_root_cannot_be_committed(project):
    """A logged-in profile is as sensitive as root.json. The canonical location is
    covered by `.autoweb/`, but the mistake someone actually makes is copying it to the
    repo root, so the patterns have to catch that too."""
    import subprocess
    repo = pathlib.Path(__file__).parent.parent
    for candidate in ("browser-profile/Default/Cookies",
                      "pursuit-browser-profile/Default/Cookies",
                      "profile-copy/Default/Cookies"):
        out = subprocess.run(["git", "check-ignore", "-v", candidate],
                             cwd=repo, capture_output=True, text=True,
                             check=False)
        assert out.returncode == 0, f"{candidate} is NOT gitignored"


# --- trace, the simultaneity gate -------------------------------------------


def _trace_file(path, lanes):
    path.write_text(json.dumps(lanes), encoding="utf-8")
    return str(path)


def _marks(t0, t1, t2, t3):
    def at(sec):
        return f"2026-10-03T14:00:{sec:02d}.000Z"
    return {"T0_start": at(t0), "T1_loaded": at(t1), "T2_read": at(t2), "T3_end": at(t3)}


def test_trace_exits_zero_when_every_lane_overlapped(project, tmp_path, capsys):
    path = _trace_file(tmp_path / "t.json", {
        "lane-1": _marks(0, 2, 28, 30),
        "lane-3": _marks(1, 3, 27, 29),
    })
    assert run(["trace", path], project) == 0
    assert "CONCURRENT" in capsys.readouterr().out


def test_trace_exits_nonzero_when_lanes_took_turns(project, tmp_path, capsys):
    """It is a gate. "They ran in parallel" is the claim this repo has been wrong about,
    so it gets an exit code and not a paragraph."""
    path = _trace_file(tmp_path / "t.json", {
        "lane-1": _marks(0, 1, 4, 5),
        "lane-3": _marks(6, 7, 9, 10),
    })
    assert run(["trace", path], project) == 1
    err = capsys.readouterr().err
    assert "SERIAL" in err
    assert "lane-1 had already finished before lane-3 started" in err


def test_trace_rejects_an_untrustworthy_trace_rather_than_judging_it(project, tmp_path):
    path = _trace_file(tmp_path / "t.json", {
        "lane-1": _marks(30, 20, 10, 0),
        "lane-3": _marks(0, 1, 2, 3),
    })
    assert run(["trace", path], project) == 2

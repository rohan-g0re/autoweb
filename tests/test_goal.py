"""`autoweb goal check`: the verdict on a run comes from a command, not from the model.

What is pinned here is the contract another program reads: which goal files parse, what
`runs/<run>/status.json` holds, when `done` is true, and the exit code. Command
assertions run real subprocesses, because a timeout that does not kill what the shell
started is a defect only a real process shows. Browser assertions go through a stub of
`goal.seeded_page`, the way `test_cli.py` stubs `state verify`, so nothing here opens a
browser.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autoweb import cli, goal
from autoweb.state import StateError

REPO = Path(__file__).resolve().parent.parent

# Commands that mean the same thing to cmd.exe and to sh. shell=True is the contract,
# and the suite runs on both.
PY = f'"{sys.executable}"'


def py(code: str) -> str:
    """A shell command running *code* with this interpreter. No double quotes inside."""
    return f'{PY} -c "{code}"'


def toml_str(value: str) -> str:
    """A TOML basic string. JSON's escapes are a subset of TOML's, so Windows paths and
    quotes of either kind survive."""
    return json.dumps(value)


def goal_text(*tables: str, heading: str = "## Assertions") -> str:
    body = "\n\n".join(tables)
    return f"# Goal: test\n\n{heading}\n\n```toml\n{body}\n```\n"


def command(ident: str, run: str, timeout_s: float | None = None) -> str:
    lines = ["[[assert]]", f'id = "{ident}"', 'kind = "command"', f"run = {toml_str(run)}"]
    if timeout_s is not None:
        lines.append(f"timeout_s = {timeout_s}")
    return "\n".join(lines)


def browser(ident: str, kind: str, url: str, expect: str, timeout_s=None) -> str:
    lines = ["[[assert]]", f'id = "{ident}"', f'kind = "{kind}"', f'url = "{url}"',
             f"expect = {toml_str(expect)}"]
    if timeout_s is not None:
        lines.append(f"timeout_s = {timeout_s}")
    return "\n".join(lines)


@pytest.fixture
def project(tmp_path):
    """A project with an empty autoweb.toml and a goals/ directory."""
    root = tmp_path / "proj"
    (root / "goals").mkdir(parents=True)
    (root / "autoweb.toml").write_text("", encoding="utf-8")
    return root


def write_goal(project, text, name="x"):
    path = project / "goals" / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def write_identity(project):
    (project / "root.json").write_text('{"cookies": [], "origins": []}', encoding="utf-8")


def check(project, *argv):
    return cli.main(["-C", str(project), "goal", "check", *argv])


def status_of(project, run="r1"):
    return json.loads((project / "runs" / run / "status.json").read_text(encoding="ascii"))


# --- parsing the goal -----------------------------------------------------------


def test_a_well_formed_block_yields_every_assertion_in_order():
    parsed = goal.parse(goal_text(
        browser("feed-loads", "url", "https://x.example/feed/", "https://x.example/feed/"),
        command("export-exists", "test -s runs/{run}/deliverable.md"),
    ))
    assert [(a.id, a.kind) for a in parsed] == [("feed-loads", "url"),
                                                ("export-exists", "command")]
    assert parsed[0].url == "https://x.example/feed/"
    assert parsed[1].run == "test -s runs/{run}/deliverable.md"


def test_the_timeout_defaults_to_thirty_seconds_and_can_be_set():
    parsed = goal.parse(goal_text(command("a", "true"), command("b", "true", 5)))
    assert [a.timeout_s for a in parsed] == [30, 5]


def test_a_goal_with_no_assertions_section_is_not_a_goal():
    with pytest.raises(goal.GoalError, match=goal.NO_ASSERTION[1:]):
        goal.parse("# Goal: x\n\n## The work\n\n1. something\n")


def test_an_assertions_section_without_a_toml_block_is_not_a_goal():
    """The old prose form, which a model had to interpret, is exactly what this replaces."""
    text = "# Goal: x\n\n## Assertions\n\n1. The final URL contains `/feed`\n"
    with pytest.raises(goal.GoalError, match=goal.NO_ASSERTION[1:]):
        goal.parse(text)


def test_a_toml_block_with_no_assert_tables_is_not_a_goal():
    with pytest.raises(goal.GoalError, match=goal.NO_ASSERTION[1:]):
        goal.parse("## Assertions\n\n```toml\n# nothing yet\n```\n")


def test_an_unknown_kind_is_rejected_and_the_kinds_are_named():
    text = goal_text('[[assert]]\nid = "a"\nkind = "screenshot"\nrun = "true"')
    with pytest.raises(goal.GoalError, match="url, text, element, command"):
        goal.parse(text)


def test_a_duplicate_id_is_rejected_because_status_json_is_read_by_id():
    text = goal_text(command("same", "true"), command("same", "false"))
    with pytest.raises(goal.GoalError, match="'same' is used twice"):
        goal.parse(text)


def test_a_block_that_is_not_toml_is_rejected():
    text = "## Assertions\n\n```toml\n[[assert]\nid = a\n```\n"
    with pytest.raises(goal.GoalError, match="not valid TOML"):
        goal.parse(text)


@pytest.mark.parametrize("ident", ["Feed", "feed loads", "feed_loads", "", 7])
def test_an_id_outside_lowercase_digits_and_dashes_is_rejected(ident):
    text = goal_text(f"[[assert]]\nid = {json.dumps(ident)}\nkind = \"command\"\n"
                     f"run = \"true\"")
    with pytest.raises(goal.GoalError, match="'id' must be"):
        goal.parse(text)


def test_a_misspelled_key_is_rejected_rather_than_quietly_defaulted():
    """`timeout = 5` falling back to 30 would change what the check means unseen."""
    text = goal_text('[[assert]]\nid = "a"\nkind = "command"\nrun = "true"\ntimeout = 5')
    with pytest.raises(goal.GoalError, match="'timeout'"):
        goal.parse(text)


def test_a_command_assertion_takes_no_url_and_a_browser_assertion_takes_no_run():
    with pytest.raises(goal.GoalError, match="'url'"):
        goal.parse(goal_text('[[assert]]\nid = "a"\nkind = "command"\nrun = "true"\n'
                             'url = "https://x.example/"'))
    with pytest.raises(goal.GoalError, match="'run'"):
        goal.parse(goal_text(browser("a", "text", "https://x.example/", "hi")
                             + '\nrun = "true"'))


@pytest.mark.parametrize("missing", ["url", "expect"])
def test_a_browser_assertion_needs_both_a_url_and_an_expectation(missing):
    table = browser("a", "text", "https://x.example/", "hi")
    table = "\n".join(line for line in table.splitlines()
                      if not line.startswith(missing))
    with pytest.raises(goal.GoalError, match=f"needs '{missing}'"):
        goal.parse(goal_text(table))


def test_a_command_assertion_needs_something_to_run():
    with pytest.raises(goal.GoalError, match="needs 'run'"):
        goal.parse(goal_text('[[assert]]\nid = "a"\nkind = "command"\nrun = "  "'))


def test_a_url_without_a_scheme_is_rejected_before_any_browser_opens():
    with pytest.raises(goal.GoalError, match="full URL"):
        goal.parse(goal_text(browser("a", "url", "x.example/feed", "x.example/feed")))


@pytest.mark.parametrize("value", ["0", "-1", "true", '"30"', "inf"])
def test_a_timeout_that_is_not_a_positive_number_is_rejected(value):
    text = goal_text(f'[[assert]]\nid = "a"\nkind = "command"\nrun = "true"\n'
                     f"timeout_s = {value}")
    with pytest.raises(goal.GoalError, match="timeout_s"):
        goal.parse(text)


def test_a_single_bracket_table_is_rejected_with_the_fix():
    with pytest.raises(goal.GoalError, match="two brackets"):
        goal.parse(goal_text('[assert]\nid = "a"\nkind = "command"\nrun = "true"'))


def test_a_stray_top_level_key_is_rejected():
    with pytest.raises(goal.GoalError, match="'name'"):
        goal.parse(goal_text('name = "x"\n\n' + command("a", "true")))


def test_the_singular_heading_older_goals_use_still_counts():
    parsed = goal.parse(goal_text(command("a", "true"), heading="## Assertion"))
    assert [a.id for a in parsed] == ["a"]


def test_a_toml_block_outside_the_section_is_not_an_assertion():
    text = ("## Resources\n\n```toml\n" + command("elsewhere", "true") + "\n```\n\n"
            + "## Assertions\n\n```toml\n" + command("here", "true") + "\n```\n\n"
            + "## Reporting\n\n```toml\n" + command("after", "true") + "\n```\n")
    assert [a.id for a in goal.parse(text)] == ["here"]


def test_a_heading_inside_a_code_fence_does_not_open_the_section():
    """The goal template shows a whole goal inside a fence. Its heading is an example,
    not this file's section."""
    text = ("# Notes\n\n````markdown\n## Assertions\n\n```toml\n"
            + command("example", "true") + "\n```\n````\n")
    with pytest.raises(goal.GoalError, match=goal.NO_ASSERTION[1:]):
        goal.parse(text)


def test_several_toml_blocks_under_the_section_are_read_together():
    text = ("## Assertions\n\nThe page:\n\n```toml\n" + command("a", "true")
            + "\n```\n\nThe file:\n\n```toml\n" + command("b", "true") + "\n```\n")
    assert [a.id for a in goal.parse(text)] == ["a", "b"]


def test_an_unclosed_toml_fence_is_reported_with_its_line():
    with pytest.raises(goal.GoalError, match="line 3 is never closed"):
        goal.parse("## Assertions\n\n```toml\n" + command("a", "true") + "\n")


def test_the_template_in_goals_readme_parses_with_one_assertion_of_each_kind():
    """The template is what people copy. If it does not parse, every goal starts broken."""
    readme = (REPO / "goals" / "README.md").read_text(encoding="utf-8")
    template = re.search(r"^````markdown\n(.*?)^````$", readme, re.DOTALL | re.MULTILINE)
    assert template, "goals/README.md has no ````markdown template block"
    parsed = goal.parse(template.group(1), source="template")
    assert sorted(a.kind for a in parsed) == sorted(goal.KINDS)


# --- substitution and the run id --------------------------------------------------


def test_run_and_goal_are_substituted_and_every_other_brace_reaches_the_shell():
    out = goal.substitute("test -s runs/{run}/{goal}.md && awk '{print $1}' ${HOME}",
                          run_id="2026-11-01-x", goal_name="x")
    assert out == "test -s runs/2026-11-01-x/x.md && awk '{print $1}' ${HOME}"


@pytest.mark.parametrize("run", ["../x", "a/b", "a\\b", "..", ".", "", ".hidden", "a..b"])
def test_a_run_id_cannot_climb_out_of_runs(run):
    with pytest.raises(goal.GoalError, match="one directory name"):
        goal.check_run_id(run)


def test_an_ordinary_run_id_is_accepted():
    assert goal.check_run_id("2026-11-01-daily-digest") == "2026-11-01-daily-digest"


# --- command assertions, with real subprocesses -------------------------------------


def evaluate(project, assertion, run_id="r1"):
    return goal.evaluate(assertion, root_dir=project, state_path=project / "root.json",
                         browser="chromium", run_id=run_id, goal_name="x")


def one(text):
    (assertion,) = goal.parse(goal_text(text))
    return assertion


def test_a_command_that_exits_0_passes(project):
    outcome = evaluate(project, one(command("ok", py("print('fine')"))))
    assert (outcome.status, outcome.detail) == ("PASS", "exit 0: fine")


def test_a_command_that_exits_non_zero_fails_with_its_output_in_the_detail(project):
    code = "import sys; sys.stderr.write('no deliverable'); sys.exit(3)"
    outcome = evaluate(project, one(command("bad", py(code))))
    assert outcome.status == "FAIL"
    assert outcome.detail == "exit 3: no deliverable"


def test_a_command_that_outlives_its_timeout_is_an_error_and_is_killed(project):
    started = time.monotonic()
    outcome = evaluate(project, one(command("slow", py("import time; time.sleep(60)"), 1)))
    assert outcome.status == "ERROR"
    assert outcome.detail.startswith("timed out after 1s")
    assert time.monotonic() - started < 20, "the timeout did not kill the command"


def test_a_command_runs_in_the_project_root_with_the_run_substituted(project):
    (project / "runs" / "r9").mkdir(parents=True)
    (project / "runs" / "r9" / "deliverable.md").write_text("done\n", encoding="utf-8")
    code = ("import pathlib, sys; "
            "sys.exit(0 if pathlib.Path('runs/{run}/deliverable.md').stat().st_size else 1)")
    assert evaluate(project, one(command("exists", py(code))), run_id="r9").status == "PASS"
    assert evaluate(project, one(command("exists", py(code))), run_id="r8").status == "FAIL"


def test_long_output_is_cut_to_its_end_on_one_line(project):
    code = "print('x' * 1000); print('the reason')"
    outcome = evaluate(project, one(command("noisy", py(code))))
    assert outcome.detail.endswith("the reason")
    assert "\n" not in outcome.detail
    assert len(outcome.detail) < 400


# --- browser assertions, judged from a stub ------------------------------------------


def view(final_url, status=200, body="", visible=None):
    return goal.PageView(final_url=final_url, status=status, body_text=body,
                         selector_visible=visible)


def url_assertion(expect="https://x.example/feed/"):
    return one(browser("feed", "url", "https://x.example/feed/", expect))


def test_a_url_assertion_passes_when_the_final_url_starts_with_the_expectation():
    status, detail = goal.judge(url_assertion(), view("https://x.example/feed/?tab=1"))
    assert (status, detail) == ("PASS", "landed on https://x.example/feed/?tab=1")


def test_a_url_assertion_ignores_only_a_trailing_slash():
    assert goal.judge(url_assertion(), view("https://x.example/feed"))[0] == "PASS"
    assert goal.judge(url_assertion(), view("https://x.example/fee"))[0] == "FAIL"


def test_a_url_assertion_fails_when_the_page_redirected_to_a_login():
    status, detail = goal.judge(url_assertion(), view("https://x.example/login"))
    assert status == "FAIL"
    assert "landed on https://x.example/login" in detail


def test_an_error_status_fails_even_at_the_expected_url():
    """"OK" on a 404 is the false pass `state verify` once shipped."""
    status, detail = goal.judge(url_assertion(), view("https://x.example/feed/", 404))
    assert (status, detail) == ("FAIL", "HTTP 404 at https://x.example/feed/")


def test_a_text_assertion_ignores_case_and_whitespace():
    """innerText applies CSS text-transform, so a styled heading reads back in capitals."""
    text = one(browser("t", "text", "https://x.example/", "Signed in as  Ada"))
    assert goal.judge(text, view("https://x.example/", body="SIGNED IN\nAS ADA"))[0] == "PASS"
    assert goal.judge(text, view("https://x.example/", body="Sign in"))[0] == "FAIL"


def test_an_element_assertion_passes_only_when_the_element_is_visible():
    element = one(browser("e", "element", "https://x.example/", "button#logout"))
    assert goal.judge(element, view("https://x.example/", visible=True))[0] == "PASS"
    assert goal.judge(element, view("https://x.example/", visible=False))[0] == "FAIL"


# --- status.json --------------------------------------------------------------------


def outcome(ident, status):
    return goal.Outcome(ident, "command", status, "detail")


def test_status_json_has_exactly_the_contract_fields():
    doc = goal.status_document("goals/x.md", "r1", [outcome("a", "PASS")],
                               checked_at=datetime(2026, 11, 1, 12, 0, tzinfo=UTC))
    assert doc == {
        "schema": 1,
        "goal": "goals/x.md",
        "run": "r1",
        "checked_at": "2026-11-01T12:00:00Z",
        "assertions": [{"id": "a", "kind": "command", "status": "PASS",
                        "detail": "detail"}],
        "done": True,
    }


@pytest.mark.parametrize("statuses, done", [
    (["PASS", "PASS"], True),
    (["PASS", "FAIL"], False),
    (["PASS", "ERROR"], False),
    ([], False),
])
def test_done_is_true_only_when_every_assertion_passed(statuses, done):
    outcomes = [outcome(f"a{n}", s) for n, s in enumerate(statuses)]
    assert goal.status_document("g", "r", outcomes)["done"] is done


def test_the_status_file_is_sorted_indented_ascii_with_a_final_newline(tmp_path):
    path = tmp_path / "runs" / "r1" / "status.json"
    doc = goal.status_document("goals/x.md", "r1",
                               [goal.Outcome("a", "text", "FAIL", "caf\u00e9 not found")])
    goal.write_status(path, doc)
    raw = path.read_bytes().decode("ascii")
    assert raw == json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert "caf\\u00e9" in raw
    assert not path.with_suffix(".json.tmp").exists()


# --- the command, end to end --------------------------------------------------------


def stub_page(monkeypatch, result=None, *, raises=None):
    """Replace the browser round trip and record what it was asked for."""
    calls = []

    def fake(state_path, url, *, browser="chrome", timeout_seconds=30, wait_text=None,
             selector=None):
        calls.append({"state_path": state_path, "url": url, "browser": browser,
                      "timeout_seconds": timeout_seconds, "wait_text": wait_text,
                      "selector": selector})
        if raises is not None:
            raise raises
        return result
    monkeypatch.setattr(goal, "seeded_page", fake)
    return calls


def test_every_assertion_passing_exits_0_and_writes_done_true(project, capsys):
    write_goal(project, goal_text(command("a", py("pass")), command("b", py("pass"))))
    assert check(project, "goals/x.md", "--run", "r1") == 0
    status = status_of(project)
    assert status["done"] is True
    assert status["goal"] == "goals/x.md"
    assert status["run"] == "r1"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", status["checked_at"])
    out = capsys.readouterr().out
    assert "PASS  a (command): exit 0" in out
    assert "PASS  b (command): exit 0" in out


def test_one_failing_assertion_exits_1_and_writes_done_false(project, capsys):
    write_goal(project, goal_text(command("a", py("pass")),
                                  command("b", py("import sys; sys.exit(1)"))))
    assert check(project, "goals/x.md", "--run", "r1") == 1
    status = status_of(project)
    assert status["done"] is False
    assert [a["status"] for a in status["assertions"]] == ["PASS", "FAIL"]
    assert "FAIL  b (command): exit 1" in capsys.readouterr().out


def test_an_assertion_that_could_not_be_evaluated_exits_1(project, monkeypatch):
    write_identity(project)
    write_goal(project, goal_text(browser("feed", "url", "https://x.example/",
                                          "https://x.example/")))
    stub_page(monkeypatch, raises=StateError("could not load https://x.example/: boom"))
    assert check(project, "x", "--run", "r1") == 1
    (only,) = status_of(project)["assertions"]
    assert only == {"id": "feed", "kind": "url", "status": "ERROR",
                    "detail": "could not load https://x.example/: boom"}


def test_browser_assertions_are_seeded_from_the_identity_with_their_own_budget(
        project, monkeypatch):
    write_identity(project)
    write_goal(project, goal_text(
        browser("feed", "url", "https://x.example/feed/", "https://x.example/feed/"),
        browser("hello", "text", "https://x.example/feed/", "Welcome back", 12),
        browser("logout", "element", "https://x.example/feed/", "button#logout"),
    ))
    calls = stub_page(monkeypatch, view("https://x.example/feed/", body="welcome back",
                                        visible=True))
    assert check(project, "goals/x.md", "--run", "r1") == 0
    assert [c["state_path"] for c in calls] == [(project / "root.json").resolve()] * 3
    assert [c["timeout_seconds"] for c in calls] == [30, 12, 30]
    assert [(c["wait_text"], c["selector"]) for c in calls] == [
        (None, None), ("Welcome back", None), (None, "button#logout")]
    assert status_of(project)["done"] is True


def test_root_flag_names_the_identity_to_seed_from(project, monkeypatch):
    other = project / "elsewhere.json"
    other.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    write_goal(project, goal_text(browser("feed", "url", "https://x.example/",
                                          "https://x.example/")))
    calls = stub_page(monkeypatch, view("https://x.example/"))
    assert check(project, "x", "--run", "r1", "--root", str(other)) == 0
    assert calls[0]["state_path"] == other.resolve()


def test_a_goal_without_assertions_exits_2_and_writes_nothing(project, capsys):
    write_goal(project, "# Goal: x\n\n## The work\n\n1. something\n")
    assert check(project, "goals/x.md", "--run", "r1") == 2
    assert goal.NO_ASSERTION in capsys.readouterr().err.lower()
    assert not (project / "runs" / "r1" / "status.json").exists()


def test_a_malformed_goal_removes_a_stale_status_that_said_done(project, capsys):
    """Otherwise `done: true` from an earlier check outlives the goal it described."""
    write_goal(project, goal_text(command("a", py("pass"))))
    assert check(project, "x", "--run", "r1") == 0
    write_goal(project, goal_text('[[assert]]\nid = "a"\nkind = "nope"'))
    assert check(project, "x", "--run", "r1") == 2
    assert not (project / "runs" / "r1" / "status.json").exists()
    assert "removed" in capsys.readouterr().err


def test_a_missing_goal_file_exits_2(project, capsys):
    assert check(project, "goals/nope.md", "--run", "r1") == 2
    assert "no goal file" in capsys.readouterr().err


def test_a_bad_run_id_exits_2_before_anything_runs(project):
    marker = project / "ran"
    write_goal(project, goal_text(command("a", py(f"open(r'{marker}', 'w')"))))
    assert check(project, "x", "--run", "../outside") == 2
    assert not marker.exists()


def test_browser_assertions_without_an_identity_exit_2(project, monkeypatch, capsys):
    write_goal(project, goal_text(browser("feed", "url", "https://x.example/",
                                          "https://x.example/")))
    calls = stub_page(monkeypatch, view("https://x.example/"))
    assert check(project, "x", "--run", "r1") == 2
    assert "state export" in capsys.readouterr().err
    assert calls == []


def test_command_assertions_need_no_identity(project):
    write_goal(project, goal_text(command("a", py("pass"))))
    assert not (project / "root.json").exists()
    assert check(project, "x", "--run", "r1") == 0


def test_the_goal_can_be_named_without_its_directory_or_extension(project):
    write_goal(project, goal_text(command("a", py("pass"))), name="daily-digest")
    assert check(project, "daily-digest", "--run", "r1") == 0
    assert check(project, "daily-digest.md", "--run", "r2") == 0
    assert status_of(project, "r2")["goal"] == "goals/daily-digest.md"


def test_the_run_is_substituted_through_the_command_line(project):
    (project / "runs" / "2026-11-01-x").mkdir(parents=True)
    (project / "runs" / "2026-11-01-x" / "deliverable.md").write_text("ok\n",
                                                                      encoding="utf-8")
    code = ("import pathlib, sys; "
            "sys.exit(0 if pathlib.Path('runs/{run}/deliverable.md').is_file() else 1)")
    write_goal(project, goal_text(command("export-exists", py(code))))
    assert check(project, "x", "--run", "2026-11-01-x") == 0
    assert check(project, "x", "--run", "2026-11-02-x") == 1


def test_a_dry_run_prints_the_plan_and_opens_runs_and_writes_nothing(project,
                                                                    monkeypatch, capsys):
    marker = project / "ran"
    write_goal(project, goal_text(
        browser("feed", "url", "https://x.example/", "https://x.example/"),
        command("a", py(f"open(r'{marker}', 'w')")),
    ))
    calls = stub_page(monkeypatch, view("https://x.example/"))
    assert check(project, "x", "--run", "r1", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "PLAN  feed (url): open https://x.example/" in out
    assert "PLAN  a (command): run" in out
    assert "nothing was opened, run or written" in out
    assert calls == []
    assert not marker.exists()
    assert not (project / "runs").exists()


def test_a_dry_run_of_a_malformed_goal_exits_2(project):
    write_goal(project, goal_text(command("a", "true"), command("a", "true")))
    assert check(project, "x", "--run", "r1", "--dry-run") == 2


def test_the_run_flag_is_required(project):
    write_goal(project, goal_text(command("a", "true")))
    with pytest.raises(SystemExit) as exc:
        check(project, "x")
    assert exc.value.code == 2

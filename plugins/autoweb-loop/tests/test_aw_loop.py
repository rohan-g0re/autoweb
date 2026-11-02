"""scripts/aw_loop.py --start / --stop / --status, through py.sh as the skill runs it."""
from __future__ import annotations

import json

from loop_helpers import TODAY, assertion, fwd, payload, read_loop, write_loop, write_status


def test_start_writes_the_loop_file(aw_loop, project, autoweb_plugin):
    result = aw_loop("-C", fwd(project), "--start", "feed", "--max", "3",
                     "--session", "sess-1")
    assert result.returncode == 0, result.stdout + result.stderr
    loop = read_loop(project)
    assert loop["goal"] == "goals/feed.md"
    assert loop["run"] == f"{TODAY}-feed"
    assert loop["max_iterations"] == 3
    assert loop["iteration"] == 1
    assert loop["started"].endswith("Z")
    assert loop["session"] == "sess-1"
    lines = dict(line.split(": ", 1) for line in result.stdout.splitlines()[1:])
    assert lines["run id"] == f"{TODAY}-feed"
    assert lines["status file"] == f"runs/{TODAY}-feed/status.json"
    assert lines["aw-run skill"] == fwd(autoweb_plugin / "skills" / "aw-run" / "SKILL.md")
    assert lines["autoweb plugin root"] == fwd(autoweb_plugin)


def test_start_defaults_to_five_iterations(aw_loop, project, autoweb_plugin):
    assert aw_loop("--start", "goals/feed.md", cwd=project).returncode == 0
    assert read_loop(project)["max_iterations"] == 5


def test_start_accepts_every_spelling_of_the_goal(aw_loop, project, autoweb_plugin):
    for spelling in ("feed", "feed.md", "goals/feed.md"):
        assert aw_loop("--start", spelling, cwd=project).returncode == 0, spelling
        assert read_loop(project)["goal"] == "goals/feed.md"
        assert aw_loop("--stop", cwd=project).returncode == 0


def test_an_unsubstituted_session_is_stored_as_unbound(aw_loop, project, autoweb_plugin):
    assert aw_loop("--start", "feed", "--session", "${CLAUDE_SESSION_ID}",
                   cwd=project).returncode == 0
    assert read_loop(project)["session"] is None


def test_start_takes_an_explicit_run_id(aw_loop, project, autoweb_plugin):
    assert aw_loop("--start", "feed", "--run", "2026-10-31-feed", cwd=project).returncode == 0
    assert read_loop(project)["run"] == "2026-10-31-feed"


def test_start_refuses_outside_an_autoweb_project(aw_loop, tmp_path, autoweb_plugin):
    plain = tmp_path / "plain"
    (plain / "goals").mkdir(parents=True)
    (plain / "goals" / "feed.md").write_text("x", encoding="utf-8")
    result = aw_loop("--start", "feed", cwd=plain)
    assert result.returncode == 1
    assert "not an AutoWeb project" in result.stdout
    assert not (plain / ".autoweb").exists()


def test_start_refuses_a_goal_that_is_not_there(aw_loop, project, autoweb_plugin):
    result = aw_loop("--start", "nope", cwd=project)
    assert result.returncode == 1
    assert "no goal file" in result.stdout and "feed.md" in result.stdout


def test_start_refuses_to_replace_a_running_loop(aw_loop, project, autoweb_plugin):
    write_loop(project, iteration=3)
    result = aw_loop("--start", "feed", cwd=project)
    assert result.returncode == 1
    assert "already active" in result.stdout
    assert read_loop(project)["iteration"] == 3


def test_start_refuses_without_the_autoweb_plugin(aw_loop, project):
    result = aw_loop("--start", "feed", cwd=project)
    assert result.returncode == 1
    assert "autoweb@autoweb" in result.stdout
    assert not (project / ".autoweb" / "active-loop.json").exists()


def test_the_autoweb_root_can_be_named_directly(aw_loop, project, tmp_path):
    root = tmp_path / "checkout"
    (root / "skills" / "aw-run").mkdir(parents=True)
    (root / "skills" / "aw-run" / "SKILL.md").write_text("x", encoding="utf-8")
    result = aw_loop("--start", "feed", cwd=project,
                     extra_env={"AW_LOOP_AUTOWEB_ROOT": fwd(root)})
    assert result.returncode == 0, result.stdout
    assert f"autoweb plugin root: {fwd(root)}" in result.stdout


def test_start_rejects_a_cap_out_of_range(aw_loop, project, autoweb_plugin):
    assert aw_loop("--start", "feed", "--max", "0", cwd=project).returncode == 2
    assert aw_loop("--start", "feed", "--max", "51", cwd=project).returncode == 2


def test_one_mode_is_required(aw_loop, project):
    assert aw_loop(cwd=project).returncode == 2
    assert aw_loop("--stop", "--status", cwd=project).returncode == 2


def test_stop_removes_the_loop_file(aw_loop, project):
    write_loop(project, iteration=2)
    result = aw_loop("--stop", cwd=project)
    assert result.returncode == 0
    assert "goals/feed.md at iteration 2 of 5" in result.stdout
    assert not (project / ".autoweb" / "active-loop.json").exists()


def test_stop_with_no_loop_is_fine(aw_loop, project):
    result = aw_loop("--stop", cwd=project)
    assert result.returncode == 0
    assert "no active loop" in result.stdout


def test_stop_removes_even_an_unreadable_loop_file(aw_loop, project):
    path = project / ".autoweb" / "active-loop.json"
    path.parent.mkdir()
    path.write_text("{", encoding="utf-8")
    assert aw_loop("--stop", cwd=project).returncode == 0
    assert not path.exists()


def test_status_reports_the_loop_and_what_fails(aw_loop, project, autoweb_plugin):
    write_loop(project, iteration=2)
    write_status(project, [assertion("title"), assertion("feed-loads", "FAIL", "authwall")])
    (project / ".autoweb" / "loop.log").write_text("--- earlier\nboom\n", encoding="utf-8")
    result = aw_loop("--status", cwd=project)
    assert result.returncode == 0
    out = result.stdout
    assert "autoweb.toml: present" in out
    assert "iteration 2 of 5" in out
    assert "assertions: 1 of 2 PASS" in out
    assert "feed-loads [FAIL]: authwall" in out
    assert "boom" in out


def test_status_with_no_loop(aw_loop, project):
    result = aw_loop("--status", cwd=project)
    assert result.returncode == 0
    assert "loop: none active" in result.stdout
    assert "NOT FOUND" in result.stdout


def test_status_flags_an_unreadable_loop_file(aw_loop, project):
    write_loop(project, max_iterations="five")
    result = aw_loop("--status", cwd=project)
    assert result.returncode == 1
    assert "UNREADABLE" in result.stdout


def test_start_then_hook_end_to_end(aw_loop, run_hook, project, autoweb_plugin):
    """The writer and the hook agree on the file, the run id and the session."""
    assert aw_loop("--start", "feed", "--max", "2", "--session", "s1",
                   cwd=project).returncode == 0
    run = read_loop(project)["run"]
    out = json.loads(run_hook(payload(project, session="s1"), cwd=project).stdout)
    assert out["decision"] == "block" and "iteration 2 of 2" in out["reason"]
    write_status(project, [assertion("feed-loads")], run=run, checked_at="2999-01-01T00:00:00Z")
    result = run_hook(payload(project, session="s1", active=True), cwd=project)
    assert result.stdout == ""
    assert not (project / ".autoweb" / "active-loop.json").exists()

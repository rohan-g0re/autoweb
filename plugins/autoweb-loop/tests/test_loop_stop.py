"""The Stop hook, run as a subprocess through py.sh with crafted stdin.

The first group is the one that matters most to people who never start a loop:
the hook fires on every Stop in every project, so with no loop file it must exit
0, print nothing, write nothing, and do it quickly.
"""
from __future__ import annotations

import json
import os
import time

from loop_helpers import RUN, assertion, payload, read_loop, write_loop, write_status


def tree(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


def block_of(result) -> dict:
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["decision"] == "block"
    return out


# ------------------------------------------------------------------- no loop: silent


def test_no_loop_file_exits_zero_silently_and_fast(run_hook, project):
    before = tree(project)
    started = time.perf_counter()
    result = run_hook(payload(project), cwd=project)
    elapsed = time.perf_counter() - started
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert elapsed < 1.0, f"the no-loop path took {elapsed:.2f}s"
    assert tree(project) == before, "the no-loop path wrote into the project"


def test_a_folder_that_is_not_an_autoweb_project_is_left_alone(run_hook, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = run_hook(payload(plain), cwd=plain)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert list(plain.iterdir()) == []


def test_garbage_stdin_with_no_loop_is_silent(run_hook, project):
    result = run_hook("this is not json {", cwd=project)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not (project / ".autoweb").exists()


def test_a_loop_in_a_parent_outside_the_project_does_not_reach_in(run_hook, tmp_path, project):
    """The walk up stops at the project's own autoweb.toml."""
    write_loop(tmp_path)  # a loop file above the project
    result = run_hook(payload(project), cwd=project)
    assert (result.returncode, result.stdout) == (0, "")
    assert (tmp_path / ".autoweb" / "active-loop.json").is_file()


# ----------------------------------------------------------------------- decisions


def test_done_removes_the_loop_file_and_says_nothing(run_hook, project):
    write_loop(project)
    write_status(project, [assertion("feed-loads"), assertion("title")])
    result = run_hook(payload(project), cwd=project)
    assert (result.returncode, result.stdout) == (0, "")
    assert not (project / ".autoweb" / "active-loop.json").exists()


def test_done_wins_over_the_cap(run_hook, project):
    write_loop(project, iteration=5, max_iterations=5)
    write_status(project, [assertion("feed-loads")])
    result = run_hook(payload(project), cwd=project)
    assert result.stdout == ""
    assert not (project / ".autoweb" / "active-loop.json").exists()


def test_the_loop_is_found_from_a_subdirectory(run_hook, project):
    """`cwd` follows a `cd`, so a session sitting in runs/ is still in the project."""
    write_loop(project)
    write_status(project, [assertion("feed-loads")])
    sub = project / "runs" / RUN
    result = run_hook(payload(sub), cwd=sub)
    assert result.stdout == ""
    assert not (project / ".autoweb" / "active-loop.json").exists()


def test_failing_blocks_names_the_failure_and_counts_an_iteration(run_hook, project):
    write_loop(project)
    write_status(project, [assertion("title"),
                           assertion("feed-loads", "FAIL", "landed on /authwall"),
                           assertion("count", "ERROR", "selector timed out")])
    out = block_of(run_hook(payload(project), cwd=project))
    reason = out["reason"]
    assert reason.startswith("iteration 2 of 5; failing: ")
    assert "feed-loads [FAIL]: landed on /authwall" in reason
    assert "count [ERROR]: selector timed out" in reason
    assert "title [" not in reason, "a passing assertion is not listed as failing"
    assert "aw-run pass for goals/feed.md" in reason
    assert f"--run {RUN}" in reason
    loop = read_loop(project)
    assert loop["iteration"] == 2
    assert loop["session"] == "sess-1", "the first Stop claims an unbound loop"


def test_done_false_with_every_assertion_passing_is_not_done(run_hook, project):
    """`done` is the contract's word, but a file that contradicts itself is not done."""
    write_loop(project)
    write_status(project, [assertion("feed-loads")], done=False)
    out = block_of(run_hook(payload(project), cwd=project))
    assert "done is false" in out["reason"]


def test_the_cap_removes_the_loop_and_tells_the_user_what_still_fails(run_hook, project):
    write_loop(project, iteration=3, max_iterations=3)
    write_status(project, [assertion("feed-loads", "FAIL", "landed on /authwall")])
    result = run_hook(payload(project), cwd=project)
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert "decision" not in out
    assert "cap reached (3 of 3)" in out["systemMessage"]
    assert "feed-loads [FAIL]: landed on /authwall" in out["systemMessage"]
    assert not (project / ".autoweb" / "active-loop.json").exists()


def test_missing_status_json_blocks_and_says_how_to_write_it(run_hook, project):
    write_loop(project)
    out = block_of(run_hook(payload(project), cwd=project))
    assert out["reason"].startswith(f"iteration 2 of 5; no status.json at runs/{RUN}/status.json")
    assert f"autoweb goal check goals/feed.md --run {RUN}" in out["reason"]
    assert read_loop(project)["iteration"] == 2


def test_missing_status_json_at_the_cap_ends_the_loop(run_hook, project):
    write_loop(project, iteration=5)
    out = json.loads(run_hook(payload(project), cwd=project).stdout)
    assert "no status.json" in out["systemMessage"]
    assert not (project / ".autoweb" / "active-loop.json").exists()


def test_a_status_json_older_than_the_loop_counts_as_missing(run_hook, project):
    write_loop(project, started="2026-11-01T12:00:00Z")
    write_status(project, [assertion("feed-loads")], checked_at="2026-11-01T09:00:00Z")
    out = block_of(run_hook(payload(project), cwd=project))
    assert "predates this loop" in out["reason"]
    assert (project / ".autoweb" / "active-loop.json").is_file()


# ------------------------------------------------------------------ stop_hook_active


def test_a_continuation_without_a_new_check_ends_the_loop(run_hook, project):
    """stop_hook_active: Claude came back from our block and did not re-check."""
    write_loop(project)
    write_status(project, [assertion("feed-loads", "FAIL", "authwall")])
    block_of(run_hook(payload(project), cwd=project))
    result = run_hook(payload(project, active=True), cwd=project)
    out = json.loads(result.stdout)
    assert "without a new `autoweb goal check`" in out["systemMessage"]
    assert "feed-loads [FAIL]" in out["systemMessage"]
    assert not (project / ".autoweb" / "active-loop.json").exists()


def test_a_continuation_that_rewrote_status_json_keeps_looping(run_hook, project):
    write_loop(project)
    path = write_status(project, [assertion("feed-loads", "FAIL", "authwall")])
    block_of(run_hook(payload(project), cwd=project))
    write_status(project, [assertion("feed-loads", "FAIL", "still authwall")],
                 checked_at="2026-11-01T10:45:00Z")
    os.utime(path, (time.time() + 5, time.time() + 5))
    out = block_of(run_hook(payload(project, active=True), cwd=project))
    assert out["reason"].startswith("iteration 3 of 5")
    assert "still authwall" in out["reason"]


def test_another_plugins_block_does_not_count_as_a_stall(run_hook, project):
    """stop_hook_active is also true when some other Stop hook blocked; our first
    decision in a loop has no previous block to compare against."""
    write_loop(project)
    out = block_of(run_hook(payload(project, active=True), cwd=project))
    assert out["reason"].startswith("iteration 2 of 5")


# ------------------------------------------------------------------------- sessions


def test_another_sessions_loop_is_not_this_sessions_business(run_hook, project):
    path = write_loop(project, session="sess-owner")
    before = path.read_text(encoding="utf-8")
    result = run_hook(payload(project, session="sess-other"), cwd=project)
    assert (result.returncode, result.stdout) == (0, "")
    assert path.read_text(encoding="utf-8") == before


def test_an_unsubstituted_session_placeholder_is_treated_as_unbound(run_hook, project):
    write_loop(project, session="${CLAUDE_SESSION_ID}")
    block_of(run_hook(payload(project, session="sess-9"), cwd=project))


# ----------------------------------------------------------------- malformed inputs


def test_a_malformed_loop_file_exits_silently_and_logs(run_hook, project):
    path = project / ".autoweb" / "active-loop.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    result = run_hook(payload(project), cwd=project)
    assert (result.returncode, result.stdout) == (0, "")
    log = (project / ".autoweb" / "loop.log").read_text(encoding="utf-8")
    assert "Traceback" in log and "JSONDecodeError" in log


def test_a_loop_file_with_the_wrong_shape_exits_silently_and_logs(run_hook, project):
    write_loop(project, iteration="two")
    result = run_hook(payload(project), cwd=project)
    assert (result.returncode, result.stdout) == (0, "")
    assert "iteration" in (project / ".autoweb" / "loop.log").read_text(encoding="utf-8")


def test_a_malformed_status_json_exits_silently_and_logs(run_hook, project):
    write_loop(project)
    path = project / "runs" / RUN / "status.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"done": tru', encoding="utf-8")
    result = run_hook(payload(project), cwd=project)
    assert (result.returncode, result.stdout) == (0, "")
    assert "JSONDecodeError" in (project / ".autoweb" / "loop.log").read_text(encoding="utf-8")
    assert read_loop(project)["iteration"] == 1, "nothing is counted on a failure"


def test_malformed_stdin_with_a_loop_active_exits_silently_and_logs(run_hook, project):
    write_loop(project)
    result = run_hook("{oops", cwd=project)
    assert (result.returncode, result.stdout) == (0, "")
    assert "not a JSON object" in (project / ".autoweb" / "loop.log").read_text(encoding="utf-8")
    assert read_loop(project)["iteration"] == 1


def test_stdout_is_exactly_one_json_object(run_hook, project):
    """Claude Code parses stdout as JSON only when it is one object and nothing else."""
    write_loop(project)
    write_status(project, [assertion("feed-loads", "FAIL", "x" * 5000)])
    result = run_hook(payload(project), cwd=project)
    assert result.stdout.count("\n") == 1
    out = json.loads(result.stdout)
    assert len(out["reason"]) <= 2000


def test_a_cwd_this_python_cannot_see_falls_back_to_the_process_cwd(run_hook, project):
    """Git Bash can hand a Windows Python an MSYS path such as /tmp/x."""
    write_loop(project)
    stdin = json.dumps({"session_id": "s", "cwd": "/no/such/dir/anywhere",
                        "stop_hook_active": False})
    out = block_of(run_hook(stdin, cwd=project))
    assert out["reason"].startswith("iteration 2 of 5")

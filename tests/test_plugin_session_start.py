"""scripts/session_start.py - what the SessionStart hook prints, and when.

Two properties matter more than any individual word, and both are asserted
repeatedly below:

  * SILENCE outside an AutoWeb project. The plugin is installed once per
    machine and the hook fires in every session on it, so a repo with no
    `autoweb.toml` must get nothing at all.
  * EXIT 0, always. A hook that fails is a hook that can interfere with the
    session, so a broken payload, an unparsable config and a missing project
    directory all have to end in a quiet zero.

Hermetic through tests/conftest.py: `aw_home` points AW_HOME, CLAUDE_CONFIG_DIR,
HOME and USERPROFILE at a temp directory. The last two are what make the trust
tests honest - `autoweb.lanes.workspace_is_trusted` reads `Path.home()/.claude.json`
rather than aw_common's CLAUDE_JSON, and on Windows `Path.home()` resolves
through USERPROFILE. Verified on Windows 11 (CPython 3.12): with both set, the
fake home is what `Path.home()` returns, so no test here can read or write the
developer's real `~/.claude.json`.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def generated_marker(aw) -> str:
    """`autoweb.lanes.GENERATED_MARKER`, imported the way aw_common imports it."""
    aw._package_importable()
    from autoweb.lanes import GENERATED_MARKER
    return GENERATED_MARKER


@pytest.fixture
def ss(aw_home, monkeypatch):
    """session_start, imported against the fake home, plus the helpers tests need."""
    mod = importlib.import_module("session_start")

    def run(cwd=None, payload=None):
        """Feed the hook a payload on stdin and return its exit code."""
        if isinstance(payload, str):
            raw = payload
        else:
            raw = json.dumps(payload if payload is not None else
                             {"cwd": str(cwd), "session_id": "sess-1",
                              "source": "startup"})
        monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
        return mod.main()

    def write_lane(project, index, generated=True):
        agents = project / ".claude" / "agents"
        agents.mkdir(parents=True, exist_ok=True)
        marker = generated_marker(mod.aw) + "\n" if generated else ""
        path = agents / f"lane-{index}.md"
        path.write_text(f"---\nname: lane-{index}\n---\n{marker}# Lane {index}\n",
                        encoding="utf-8", newline="\n")
        return path

    def write_goal(project, name):
        goals = project / "goals"
        goals.mkdir(parents=True, exist_ok=True)
        path = goals / name
        path.write_text("# a goal\n", encoding="utf-8", newline="\n")
        return path

    def write_trust(project, accepted, other=False):
        """`~/.claude.json` as Claude Code writes it: projects[<path>].hasTrustDialogAccepted."""
        projects = {}
        if other:
            projects[str(Path(project).parent / "somewhere-else").replace("\\", "/")] = \
                {"hasTrustDialogAccepted": True}
        if accepted is not None:
            projects[str(project).replace("\\", "/")] = \
                {"hasTrustDialogAccepted": bool(accepted)}
        path = aw_home / ".claude.json"
        path.write_text(json.dumps({"projects": projects}), encoding="utf-8",
                        newline="\n")
        return path

    return SimpleNamespace(mod=mod, aw=mod.aw, home=aw_home, run=run,
                           write_lane=write_lane, write_goal=write_goal,
                           write_trust=write_trust)


def lines_of(captured) -> list:
    return captured.out.rstrip("\n").splitlines()


# ------------------------------------------------------------------- silence

def test_a_directory_without_autoweb_toml_gets_nothing(ss, project, capsys):
    assert ss.run(project) == 0
    assert capsys.readouterr().out == ""


def test_the_payload_cwd_wins_over_the_process_cwd(ss, project, autoweb_project,
                                                   capsys, monkeypatch, tmp_path):
    """A hook's own cwd must never become a silent argument.

    The process sits inside the AutoWeb project and the payload names a plain
    directory: the answer is silence, because the payload is the session's cwd.
    """
    monkeypatch.chdir(autoweb_project)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ss.run(plain) == 0
    assert capsys.readouterr().out == ""


def test_an_autoweb_project_gets_exactly_two_lines(ss, autoweb_project, capsys):
    assert ss.run(autoweb_project) == 0
    out = capsys.readouterr().out
    assert out.endswith("\n")
    lines = out.rstrip("\n").splitlines()
    assert len(lines) == 2
    assert all(line.startswith("autoweb: ") for line in lines)


def test_stdout_stays_inside_the_budget(ss, autoweb_project, capsys):
    ss.write_lane(autoweb_project, 1)
    ss.write_goal(autoweb_project, "daily-digest.md")
    ss.write_trust(autoweb_project, True)
    ss.run(autoweb_project)
    out = capsys.readouterr().out.rstrip("\n")
    assert 0 < len(out) <= ss.mod.STDOUT_BUDGET


def test_an_oversized_block_is_truncated_rather_than_printed(ss, autoweb_project,
                                                             capsys, monkeypatch):
    monkeypatch.setattr(ss.mod, "INSTRUCTION", "autoweb: " + "x" * 900)
    ss.run(autoweb_project)
    out = capsys.readouterr().out.rstrip("\n")
    assert len(out) == ss.mod.STDOUT_BUDGET
    assert out.endswith("...")


# ------------------------------------------------------------------ identity

def test_a_missing_root_json_names_the_command_that_makes_one(ss, autoweb_project,
                                                              capsys):
    ss.run(autoweb_project)
    assert "identity missing (run: autoweb state export <url>)" in \
        lines_of(capsys.readouterr())[0]


def test_an_existing_root_json_is_reported_as_present(ss, autoweb_project, capsys):
    (autoweb_project / "root.json").write_text("{}", encoding="utf-8")
    ss.run(autoweb_project)
    assert "identity root.json present" in lines_of(capsys.readouterr())[0]


def test_the_identity_path_comes_from_the_config_not_the_default_name(ss, project,
                                                                     capsys):
    """`state.root` can be renamed, and then root.json is the wrong file to look at."""
    (project / "autoweb.toml").write_text('[state]\nroot = "identity.json"\n',
                                          encoding="utf-8", newline="\n")
    (project / "identity.json").write_text("{}", encoding="utf-8")
    ss.run(project)
    status = lines_of(capsys.readouterr())[0]
    assert "identity identity.json present" in status
    assert "root.json" not in status


# --------------------------------------------------------------------- lanes

def test_no_lane_files_names_the_command_that_makes_them(ss, autoweb_project, capsys):
    ss.run(autoweb_project)
    assert "no lanes (run: autoweb lanes sync)" in lines_of(capsys.readouterr())[0]


def test_generated_lanes_are_counted_against_the_configured_maximum(ss,
                                                                    autoweb_project,
                                                                    capsys):
    for index in (1, 2):
        ss.write_lane(autoweb_project, index)
    ss.run(autoweb_project)
    status = lines_of(capsys.readouterr())[0]
    assert "2 of 3 lanes generated" in status      # the fixture's autoweb.toml: max = 3
    assert "hand-written" not in status


def test_a_hand_written_lane_is_counted_separately(ss, autoweb_project, capsys):
    """A lane with no generator marker has no inline server named for it.

    Claude Code de-duplicates inline servers by name across concurrent
    subagents, so such a file either has no browser at all or shares a
    sibling's. It is never folded into the generated count.
    """
    ss.write_lane(autoweb_project, 1)
    ss.write_lane(autoweb_project, 2)
    ss.write_lane(autoweb_project, 3, generated=False)
    ss.run(autoweb_project)
    assert "2 of 3 lanes generated (+1 hand-written)" in lines_of(capsys.readouterr())[0]


def test_only_hand_written_lanes_still_report_zero_generated(ss, autoweb_project,
                                                             capsys):
    ss.write_lane(autoweb_project, 1, generated=False)
    ss.run(autoweb_project)
    assert "0 of 3 lanes generated (+1 hand-written)" in lines_of(capsys.readouterr())[0]


# --------------------------------------------------------------------- goals

def test_the_goal_template_is_not_a_goal(ss, autoweb_project, capsys):
    (autoweb_project / "goals").mkdir()
    (autoweb_project / "goals" / "README.md").write_text("template", encoding="utf-8")
    ss.run(autoweb_project)
    assert "0 goals" in lines_of(capsys.readouterr())[0]


def test_one_goal_is_singular_and_several_are_counted(ss, autoweb_project, capsys):
    ss.write_goal(autoweb_project, "daily-digest.md")
    (autoweb_project / "goals" / "README.md").write_text("template", encoding="utf-8")
    ss.run(autoweb_project)
    status = lines_of(capsys.readouterr())[0]
    assert "1 goal" in status
    assert "1 goals" not in status
    ss.write_goal(autoweb_project, "inbox-sweep.md")
    ss.run(autoweb_project)
    assert "2 goals" in lines_of(capsys.readouterr())[0]


# --------------------------------------------------------------------- trust

def test_a_trusted_folder_reads_trust_ok(ss, autoweb_project, capsys):
    ss.write_trust(autoweb_project, True)
    ss.run(autoweb_project)
    assert "trust ok" in lines_of(capsys.readouterr())[0]


def test_an_untrusted_folder_says_what_to_do_about_it(ss, autoweb_project, capsys):
    ss.write_trust(autoweb_project, False)
    ss.run(autoweb_project)
    assert "trust not granted (open the folder and accept the prompt)" in \
        lines_of(capsys.readouterr())[0]


def test_a_folder_claude_code_has_never_seen_is_untrusted_not_unknown(ss,
                                                                     autoweb_project,
                                                                     capsys):
    """A readable config with no entry for this folder is a definite "no"."""
    ss.write_trust(autoweb_project, None, other=True)
    ss.run(autoweb_project)
    assert "trust not granted" in lines_of(capsys.readouterr())[0]


def test_no_claude_json_at_all_reads_trust_unknown(ss, autoweb_project, capsys):
    assert not (ss.home / ".claude.json").exists()
    ss.run(autoweb_project)
    assert "trust unknown" in lines_of(capsys.readouterr())[0]


def test_an_unparsable_claude_json_reads_trust_unknown(ss, autoweb_project, capsys):
    (ss.home / ".claude.json").write_text("{nope", encoding="utf-8")
    ss.run(autoweb_project)
    assert "trust unknown" in lines_of(capsys.readouterr())[0]


def test_the_fake_home_is_what_path_home_returns(ss):
    """The guarantee the three tests above rest on, asserted once directly."""
    assert os.path.normcase(str(Path.home())) == os.path.normcase(str(ss.home))


# --------------------------------------------------------------- instructions

def test_the_instruction_line_points_at_the_goals_the_runs_and_the_skill(ss,
                                                                        autoweb_project,
                                                                        capsys):
    ss.run(autoweb_project)
    line = lines_of(capsys.readouterr())[1]
    assert "automates websites with AutoWeb" in line
    assert "goals/" in line
    assert "runs in runs/" in line
    assert "autoweb skill" in line
    assert "/autoweb:aw-run <goal>" in line


def test_the_instruction_line_carries_a_runnable_cli_command(ss, autoweb_project,
                                                             capsys):
    ss.run(autoweb_project)
    line = lines_of(capsys.readouterr())[1]
    scripts = str(SCRIPTS).replace("\\", "/")
    project = str(autoweb_project).replace("\\", "/")
    assert f'bash "{scripts}/py.sh" "{scripts}/aw.py" -C "{project}" <subcommand>' in line
    assert "\\" not in line           # backslashes do not survive a shell quote


def test_the_scripts_path_is_absolute_and_real(ss):
    assert Path(ss.mod.fwd(ss.aw.SCRIPTS_DIR)).is_absolute()
    assert (SCRIPTS / "aw.py").is_file()
    assert (SCRIPTS / "py.sh").is_file()


# ------------------------------------------------------------------ hardiness

@pytest.mark.parametrize("payload", ["", "not json", "[]", "null", "{}", "0"])
def test_a_broken_payload_exits_zero_and_says_nothing(ss, payload, capsys,
                                                      monkeypatch, tmp_path):
    """With no usable cwd the hook falls back to the process cwd, which here is
    not an AutoWeb project - so the right answer is silence and a zero."""
    monkeypatch.chdir(tmp_path)
    assert ss.run(payload=payload) == 0
    assert capsys.readouterr().out == ""


def test_an_unreadable_config_still_prints_two_lines(ss, project, capsys):
    """`autoweb.toml` is the opt-in; an invalid one does not withdraw it.

    `autoweb config check` is what explains the file. The hook drops the "of M"
    it cannot know and says the rest.
    """
    (project / "autoweb.toml").write_text("this is not toml {{{", encoding="utf-8")
    ss.write_lane(project, 1)
    assert ss.run(project) == 0
    status = lines_of(capsys.readouterr())[0]
    assert "1 lanes generated" in status
    assert " of " not in status


def test_a_cwd_that_does_not_exist_exits_zero(ss, capsys, tmp_path):
    assert ss.run(tmp_path / "gone") == 0
    assert capsys.readouterr().out == ""


def test_the_session_source_is_logged(ss, autoweb_project, capsys):
    ss.run(payload={"cwd": str(autoweb_project), "session_id": "s9",
                    "source": "resume"})
    capsys.readouterr()
    log = ss.aw.LOG_PATH.read_text(encoding="utf-8")
    assert "session start: source=resume session=s9" in log
    assert str(autoweb_project) in log


def test_nothing_is_logged_without_a_source(ss, autoweb_project, capsys):
    ss.run(payload={"cwd": str(autoweb_project)})
    capsys.readouterr()
    assert not ss.aw.LOG_PATH.exists()


def test_the_script_as_a_whole_exits_zero_on_garbage(aw_home, tmp_path):
    """The real file, in its own process, with the except/exit 0 wrapper live."""
    env = dict(os.environ)
    env["AW_HOME"] = str(aw_home)
    proc = subprocess.run([sys.executable, str(SCRIPTS / "session_start.py")],
                          input="not json at all", capture_output=True, text=True,
                          env=env, cwd=str(tmp_path), timeout=120)
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_the_hook_runs_inside_two_seconds(ss, autoweb_project, capsys):
    """No subprocess anywhere in this path: the budget is the whole point.

    Generous by 10x deliberately - this fails only if something starts shelling
    out, which is the regression worth catching.
    """
    import time
    ss.write_lane(autoweb_project, 1)
    ss.write_trust(autoweb_project, True)
    started = time.monotonic()
    ss.run(autoweb_project)
    elapsed = time.monotonic() - started
    capsys.readouterr()
    assert elapsed < 2.0

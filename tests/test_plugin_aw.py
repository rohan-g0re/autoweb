"""scripts/aw.py - the CLI launcher, both of its two paths.

The installed-binary path is always tested in a SUBPROCESS, never in process:
on POSIX it ends in `os.execv`, which would replace the pytest process with the
fake tool and take the whole session with it. The in-place path is tested both
ways - in process where a monkeypatch is the only honest way to produce the
failure, and in a subprocess where the real `autoweb` package has to do the
work.

Hermetic through tests/conftest.py: `AW_HOME` is a temp directory (so the tool
cache aw_common writes is not the developer's), `PATH` holds one directory, and
the only `autoweb` on it is a fake built by `make_tool`. Nothing here can find
real `uv`: aw_common looks for it in `$HOME/.local/bin` and on PATH, both of
which are temp directories in every call below, so the `uv tool dir` subprocess
is never reached.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
AW_PY = str(SCRIPTS / "aw.py")


def package_version() -> str:
    """`autoweb.__version__` from the copy in this repo, read without importing it."""
    text = (REPO / "autoweb" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"\'')
    raise AssertionError("autoweb/__init__.py has no __version__")


@pytest.fixture
def sub(aw_home, empty_path):
    """Run aw.py in a subprocess with a hermetic environment."""

    def _run(*argv, cwd=None):
        env = dict(os.environ)
        env["AW_HOME"] = str(aw_home)
        env["HOME"] = str(aw_home)
        env["USERPROFILE"] = str(aw_home)
        env["PATH"] = str(empty_path)
        env.pop("AW_AUTOWEB_BIN", None)
        return subprocess.run([sys.executable, AW_PY, *argv], capture_output=True,
                              text=True, env=env, cwd=str(cwd) if cwd else None,
                              timeout=180)

    return _run


@pytest.fixture
def aw_mod(aw, empty_path):
    """The aw.py module itself, imported against the fake home."""
    mod = importlib.import_module("aw")
    mod.aw._FIND_TOOL_CACHE.clear()
    return mod


# ----------------------------------------------------- the installed binary

def test_an_installed_autoweb_gets_the_arguments_unchanged(sub, empty_path,
                                                           make_tool, tmp_path):
    record = tmp_path / "argv.jsonl"
    make_tool(empty_path, "autoweb", stdout="FAKE OUTPUT\n", rc=0, record=record)
    proc = sub("-C", "/some/project", "lanes", "list", "--json")
    assert proc.returncode == 0, proc.stderr
    assert "FAKE OUTPUT" in proc.stdout
    assert json.loads(record.read_text(encoding="utf-8").splitlines()[0]) == \
        ["-C", "/some/project", "lanes", "list", "--json"]


def test_the_launcher_never_adds_an_argument_of_its_own(sub, empty_path, make_tool,
                                                        tmp_path):
    """No `-C` unless the caller gave one.

    Which project the CLI acts on is the caller's decision (aw_common.autoweb_argv
    is where it is made). A launcher that filled it in from its own cwd would turn
    a hook's working directory into a silent argument.
    """
    record = tmp_path / "argv.jsonl"
    make_tool(empty_path, "autoweb", record=record)
    proc = sub("config", "show", cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(record.read_text(encoding="utf-8").splitlines()[0]) == \
        ["config", "show"]


def test_no_arguments_at_all_are_forwarded_as_no_arguments(sub, empty_path,
                                                           make_tool, tmp_path):
    record = tmp_path / "argv.jsonl"
    make_tool(empty_path, "autoweb", record=record)
    assert sub().returncode == 0
    assert json.loads(record.read_text(encoding="utf-8").splitlines()[0]) == []


@pytest.mark.parametrize("rc", [0, 1, 2, 7])
def test_the_installed_binarys_exit_code_is_the_launchers(sub, empty_path, make_tool,
                                                          rc):
    """0 pass, 1 gate failed, 2 your files are wrong - all have to survive."""
    make_tool(empty_path, "autoweb", rc=rc)
    assert sub("config", "check").returncode == rc


def test_stderr_from_the_installed_binary_is_not_swallowed(sub, empty_path,
                                                           make_tool):
    make_tool(empty_path, "autoweb", stderr="error: bad config\n", rc=2)
    proc = sub("config", "check")
    assert proc.returncode == 2
    assert "error: bad config" in proc.stderr


def test_an_explicit_binary_path_wins(sub, aw_home, make_tool, tmp_path):
    """$AW_AUTOWEB_BIN, the escape hatch aw_common.find_tool checks first."""
    elsewhere = tmp_path / "elsewhere"
    record = tmp_path / "argv.jsonl"
    tool = make_tool(elsewhere, "autoweb", stdout="CHOSEN\n", record=record)
    env = dict(os.environ)
    env["AW_HOME"] = str(aw_home)
    env["AW_AUTOWEB_BIN"] = str(tool)
    proc = subprocess.run([sys.executable, AW_PY, "config", "show"],
                          capture_output=True, text=True, env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr
    assert "CHOSEN" in proc.stdout


# ------------------------------------------------------------- in place

def test_with_nothing_installed_the_shipped_package_answers(sub):
    proc = sub("--version")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"autoweb {package_version()}"


def test_config_check_runs_in_place_against_a_named_project(sub, autoweb_project):
    proc = sub("-C", str(autoweb_project), "config", "check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_config_show_in_place_reports_the_projects_own_values(sub, autoweb_project):
    proc = sub("-C", str(autoweb_project), "config", "show")
    assert proc.returncode == 0, proc.stderr
    assert "max                      = 3" in proc.stdout
    assert "browser                  = chromium" in proc.stdout


def test_a_broken_config_exits_two_in_place(sub, project):
    """The package's own exit codes pass through: 2 means your files are wrong."""
    (project / "autoweb.toml").write_text("[lanes]\nmax = 0\n", encoding="utf-8")
    proc = sub("-C", str(project), "config", "check")
    assert proc.returncode == 2
    assert "error:" in proc.stderr


def test_no_arguments_in_place_prints_help_and_exits_one(sub):
    proc = sub()
    assert proc.returncode == 1
    assert "usage: autoweb" in proc.stdout


def test_an_unknown_subcommand_in_place_exits_two(sub):
    """argparse raises SystemExit(2); the launcher must return it, not 0."""
    proc = sub("no-such-command")
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_the_plugin_root_leads_sys_path(aw_mod):
    """A project with an `autoweb/` of its own must not shadow the shipped one."""
    before = list(sys.path)
    try:
        aw_mod.load_cli_main()
        assert sys.path[0] == str(aw_mod.aw.PLUGIN_ROOT)
    finally:
        sys.path[:] = before


# --------------------------------------------------------------- playwright

PLAYWRIGHT_ERRORS = [
    ModuleNotFoundError("No module named 'playwright'", name="playwright"),
    ModuleNotFoundError("No module named 'playwright.sync_api'",
                        name="playwright.sync_api"),
    ImportError("cannot import name 'sync_playwright' from 'playwright.sync_api'"),
]

OTHER_ERRORS = [
    ModuleNotFoundError("No module named 'tomli'", name="tomli"),
    ImportError("cannot import name 'Config' from 'autoweb.config'"),
]


@pytest.mark.parametrize("exc", PLAYWRIGHT_ERRORS, ids=lambda e: type(e).__name__)
def test_a_playwright_import_error_is_recognised(aw_mod, exc):
    assert aw_mod.is_playwright_import_error(exc) is True


@pytest.mark.parametrize("exc", OTHER_ERRORS, ids=lambda e: str(e)[:20])
def test_any_other_import_error_is_not(aw_mod, exc):
    assert aw_mod.is_playwright_import_error(exc) is False


def test_a_missing_playwright_names_the_setup_skill_and_exits_two(aw_mod, capsys,
                                                                  monkeypatch):
    """Only `state export` and `state verify` need it, so this is advice, not a crash."""

    def raiser(_argv):
        raise ModuleNotFoundError("No module named 'playwright'", name="playwright")

    monkeypatch.setattr(aw_mod, "load_cli_main", lambda: raiser)
    assert aw_mod.run_in_place(["state", "verify", "https://example.com"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""                 # advice belongs on stderr
    assert "/autoweb:aw-setup" in captured.err
    assert len(captured.err.strip().splitlines()) == 1


def test_an_import_error_about_anything_else_keeps_its_traceback(aw_mod, monkeypatch):
    """A packaging bug must not be reported as "run setup"."""

    def raiser(_argv):
        raise ModuleNotFoundError("No module named 'tomli'", name="tomli")

    monkeypatch.setattr(aw_mod, "load_cli_main", lambda: raiser)
    with pytest.raises(ModuleNotFoundError):
        aw_mod.run_in_place(["config", "show"])


def test_a_playwright_failure_during_the_import_itself_is_also_caught(aw_mod,
                                                                     monkeypatch,
                                                                     capsys):
    def raiser():
        raise ImportError("playwright driver is not installed")

    monkeypatch.setattr(aw_mod, "load_cli_main", raiser)
    assert aw_mod.run_in_place(["state", "export"]) == 2
    assert "/autoweb:aw-setup" in capsys.readouterr().err


# ------------------------------------------------------------- exit codes

@pytest.mark.parametrize("code,expected", [(0, 0), (1, 1), (2, 2), (None, 0)])
def test_system_exit_from_argparse_becomes_the_launchers_code(aw_mod, monkeypatch,
                                                              code, expected):
    def raiser(_argv):
        raise SystemExit(code)

    monkeypatch.setattr(aw_mod, "load_cli_main", lambda: raiser)
    assert aw_mod.run_in_place(["--version"]) == expected


def test_a_system_exit_carrying_a_message_is_reported_and_fails(aw_mod, monkeypatch,
                                                                capsys):
    def raiser(_argv):
        raise SystemExit("something went wrong")

    monkeypatch.setattr(aw_mod, "load_cli_main", lambda: raiser)
    assert aw_mod.run_in_place([]) == 1
    assert "something went wrong" in capsys.readouterr().err


def test_a_cli_that_returns_none_is_a_pass(aw_mod, monkeypatch):
    monkeypatch.setattr(aw_mod, "load_cli_main", lambda: (lambda _argv: None))
    assert aw_mod.run_in_place(["config", "show"]) == 0

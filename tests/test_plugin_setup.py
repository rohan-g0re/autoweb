"""aw_setup: the table, the JSON, every probe, `--install`'s argv, `--init-project`.

Hermetic by construction. `AW_HOME` and `CLAUDE_CONFIG_DIR` are temp directories
(the `aw_home` fixture), PATH is empty (`empty_path`), and every tool is a
generated fake (`make_tool`). `PLAYWRIGHT_BROWSERS_PATH` is pointed at a temp
directory so the browser row cannot be answered by whatever this developer
happens to have downloaded, and `git_bash_candidates` is stubbed out so the row
does not depend on Git being installed. No real `uv`, `node`, `npx`, `autoweb`
or `playwright` is ever run, and nothing reaches the network.

`--install` *is* exercised here, against a fake `uv` whose only job is to record
the argv it was handed. That is the point: the two commands it runs are the
contract, and a wrong one would reinstall the wrong thing on a real machine.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 only
    import tomli as tomllib

from autoweb.config import CapsConfig, Config, LaneConfig, StateConfig
from autoweb.lanes import GENERATED_MARKER

WINDOWS = os.name == "nt"

# The labels, in order. A contract with docs/PLUGIN-DESIGN.md section 5.4: the
# aw-setup and aw-doctor skills read these rows, so renaming one is a
# documentation change and this list is where that shows up.
EXPECTED_CHECKS = [
    "python >= 3.10",
    "uv",
    "node >= 18",
    "playwright mcp 0.0.83",
    "autoweb cli",
    "playwright package",
    "browser",
    *(["git bash"] if WINDOWS else []),
    "trust",
    "lanes",
    "identity",
]

NOTE_CHECKS = {"playwright mcp 0.0.83", "trust", "lanes", "identity"}


@pytest.fixture
def s(aw, autoweb_project, empty_path, tmp_path, monkeypatch, make_tool):
    """aw_setup bound to a fake home, an empty PATH and an empty browser cache."""
    mod = importlib.import_module("aw_setup")
    bins = tmp_path / "bins"
    bins.mkdir()
    browsers = tmp_path / "ms-playwright"
    browsers.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    # Whether Git for Windows is installed is not a fact about this test.
    monkeypatch.setattr(mod, "git_bash_candidates", lambda: [])

    def install(name, **kwargs):
        """Create a fake tool and point AW_<NAME>_BIN at it, as find_tool reads."""
        path = make_tool(bins, name, **kwargs)
        key = "AW_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper() + "_BIN"
        monkeypatch.setenv(key, str(path))
        aw._FIND_TOOL_CACHE.clear()
        return path

    def on_path(name, **kwargs):
        """A fake tool reachable by name, for uv_bin(), which only asks PATH."""
        path = make_tool(bins, name, **kwargs)
        monkeypatch.setenv("PATH", str(bins))
        aw._FIND_TOOL_CACHE.clear()
        aw._UV_TOOL_BIN_CACHE.clear()
        return path

    def run(*argv):
        return mod.main([*argv, "--cwd", str(autoweb_project)])

    return SimpleNamespace(aw=aw, mod=mod, project=autoweb_project, bins=bins,
                           browsers=browsers, install=install, on_path=on_path,
                           run=run)


def report(s, capsys, *argv):
    """Run `--check --json` (or whatever is passed) and parse stdout."""
    rc = s.run(*(argv or ("--check", "--json")))
    return rc, json.loads(capsys.readouterr().out)


def row(rep, name):
    return next(c for c in rep["checks"] if c["check"] == name)


def fake_env_python(home, make_tool=None, **kwargs):
    """A uv `autoweb` tool environment under the fake home.

    `uv_tool_env_dir` looks for both of uv's default layouts whatever the OS, and
    `uv_tool_env_python` then wants `Scripts/python.exe` on Windows and
    `bin/python` elsewhere. A real interpreter cannot be faked at those exact
    names on Windows (a .exe has to be a PE), so when the file only has to
    *exist* this writes an empty one; tests that need it to run monkeypatch
    `aw_common.uv_tool_env_python` instead.
    """
    env = home / ("AppData/Roaming/uv/tools/autoweb" if WINDOWS
                  else ".local/share/uv/tools/autoweb")
    py = env / ("Scripts/python.exe" if WINDOWS else "bin/python")
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    return env, py


def write_lane(project, index, args_line=""):
    """A file that looks like one `autoweb lanes sync` wrote."""
    path = project / ".claude" / "agents" / f"lane-{index}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: lane-{index}\n{args_line}---\n\n{GENERATED_MARKER}\n",
                    encoding="utf-8")
    return path


# --------------------------------------------------------------------- the shape

def test_the_json_report_has_the_documented_shape(s, capsys):
    rc, rep = report(s, capsys)
    assert rc == 1
    assert set(rep) == {"ok", "checks"}
    assert rep["ok"] is False
    assert [c["check"] for c in rep["checks"]] == EXPECTED_CHECKS
    for check in rep["checks"]:
        assert set(check) == {"check", "ok", "detail", "fix", "level"}
        assert isinstance(check["ok"], bool)
        assert check["detail"].strip()
        expected = "note" if check["check"] in NOTE_CHECKS else "check"
        assert check["level"] == expected, check["check"]


def test_every_missing_gating_row_carries_a_fix(s, capsys):
    _, rep = report(s, capsys)
    missing = [c for c in rep["checks"] if not c["ok"] and c["level"] != "note"]
    assert missing, "with no tools installed something must be missing"
    for check in missing:
        assert check["fix"].strip(), check["check"]


def test_no_passing_row_carries_a_fix(s, capsys, monkeypatch, make_tool):
    """A non-empty `fix` means there is something to run, so a passing row has none."""
    s.on_path("uv")
    _, rep = report(s, capsys)
    assert [c["check"] for c in rep["checks"] if c["ok"] and c["fix"]] == []
    assert row(rep, "uv")["ok"] is True       # and it is a row that *has* a fix string


def test_the_table_prints_a_fix_under_every_missing_line(s, capsys):
    assert s.run("--check") == 1
    out = capsys.readouterr().out
    assert "autoweb prerequisites" in out
    assert str(s.project).replace("\\", "/") in out
    assert "[MISSING] uv" in out
    assert "fix:" in out
    assert "verdict: missing pieces above" in out


def test_check_is_the_default_and_needs_no_flag(s, capsys):
    assert s.run() == 1
    assert "autoweb prerequisites" in capsys.readouterr().out


def test_check_never_reaches_the_installer(s, monkeypatch):
    def never(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("--check must not install anything")

    monkeypatch.setattr(s.mod, "do_install", never)
    assert s.run("--check", "--json") == 1


def test_a_note_never_fails_the_run(s, capsys, monkeypatch):
    """Three unhappy NOTE rows and one good gating row: still ready, still 0."""
    monkeypatch.setattr(s.mod, "collect", lambda project: s.mod.Report(checks=[
        s.mod.Check("uv", True, "/bin/uv"),
        s.mod.Check("trust", False, "unknown", fix="accept the prompt",
                    level=s.mod.LEVEL_NOTE),
        s.mod.Check("lanes", False, "0 of 5 generated", fix="autoweb lanes sync",
                    level=s.mod.LEVEL_NOTE),
        s.mod.Check("identity", False, "root.json missing", fix="autoweb state export",
                    level=s.mod.LEVEL_NOTE),
    ]))
    assert s.run("--check") == 0
    out = capsys.readouterr().out
    assert "[NOTE   ] trust" in out
    assert "hint: accept the prompt" in out       # a note hints, it does not fix
    assert "verdict: ready" in out


# -------------------------------------------------------------- python, uv, node

def test_the_interpreter_running_the_suite_passes_the_python_check(s, capsys):
    _, rep = report(s, capsys)
    check = row(rep, "python >= 3.10")
    assert check["ok"] is True
    assert s.mod.fwd(sys.executable) in check["detail"]


def test_the_uv_fix_is_the_official_installer_for_this_os(s, capsys):
    _, rep = report(s, capsys)
    check = row(rep, "uv")
    assert check["ok"] is False
    assert check["detail"] == "not found"
    assert check["fix"] == (s.mod.UV_INSTALL_WINDOWS if WINDOWS
                            else s.mod.UV_INSTALL_POSIX)


def test_uv_is_found_on_path_with_its_location(s, capsys):
    uv = s.on_path("uv")
    _, rep = report(s, capsys)
    check = row(rep, "uv")
    assert check["ok"] is True
    # Case-insensitive: `which` on Windows returns the extension as PATHEXT
    # spells it (uv.CMD), not as the file is named.
    assert check["detail"].lower() == s.mod.fwd(uv).lower()


def test_a_new_enough_node_passes_with_its_version_and_path(s, capsys):
    node = s.install("node", stdout="v20.11.1\n")
    _, rep = report(s, capsys)
    check = row(rep, "node >= 18")
    assert check["ok"] is True
    assert "v20.11.1" in check["detail"]
    assert s.mod.fwd(node) in check["detail"]
    assert check["fix"] == ""


def test_an_old_node_fails_and_names_the_version_it_found(s, capsys):
    s.install("node", stdout="v16.20.2\n")
    _, rep = report(s, capsys)
    check = row(rep, "node >= 18")
    assert check["ok"] is False
    assert "v16.20.2" in check["detail"]
    assert "too old" in check["detail"]
    assert check["fix"] == s.mod.node_install_hint()


def test_a_node_that_cannot_run_fails_rather_than_raising(s, capsys, tmp_path,
                                                          monkeypatch):
    monkeypatch.setenv("AW_NODE_BIN", str(tmp_path / "no-such-node"))
    s.aw._FIND_TOOL_CACHE.clear()
    _, rep = report(s, capsys)
    # The override names a file that does not exist, so discovery finds nothing.
    assert row(rep, "node >= 18")["ok"] is False


def test_a_node_that_prints_nothing_fails(s, capsys):
    s.install("node", stdout="")
    _, rep = report(s, capsys)
    check = row(rep, "node >= 18")
    assert check["ok"] is False
    assert "did not run" in check["detail"] or "no version" in check["detail"]


# ------------------------------------------------------------ the npx cache note

def test_a_warm_npx_cache_is_reported_as_warm(s, capsys):
    s.install("npx", stdout="Version 0.0.83\n")
    _, rep = report(s, capsys)
    check = row(rep, "playwright mcp 0.0.83")
    assert check["level"] == "note"
    assert check["ok"] is True
    assert "cache warm" in check["detail"]
    assert "0.0.83" in check["detail"]


def test_a_cold_npx_cache_is_a_note_and_not_a_failure(s, capsys, monkeypatch):
    """The only difference a cold cache makes is one download, so it cannot fail."""
    s.install("npx", rc=1, stderr="npm error could not determine executable\n")
    monkeypatch.setattr(s.mod, "collect", lambda project: s.mod.Report(
        checks=[s.mod.mcp_cache_check("0.0.83")]))
    assert s.run("--check") == 0
    out = capsys.readouterr().out
    assert "[NOTE   ] playwright mcp 0.0.83" in out
    assert "cache cold" in out
    assert "verdict: ready" in out


def test_the_mcp_label_follows_the_version_the_project_pins(s, capsys):
    (s.project / "autoweb.toml").write_text(
        '[lanes]\nmcp_version = "0.0.99"\n', encoding="utf-8", newline="\n")
    _, rep = report(s, capsys)
    names = [c["check"] for c in rep["checks"]]
    assert "playwright mcp 0.0.99" in names
    assert "playwright mcp 0.0.83" not in names


def test_the_pinned_version_defaults_to_the_packages_own(s):
    assert s.mod.pinned_mcp_version(None) == LaneConfig().mcp_version


# ------------------------------------------------------- the CLI and its package

def test_an_installed_autoweb_binary_is_reported_with_its_path(s, capsys):
    exe = s.install("autoweb")
    _, rep = report(s, capsys)
    check = row(rep, "autoweb cli")
    assert check["ok"] is True
    assert check["detail"] == s.mod.fwd(exe)


def test_no_binary_is_ok_because_the_plugin_runs_in_place(s, capsys):
    _, rep = report(s, capsys)
    check = row(rep, "autoweb cli")
    assert check["ok"] is True
    assert "running in place from the plugin" in check["detail"]
    assert s.mod.fwd(s.aw.PLUGIN_ROOT) in check["detail"]


def test_playwright_is_missing_when_the_tool_environment_is_absent(s, capsys):
    _, rep = report(s, capsys)
    check = row(rep, "playwright package")
    assert check["ok"] is False
    assert check["fix"] == s.mod.INSTALL_FIX == "run --install"
    assert "state export" in check["detail"]        # it gates only those two commands


def test_a_tool_environment_whose_python_cannot_run_is_missing(s, aw_home, capsys):
    env, py = fake_env_python(aw_home)
    assert s.aw.uv_tool_env_dir() == env
    assert s.aw.uv_tool_env_python() == py
    _, rep = report(s, capsys)
    check = row(rep, "playwright package")
    assert check["ok"] is False
    assert s.mod.fwd(py) in check["detail"]
    assert check["fix"] == "run --install"


def test_playwright_passes_when_the_tool_python_reports_a_version(s, capsys,
                                                                  monkeypatch,
                                                                  make_tool):
    py = make_tool(s.bins, "toolpython", stdout="1.55.0\n")
    monkeypatch.setattr(s.aw, "uv_tool_env_python", lambda: Path(py))
    _, rep = report(s, capsys)
    check = row(rep, "playwright package")
    assert check["ok"] is True
    assert "playwright 1.55.0" in check["detail"]
    assert check["fix"] == ""


def test_the_playwright_probe_asks_importlib_not_a_dunder(s):
    """playwright has no __version__; the obvious one-liner raises AttributeError."""
    assert "importlib.metadata" in s.mod.PLAYWRIGHT_PROBE
    assert "__version__" not in s.mod.PLAYWRIGHT_PROBE


# ------------------------------------------------------------------- the browser

def test_a_downloaded_browser_is_found_in_the_cache(s, capsys):
    (s.browsers / "chromium-1243").mkdir()
    _, rep = report(s, capsys)
    check = row(rep, "browser")
    assert check["ok"] is True
    assert "chromium-1243" in check["detail"]
    assert s.mod.fwd(s.browsers) in check["detail"]


def test_an_empty_cache_is_missing_and_points_at_install(s, capsys):
    _, rep = report(s, capsys)
    check = row(rep, "browser")
    assert check["ok"] is False
    assert "no chromium-*" in check["detail"]
    assert "--install" in check["fix"]


def test_the_headless_shell_alone_is_not_a_browser(s, capsys):
    """`chromium_headless_shell-*` cannot run a headed lane, so it must not pass."""
    (s.browsers / "chromium_headless_shell-1243").mkdir()
    _, rep = report(s, capsys)
    assert row(rep, "browser")["ok"] is False


def test_the_cache_directory_honours_playwright_browsers_path(s, monkeypatch,
                                                              tmp_path):
    wanted = tmp_path / "elsewhere"
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(wanted))
    assert s.mod.browsers_cache_dir() == wanted
    # "0" means "beside the package", which is not a directory to look in.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    assert s.mod.browsers_cache_dir() != Path("0")


def test_the_default_cache_directory_is_the_documented_one_per_os(s, aw_home,
                                                                  monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    if WINDOWS:
        monkeypatch.setenv("LOCALAPPDATA", str(aw_home / "AppData" / "Local"))
        expected = aw_home / "AppData" / "Local" / "ms-playwright"
    elif sys.platform == "darwin":
        expected = aw_home / "Library" / "Caches" / "ms-playwright"
    else:
        expected = aw_home / ".cache" / "ms-playwright"
    assert s.mod.browsers_cache_dir() == expected


def test_a_channel_browser_is_looked_for_where_its_installer_puts_it(s, capsys,
                                                                     monkeypatch,
                                                                     tmp_path):
    (s.project / "autoweb.toml").write_text(
        '[lanes]\nbrowser = "chrome"\n', encoding="utf-8", newline="\n")
    chrome = tmp_path / "Chrome" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("", encoding="utf-8")
    monkeypatch.setattr(s.mod, "channel_paths", lambda browser: [chrome])
    _, rep = report(s, capsys)
    check = row(rep, "browser")
    assert check["ok"] is True
    assert check["detail"] == f"chrome at {s.mod.fwd(chrome)}"


def test_a_missing_channel_browser_offers_chromium_instead(s, capsys, monkeypatch,
                                                           tmp_path):
    (s.project / "autoweb.toml").write_text(
        '[lanes]\nbrowser = "chrome"\n', encoding="utf-8", newline="\n")
    monkeypatch.setattr(s.mod, "channel_paths",
                        lambda browser: [tmp_path / "nowhere" / "chrome.exe"])
    monkeypatch.setattr(s.mod, "channel_on_path", lambda browser: None)
    _, rep = report(s, capsys)
    check = row(rep, "browser")
    assert check["ok"] is False
    assert "chrome" in check["detail"]
    assert 'lanes.browser = "chromium"' in check["fix"]


def test_a_channel_browser_may_also_be_on_path(s, capsys, monkeypatch):
    (s.project / "autoweb.toml").write_text(
        '[lanes]\nbrowser = "chrome"\n', encoding="utf-8", newline="\n")
    monkeypatch.setattr(s.mod, "channel_paths", lambda browser: [])
    monkeypatch.setattr(s.mod, "channel_on_path",
                        lambda browser: "/usr/bin/google-chrome")
    _, rep = report(s, capsys)
    assert row(rep, "browser")["ok"] is True


@pytest.mark.parametrize("configured,requested,expected", [
    ("chromium", None, "chromium"),
    ("chrome", None, "chrome"),
    ("chrome", "firefox", "firefox"),
    ("chrome", "FIREFOX", "firefox"),
    (None, None, "chromium"),
    ("chrome", "nonsense", "chrome"),
])
def test_resolve_browser_prefers_the_flag_then_the_config(s, configured, requested,
                                                          expected):
    cfg = None
    if configured:
        cfg = SimpleNamespace(lanes=SimpleNamespace(browser=configured))
    assert s.mod.resolve_browser(cfg, requested) == expected


def test_the_browser_flags_are_rejected_outside_install(s):
    for argv in (("--browser", "chromium"), ("--skip-browser",)):
        with pytest.raises(SystemExit) as exc:
            s.run("--check", *argv)
        assert exc.value.code == 2


def test_an_unknown_browser_name_is_rejected(s):
    with pytest.raises(SystemExit) as exc:
        s.run("--install", "--browser", "nonsense")
    assert exc.value.code == 2


# ------------------------------------------------------------------- git bash

@pytest.mark.skipif(not WINDOWS, reason="the row exists on Windows only")
def test_the_git_bash_row_is_windows_only(s, capsys):
    _, rep = report(s, capsys)
    assert "git bash" in [c["check"] for c in rep["checks"]]


@pytest.mark.skipif(WINDOWS, reason="the row exists on Windows only")
def test_there_is_no_git_bash_row_off_windows(s, capsys):
    _, rep = report(s, capsys)
    assert "git bash" not in [c["check"] for c in rep["checks"]]


def test_git_bash_is_found_when_one_exists(s, monkeypatch, tmp_path):
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.write_text("", encoding="utf-8")
    monkeypatch.setattr(s.mod, "git_bash_candidates", lambda: [bash])
    check = s.mod.git_bash_check()
    assert check.ok is True
    assert check.detail == s.mod.fwd(bash)


def test_the_windowsapps_wsl_launcher_is_not_git_bash(s, monkeypatch, tmp_path):
    """Finding the WSL stub and calling it Git Bash is worse than finding nothing."""
    shim = tmp_path / "WindowsApps" / "bash.exe"
    shim.parent.mkdir(parents=True)
    shim.write_text("", encoding="utf-8")
    monkeypatch.setattr(s.mod, "git_bash_candidates", lambda: [shim])
    check = s.mod.git_bash_check()
    assert check.ok is False
    assert "WSL" in check.detail
    assert "Git for Windows" in check.fix


def test_a_real_git_bash_wins_over_a_shim_whatever_the_order(s, monkeypatch,
                                                             tmp_path):
    shim = tmp_path / "WindowsApps" / "bash.exe"
    real = tmp_path / "Git" / "usr" / "bin" / "bash.exe"
    for path in (shim, real):
        path.parent.mkdir(parents=True)
        path.write_text("", encoding="utf-8")
    monkeypatch.setattr(s.mod, "git_bash_candidates", lambda: [shim, real])
    assert s.mod.git_bash_check().detail == s.mod.fwd(real)


@pytest.mark.parametrize("path,shim", [
    (r"C:\Windows\System32\bash.exe", True),
    (r"C:\Users\example\AppData\Local\Microsoft\WindowsApps\bash.exe", True),
    (r"C:\Program Files\Git\bin\bash.exe", False),
    ("/usr/bin/bash", False),
])
def test_is_wsl_shim(s, path, shim):
    assert s.mod.is_wsl_shim(path) is shim


# ---------------------------------------------------------- trust, lanes, identity

def write_trust(home, project, accepted):
    (home / ".claude.json").write_text(
        json.dumps({"projects": {str(Path(project).resolve().as_posix()):
                                 {"hasTrustDialogAccepted": accepted}}}),
        encoding="utf-8")


def test_trust_is_unknown_without_a_claude_json(s, capsys):
    _, rep = report(s, capsys)
    check = row(rep, "trust")
    assert check["level"] == "note"
    assert check["ok"] is False
    assert "unknown" in check["detail"]


def test_trust_granted_is_reported_granted(s, aw_home, capsys):
    write_trust(aw_home, s.project, True)
    _, rep = report(s, capsys)
    check = row(rep, "trust")
    assert check["ok"] is True
    assert check["detail"] == "granted"


def test_trust_refused_says_the_skip_is_silent(s, aw_home, capsys):
    write_trust(aw_home, s.project, False)
    _, rep = report(s, capsys)
    check = row(rep, "trust")
    assert check["ok"] is False
    assert "not granted" in check["detail"]
    assert "silent" in check["detail"]
    assert check["fix"] == s.mod.TRUST_FIX


def test_the_lanes_note_counts_generated_against_the_ceiling(s, capsys):
    _, rep = report(s, capsys)
    check = row(rep, "lanes")
    assert check["level"] == "note"
    assert check["detail"] == "0 of 3 generated"       # the fixture sets max = 3
    assert check["fix"] == "autoweb lanes sync"


def test_a_full_set_of_lanes_satisfies_the_note(s, capsys):
    for index in (1, 2, 3):
        write_lane(s.project, index)
    _, rep = report(s, capsys)
    check = row(rep, "lanes")
    assert check["ok"] is True
    assert check["detail"] == "3 of 3 generated"
    assert check["fix"] == ""


def test_hand_written_lanes_are_counted_separately(s, capsys):
    write_lane(s.project, 1)
    hand = s.project / ".claude" / "agents" / "lane-9.md"
    hand.write_text("---\nname: lane-9\n---\n\nmine, not generated\n",
                    encoding="utf-8")
    _, rep = report(s, capsys)
    detail = row(rep, "lanes")["detail"]
    assert "1 of 3 generated" in detail
    assert "+1 hand-written" in detail


def test_a_profile_outside_the_project_is_reported_as_the_one_leak(s, capsys,
                                                                   tmp_path):
    shared = tmp_path / "shared-profile"
    write_lane(s.project, 1, args_line=(
        f'args: ["@playwright/mcp@0.0.83", "--user-data-dir", '
        f'"{shared.as_posix()}"]\n'))
    _, rep = report(s, capsys)
    check = row(rep, "lanes")
    assert check["ok"] is False
    assert "lane-1.md points --user-data-dir outside the project" in check["detail"]
    assert shared.as_posix() in check["detail"]
    assert "another project" in check["fix"]


def test_a_profile_inside_the_project_is_not_a_leak(s, capsys):
    inside = (s.project / ".autoweb" / "profiles" / "lane-1").as_posix()
    write_lane(s.project, 1, args_line=(
        f'args: ["@playwright/mcp@0.0.83", "--user-data-dir", "{inside}"]\n'))
    _, rep = report(s, capsys)
    assert "outside the project" not in row(rep, "lanes")["detail"]


def test_the_equals_spelling_of_a_leaked_profile_is_found_too(s, capsys, tmp_path):
    shared = tmp_path / "shared2"
    write_lane(s.project, 1,
               args_line=f'args: ["--user-data-dir={shared.as_posix()}"]\n')
    _, rep = report(s, capsys)
    assert "outside the project" in row(rep, "lanes")["detail"]


def test_the_identity_note_names_root_json_and_how_to_make_one(s, capsys):
    _, rep = report(s, capsys)
    check = row(rep, "identity")
    assert check["level"] == "note"
    assert check["ok"] is False
    assert "root.json missing" in check["detail"]
    assert check["fix"] == "autoweb state export <url>"


def test_an_existing_root_json_satisfies_the_identity_note(s, capsys):
    (s.project / "root.json").write_text('{"cookies": []}', encoding="utf-8")
    _, rep = report(s, capsys)
    check = row(rep, "identity")
    assert check["ok"] is True
    assert "present" in check["detail"]


def test_the_identity_note_follows_state_root(s, capsys):
    (s.project / "autoweb.toml").write_text(
        '[state]\nroot = "identity/root.json"\n', encoding="utf-8", newline="\n")
    _, rep = report(s, capsys)
    assert "identity/root.json" in row(rep, "identity")["detail"].replace("\\", "/")


# ----------------------------------------------------------------- the exit codes

def test_every_gating_check_can_pass(s, capsys, monkeypatch, make_tool, tmp_path):
    """All gates green while every NOTE is unhappy: exit 0, because notes do not gate."""
    s.on_path("uv")                                  # uv_bin only asks PATH
    s.install("node", stdout="v20.11.1\n")
    s.install("npx", rc=1)                           # a cold cache must not matter
    s.install("autoweb", stdout="autoweb 0.2.0\n")
    py = make_tool(s.bins, "toolpython", stdout="1.55.0\n")
    monkeypatch.setattr(s.aw, "uv_tool_env_python", lambda: Path(py))
    (s.browsers / "chromium-1243").mkdir()
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.write_text("", encoding="utf-8")
    monkeypatch.setattr(s.mod, "git_bash_candidates", lambda: [bash])

    rc, rep = report(s, capsys)
    assert [c["check"] for c in rep["checks"]
            if not c["ok"] and c["level"] != "note"] == []
    assert rep["ok"] is True
    assert rc == 0
    # ... and the notes really were unhappy, so this proves the level, not luck.
    assert [c["check"] for c in rep["checks"] if not c["ok"]] == [
        "playwright mcp 0.0.83", "trust", "lanes", "identity"]


# --------------------------------------------------------------------- --install

@pytest.fixture
def installer(s, monkeypatch, make_tool, tmp_path):
    """A fake uv and a fake tool-env python, both recording the argv they get."""
    uv_log = tmp_path / "uv-argv.jsonl"
    py_log = tmp_path / "py-argv.jsonl"
    s.on_path("uv", record=uv_log)
    s.install("autoweb", stdout="autoweb 0.2.0\n")
    py = make_tool(s.bins, "toolpython", stdout="1.55.0\n", record=py_log)
    monkeypatch.setattr(s.aw, "uv_tool_env_python", lambda: Path(py))

    def argvs(log):
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line]

    return SimpleNamespace(uv=lambda: argvs(uv_log), py=lambda: argvs(py_log))


def test_install_installs_the_plugin_root_as_a_uv_tool(s, installer, capsys):
    s.run("--install")
    assert ["tool", "install", "--force", str(s.aw.PLUGIN_ROOT)] in installer.uv()


def test_install_downloads_the_browser_with_the_tool_environments_python(
        s, installer, capsys):
    s.run("--install")
    assert ["-m", "playwright", "install", "chromium"] in installer.py()


def test_install_honours_the_browser_flag(s, installer, capsys):
    s.run("--install", "--browser", "firefox")
    assert ["-m", "playwright", "install", "firefox"] in installer.py()


def test_skip_browser_installs_the_package_and_nothing_else(s, installer, capsys):
    s.run("--install", "--skip-browser")
    assert ["tool", "install", "--force", str(s.aw.PLUGIN_ROOT)] in installer.uv()
    # The tool python is still asked for playwright's version by every --check,
    # so the statement is that it was never asked to download anything.
    assert [argv for argv in installer.py() if "install" in argv] == []
    assert "--skip-browser" in capsys.readouterr().out


def test_the_install_summary_goes_to_stdout_and_the_tool_output_to_stderr(
        s, installer, capsys):
    s.run("--install")
    captured = capsys.readouterr()
    assert "install summary:" in captured.out
    assert "autoweb --version: autoweb 0.2.0" in captured.out
    # The banners stream() prints belong with the tool output, not with the summary.
    assert "-> install the autoweb package" in captured.err
    assert "-> install the autoweb package" not in captured.out


def test_install_with_json_prints_one_document_carrying_the_summary(s, installer,
                                                                   capsys):
    s.run("--install", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"ok", "checks", "install"}
    assert any("uv tool install --force" in line for line in payload["install"])


def test_install_without_uv_prints_the_official_one_liner_and_fails(s, capsys):
    assert s.run("--install") == 1
    captured = capsys.readouterr()
    hint = s.mod.uv_install_hint()
    assert hint in captured.out or hint in captured.err
    assert "uv is missing" in captured.out


def test_install_forgets_the_cached_autoweb_binary(s, aw_home):
    """A path cached before the install can name a binary uv has just replaced."""
    s.aw.write_json_atomic(s.aw.TOOLS_CACHE_PATH,
                           {"autoweb": "C:/stale/autoweb.exe", "node": "/keep/node"})
    s.aw._FIND_TOOL_CACHE["autoweb"] = "C:/stale/autoweb.exe"
    s.mod.forget_autoweb_binary()
    assert "autoweb" not in s.aw._FIND_TOOL_CACHE
    cached = s.aw.read_json(s.aw.TOOLS_CACHE_PATH, {})
    assert "autoweb" not in cached
    assert cached["node"] == "/keep/node"       # only our own entry is dropped


def test_install_writes_no_settings_file(s, installer, aw_home):
    """The lane grants belong to `autoweb lanes sync`; setup touches no settings."""
    s.run("--install")
    assert not (s.project / ".claude" / "settings.local.json").exists()
    assert not (aw_home / ".claude" / "settings.json").exists()


# ---------------------------------------------------------------- --init-project

@pytest.fixture
def fresh(s, project, tmp_path):
    """A directory that is not yet an AutoWeb project, for --init-project."""
    target = tmp_path / "newproj"
    target.mkdir()
    return SimpleNamespace(
        path=target,
        run=lambda *argv: s.mod.main([*argv, "--cwd", str(target)]),
        mod=s.mod)


def test_init_writes_a_config_whose_defaults_are_the_packages_own(fresh):
    assert fresh.run("--init-project") == 0
    text = (fresh.path / "autoweb.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    lanes, state, caps = LaneConfig(), StateConfig(), CapsConfig()
    assert data["lanes"] == {"max": lanes.max, "browser": lanes.browser,
                             "mcp_version": lanes.mcp_version,
                             "isolated": lanes.isolated}
    assert data["state"] == {"root": state.root, "indexeddb": state.indexeddb}
    assert data["caps"] == {"total_bytes": caps.total_bytes,
                            "max_origins": caps.max_origins,
                            "max_indexeddb_per_origin": caps.max_indexeddb_per_origin}


def test_the_generated_config_loads_through_the_packages_own_loader(fresh):
    """The strongest statement available: the loader accepts it and changes nothing."""
    fresh.run("--init-project")
    cfg = Config.load(fresh.path)
    assert cfg.lanes == LaneConfig()
    assert cfg.state == StateConfig()
    assert cfg.caps == CapsConfig()
    assert cfg.origins == {}


def test_the_generated_config_carries_the_comments_that_explain_it(fresh):
    fresh.run("--init-project")
    text = (fresh.path / "autoweb.toml").read_text(encoding="utf-8")
    assert "# AutoWeb configuration." in text
    assert "ceiling on concurrent lanes, never a target" in text
    assert "nonexistent for ARM Linux" in text
    assert "false quietly loses Firebase/Supabase/Auth0 logins" in text
    assert "A safety valve, not a selection policy" in text
    assert '# [origins."example.com"]' in text


@pytest.fixture
def plugin_goals(s, monkeypatch, tmp_path):
    """A fake plugin root holding the goal template, so the copy is hermetic."""
    root = tmp_path / "plugin"
    (root / "goals").mkdir(parents=True)
    template = root / "goals" / "README.md"
    template.write_text("# Goal files\n\nAn assertion or it is not a goal.\n",
                        encoding="utf-8")
    monkeypatch.setattr(s.aw, "PLUGIN_ROOT", root)
    return template


def test_init_creates_the_goals_directory(fresh):
    fresh.run("--init-project")
    assert (fresh.path / "goals").is_dir()


def test_init_copies_the_plugins_goal_template(fresh, plugin_goals, capsys):
    fresh.run("--init-project")
    copied = fresh.path / "goals" / "README.md"
    assert copied.read_bytes() == plugin_goals.read_bytes()
    assert "copied goals/README.md" in capsys.readouterr().out


def test_init_never_overwrites_an_existing_goal_template(fresh, plugin_goals,
                                                         capsys):
    goals = fresh.path / "goals"
    goals.mkdir()
    (goals / "README.md").write_text("my own notes\n", encoding="utf-8")
    fresh.run("--init-project")
    assert (goals / "README.md").read_text(encoding="utf-8") == "my own notes\n"
    assert "already there, left alone" in capsys.readouterr().out


def test_init_says_so_when_the_plugin_has_no_template(fresh, s, monkeypatch,
                                                      tmp_path, capsys):
    monkeypatch.setattr(s.aw, "PLUGIN_ROOT", tmp_path / "empty-plugin")
    fresh.run("--init-project")
    assert (fresh.path / "goals").is_dir()
    assert not (fresh.path / "goals" / "README.md").exists()
    assert "no goal template at" in capsys.readouterr().out


def test_init_says_what_it_did(fresh, capsys):
    fresh.run("--init-project")
    out = capsys.readouterr().out
    assert "wrote autoweb.toml" in out
    assert "created goals/" in out


def test_init_with_json_lists_its_actions(fresh, capsys):
    assert fresh.run("--init-project", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["project"] == fresh.mod.fwd(fresh.path)
    assert any("autoweb.toml" in line for line in payload["actions"])


def test_init_extends_the_local_git_excludes(fresh):
    (fresh.path / ".git" / "info").mkdir(parents=True)
    fresh.run("--init-project")
    lines = (fresh.path / ".git" / "info" / "exclude").read_text(
        encoding="utf-8").splitlines()
    for wanted in fresh.mod.EXCLUDE_LINES:
        assert wanted in lines
    assert ".claude/agents/lane-*.md" in lines
    assert ".claude/settings.local.json" in lines


def test_init_keeps_what_the_excludes_file_already_had(fresh):
    info = fresh.path / ".git" / "info"
    info.mkdir(parents=True)
    (info / "exclude").write_text("# mine\n*.log\nruns/\n", encoding="utf-8")
    fresh.run("--init-project")
    lines = (info / "exclude").read_text(encoding="utf-8").splitlines()
    assert "# mine" in lines
    assert "*.log" in lines
    assert lines.count("runs/") == 1                  # already there, not repeated
    assert ".autoweb/" in lines


def test_init_never_touches_the_repositorys_gitignore(fresh):
    (fresh.path / ".git" / "info").mkdir(parents=True)
    gitignore = fresh.path / ".gitignore"
    gitignore.write_text("# committed, and not ours\n", encoding="utf-8")
    fresh.run("--init-project")
    assert gitignore.read_text(encoding="utf-8") == "# committed, and not ours\n"


def test_init_creates_no_gitignore_where_there_was_none(fresh):
    (fresh.path / ".git").mkdir()
    fresh.run("--init-project")
    assert not (fresh.path / ".gitignore").exists()


def test_init_works_in_a_folder_that_is_not_a_repository(fresh, capsys):
    assert fresh.run("--init-project") == 0
    assert (fresh.path / "autoweb.toml").is_file()
    assert "no .git" in capsys.readouterr().out
    assert not (fresh.path / ".git").exists()


def test_init_follows_a_worktrees_gitdir_pointer(fresh, tmp_path):
    real = tmp_path / "real-git-dir"
    (real / "info").mkdir(parents=True)
    (fresh.path / ".git").write_text(f"gitdir: {real.as_posix()}\n", encoding="utf-8")
    fresh.run("--init-project")
    lines = (real / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    assert ".autoweb/" in lines


def test_init_is_idempotent(fresh, capsys):
    (fresh.path / ".git" / "info").mkdir(parents=True)
    assert fresh.run("--init-project") == 0
    capsys.readouterr()
    before = {p: p.read_bytes() for p in (
        fresh.path / "autoweb.toml", fresh.path / ".git" / "info" / "exclude")}

    assert fresh.run("--init-project") == 0
    out = capsys.readouterr().out
    assert "already there, left alone" in out
    assert "goals/ already there" in out
    assert f"all {len(fresh.mod.EXCLUDE_LINES)} lines already there" in out
    for path, content in before.items():
        assert path.read_bytes() == content


def test_init_leaves_an_existing_config_alone(fresh):
    (fresh.path / "autoweb.toml").write_text("[lanes]\nmax = 2\n", encoding="utf-8")
    fresh.run("--init-project")
    assert (fresh.path / "autoweb.toml").read_text(encoding="utf-8") == \
        "[lanes]\nmax = 2\n"


def test_init_then_check_reports_on_the_folder_it_just_made(fresh, capsys):
    rc = fresh.run("--init-project", "--check")
    captured = capsys.readouterr()
    assert rc == 1                                    # nothing is installed here
    assert "autoweb prerequisites" in captured.out
    assert "wrote autoweb.toml" in captured.err       # the init narration moves aside
    assert "0 of %d generated" % LaneConfig().max in captured.out

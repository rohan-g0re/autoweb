"""Shared fixtures for the plugin-layer tests (tests/test_plugin_*.py).

The package tests (test_cli.py, test_config.py, ...) do not use these and are
unaffected: nothing here is autouse.

Guarantees every plugin test relies on:

  * `AW_HOME` and `CLAUDE_CONFIG_DIR` point at a temp dir, so nothing touches the
    developer's real ~/.autoweb-plugin, ~/.claude or ~/.claude.json.
  * `aw_common` is imported fresh per test, after the env is set, so its
    module-level paths are the temp ones.
  * No real `autoweb`, `uv`, `node` or `npx` is ever run. Tools are fake
    scripts created by `make_tool` on an otherwise empty PATH.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
WINDOWS = os.name == "nt"

_PLUGIN_MODULES = ("aw_common", "session_start", "aw_setup", "aw")


def _purge_plugin_modules() -> None:
    for name in _PLUGIN_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture
def aw_home(tmp_path, monkeypatch):
    """A throwaway home: AW_HOME, CLAUDE_CONFIG_DIR, HOME and USERPROFILE all
    point under tmp_path. Returns the fake home directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AW_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in list(os.environ):
        if key.startswith("AW_") and key.endswith("_BIN"):
            monkeypatch.delenv(key, raising=False)
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    _purge_plugin_modules()
    yield home
    _purge_plugin_modules()


@pytest.fixture
def aw(aw_home):
    """The freshly imported aw_common module, bound to the fake home."""
    mod = importlib.import_module("aw_common")
    mod._FIND_TOOL_CACHE.clear()
    mod._UV_TOOL_BIN_CACHE.clear()
    return mod


@pytest.fixture
def project(tmp_path):
    """A bare project directory (not yet an AutoWeb project)."""
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.fixture
def autoweb_project(project):
    """A project directory that opted in: it holds a default `autoweb.toml`."""
    (project / "autoweb.toml").write_text(
        '[lanes]\nmax = 3\nbrowser = "chromium"\nisolated = true\n'
        '[state]\nroot = "root.json"\n', encoding="utf-8", newline="\n")
    return project


@pytest.fixture
def empty_path(tmp_path, monkeypatch):
    """An empty PATH so only fake tools can be found."""
    d = tmp_path / "emptypath"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    return d


@pytest.fixture
def make_tool():
    """Create a runnable fake CLI tool and return its path.

    On Windows the launcher is a .cmd (CreateProcess runs those through the
    command interpreter); elsewhere a /bin/sh script with the exec bit. Either
    way the work is done by a generated Python file run with sys.executable, so
    the fake needs nothing on PATH. `body` is extra Python appended after the
    canned output, with `sys`, `os`, `json` imported and `REC` (record path)
    in scope.
    """

    def _make(directory, name, *, stdout="", stderr="", rc=0, record=None, body=""):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        impl = directory / ("_%s_impl.py" % name.replace("-", "_").replace(".", "_"))
        impl.write_text(
            "import json, os, sys\n"
            "REC = %r\n"
            "OUT = %r\n"
            "ERR = %r\n"
            "RC = %r\n"
            "if REC:\n"
            "    with open(REC, 'a', encoding='utf-8') as fh:\n"
            "        fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if OUT:\n"
            "    sys.stdout.write(OUT)\n"
            "if ERR:\n"
            "    sys.stderr.write(ERR)\n"
            "%s\n"
            "sys.exit(RC)\n"
            % (str(record) if record else "", stdout, stderr, int(rc), body or "pass"),
            encoding="utf-8",
            newline="\n",
        )
        if WINDOWS:
            launcher = directory / f"{name}.cmd"
            launcher.write_text(
                '@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, impl),
                encoding="utf-8",
            )
        else:
            launcher = directory / name
            launcher.write_text(
                '#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, impl),
                encoding="utf-8",
                newline="\n",
            )
            launcher.chmod(0o755)
        return launcher

    return _make

"""Shared fixtures: a clean environment, a throwaway project, and the hook run as it
ships. Paths, the bash lookup and the plain helpers are in `loop_helpers.py`.

Every hook test runs `bash scripts/py.sh scripts/loop_stop.py` as a subprocess with
crafted stdin, because that is the command `hooks/hooks.json` names. A unit test of
`decide()` would miss the launcher, the import path and the exit code, which are
the parts that hold a user in their session when they are wrong.

Hermetic: `AW_HOME` is a temp directory, so `py.sh` never reads or writes the
developer's `~/.autoweb-plugin/python_path`; `AW_PYTHON` names the interpreter
running pytest, so no test can reach `uv` or download a Python;
`CLAUDE_CONFIG_DIR` points at a temp directory holding a fake plugin registry.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from loop_helpers import AW_LOOP, BASH, LOOP_STOP, PY_SH, fwd


@pytest.fixture
def env(tmp_path):
    """The environment every subprocess gets, with nothing of the developer's."""
    home = tmp_path / "awhome"
    home.mkdir()
    config = tmp_path / "claude-config"
    config.mkdir()
    out = dict(os.environ)
    for key in ("AW_PYTHON", "AW_HOME", "CLAUDE_CONFIG_DIR", "AW_LOOP_AUTOWEB_ROOT"):
        out.pop(key, None)
    out.update({"AW_HOME": fwd(home), "AW_PYTHON": fwd(sys.executable),
                "CLAUDE_CONFIG_DIR": fwd(config)})
    return out


@pytest.fixture
def project(tmp_path):
    """An AutoWeb project with one goal."""
    root = tmp_path / "proj"
    (root / "goals").mkdir(parents=True)
    (root / "autoweb.toml").write_text("", encoding="utf-8")
    (root / "goals" / "feed.md").write_text("# feed\n", encoding="utf-8")
    return root


@pytest.fixture
def run_hook(env):
    """Run the Stop hook exactly as hooks/hooks.json does."""
    if BASH is None:
        pytest.skip("no POSIX bash on this machine")

    def _run(stdin: str, cwd=None):
        return subprocess.run([BASH, PY_SH, LOOP_STOP], input=stdin, capture_output=True,
                              text=True, env=env, cwd=cwd, timeout=60)

    return _run


@pytest.fixture
def autoweb_plugin(tmp_path, env):
    """A fake installed `autoweb` plugin, registered the way Claude Code registers one."""
    root = tmp_path / "plugins-cache" / "autoweb" / "autoweb" / "0.3.0"
    skill = root / "skills" / "aw-run" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: aw-run\n---\n", encoding="utf-8")
    registry = Path(env["CLAUDE_CONFIG_DIR"]) / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"version": 2, "plugins": {"autoweb@autoweb": [
        {"scope": "user", "installPath": str(root), "version": "0.3.0"}]}}),
        encoding="utf-8")
    return root


@pytest.fixture
def aw_loop(env):
    """Run scripts/aw_loop.py through py.sh, as the skill does."""
    if BASH is None:
        pytest.skip("no POSIX bash on this machine")

    def _run(*args, cwd=None, extra_env=None):
        e = dict(env)
        e.update(extra_env or {})
        return subprocess.run([BASH, PY_SH, AW_LOOP, *args], capture_output=True,
                              text=True, env=e, cwd=cwd, timeout=60)

    return _run

"""scripts/py.sh, hooks/hooks.json and .gitattributes - the launch path.

py.sh is exercised through a real bash, because every bug it exists to prevent
is a bug in how a shell splits words: a path with a space, a cache line read
back wrong, a Windows-format PATH that makes `command -v python` find nothing.
A unit test of the Python inside it would prove none of that.

Hermetic: `AW_HOME` is a temp directory in every call, so nothing reads or
writes the developer's `~/.autoweb-plugin`. The tests that need an interpreter
either name one explicitly (`AW_PYTHON`), seed the cache with the one running
pytest, or put exactly one directory on `PATH` - so no test can reach real `uv`,
and none can download a Python.

`make_sh_script` is local to this file: tests/conftest.py ships `make_tool`,
which builds a CLI whose work is done by a generated Python file, and these
tests need the opposite - a one-line sh script that is NOT a working Python.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
PY_SH_PATH = SCRIPTS / "py.sh"
PY_SH = str(PY_SH_PATH).replace("\\", "/")
HOOKS_JSON = REPO / "hooks" / "hooks.json"
GITATTRIBUTES = REPO / ".gitattributes"

NO_PYTHON_NOTICE = "no Python >= 3.10 found"

PROBE = """import sys
print("PYVER %d.%d" % (sys.version_info[0], sys.version_info[1]))
print("PYEXE %s" % sys.executable)
print("ARGS %s" % " ".join(sys.argv[1:]))
"""


def _find_bash():
    """A POSIX bash. On Windows, Git Bash - never a WSL or Store stub."""
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe",
                          r"C:\Program Files\Git\usr\bin\bash.exe",
                          r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.isfile(candidate):
                return candidate
        found = shutil.which("bash")
        if found and not any(part in found.lower()
                             for part in ("system32", "windowsapps")):
            return found
        return None
    return shutil.which("bash")


BASH = _find_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no POSIX bash on this machine")
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")


def fwd(path) -> str:
    return str(path).replace("\\", "/")


def run_sh(script, args, env_extra, cwd=None, stdin=""):
    """Run a shell script with a controlled environment.

    Everything the launcher reads is popped first, so a developer who exports
    AW_PYTHON or AW_HOME in their own shell cannot change what a test measures.
    """
    env = dict(os.environ)
    for key in ("AW_PYTHON", "AW_HOME"):
        env.pop(key, None)
    env.update(env_extra)
    return subprocess.run([BASH, script, *args], capture_output=True, text=True,
                          env=env, cwd=cwd, input=stdin, timeout=180)


def printed(stdout: str) -> dict:
    """The probe's `KEY value` lines as a dict."""
    return dict(line.split(" ", 1) for line in stdout.splitlines() if " " in line)


def same_file(a, b) -> bool:
    """Path equality that survives Windows case and symlinks."""
    return os.path.normcase(os.path.realpath(str(a))) == \
        os.path.normcase(os.path.realpath(str(b)))


@pytest.fixture
def home(tmp_path):
    """A throwaway AW_HOME, so py.sh's cache is never the developer's."""
    h = tmp_path / "awhome"
    h.mkdir()
    return h


@pytest.fixture
def cache(home):
    return home / ".autoweb-plugin" / "python_path"


@pytest.fixture
def probe(tmp_path):
    path = tmp_path / "probe.py"
    path.write_text(PROBE, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def make_sh_script():
    """A /bin/sh script with LF endings and the exec bit (see the module docstring)."""

    def _make(path, body: str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
        return path

    return _make


@pytest.fixture
def too_old(tmp_path, make_sh_script):
    """An interpreter that fails py.sh's version probe, as a 3.9 would.

    The probe is `python -c 'sys.exit(0 if version_info >= (3, 10) else 1)'`, so
    anything that exits non-zero is indistinguishable from an old interpreter -
    including the Windows Store stub, which is the case that actually happens.
    """
    return make_sh_script(tmp_path / "old" / "python", "exit 1\n")


# ------------------------------------------------------------------ the file

def test_py_sh_is_committed_with_lf_endings_only():
    """One CR and bash reads `exit 0` as `exit 0\\r`. .gitattributes pins this."""
    raw = PY_SH_PATH.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_gitattributes_pins_shell_scripts_to_lf():
    text = GITATTRIBUTES.read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in text
    assert "* text=auto" in text


@needs_bash
@posix_only
def test_py_sh_does_not_need_the_exec_bit_or_its_shebang(tmp_path, home, probe):
    """It is always run as `bash py.sh ...`; the shebang is a courtesy only."""
    copy = tmp_path / "copy.sh"
    copy.write_bytes(PY_SH_PATH.read_bytes())
    copy.chmod(0o644)
    proc = run_sh(fwd(copy), [fwd(probe)], {"AW_HOME": fwd(home),
                                            "AW_PYTHON": fwd(sys.executable)})
    assert proc.returncode == 0, proc.stderr
    assert "PYVER" in proc.stdout


# ------------------------------------------------------------------ discovery

@needs_bash
def test_py_sh_runs_a_script_with_python_310_or_newer(home, probe):
    """Rung 3, for real: one directory on PATH, the one holding this interpreter.

    PATH is replaced rather than inherited so the search cannot reach uv, and so
    a failure here means discovery is broken rather than that the developer's
    PATH is unusual.
    """
    proc = run_sh(PY_SH, [fwd(probe), "one", "two"],
                  {"AW_HOME": fwd(home),
                   "PATH": fwd(Path(sys.executable).parent)})
    assert proc.returncode == 0, proc.stderr
    lines = printed(proc.stdout)
    assert tuple(int(n) for n in lines["PYVER"].split(".")) >= (3, 10)
    assert lines["ARGS"] == "one two"


@needs_bash
def test_py_sh_honours_aw_python(home, probe):
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"AW_HOME": fwd(home), "AW_PYTHON": fwd(sys.executable)})
    assert proc.returncode == 0, proc.stderr
    assert same_file(printed(proc.stdout)["PYEXE"], sys.executable)


@needs_bash
def test_py_sh_skips_an_override_that_is_too_old_and_keeps_looking(home, probe,
                                                                  cache, too_old):
    """An AW_PYTHON that fails the probe is skipped, not fatal.

    The next rung is seeded with the interpreter running pytest, so this proves
    the fall-through without touching PATH or uv.
    """
    cache.parent.mkdir(parents=True)
    cache.write_text(fwd(sys.executable) + "\n\n", encoding="utf-8", newline="\n")
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"AW_HOME": fwd(home), "AW_PYTHON": fwd(too_old)})
    assert proc.returncode == 0, proc.stderr
    used = printed(proc.stdout)["PYEXE"]
    assert same_file(used, sys.executable)
    assert not same_file(used, too_old)


@needs_bash
def test_py_sh_runs_an_interpreter_whose_path_contains_a_space(home, probe, tmp_path,
                                                               cache, make_sh_script):
    """$AW_PY is quoted, so a per-user install under "John Smith" still runs."""
    wrapper = make_sh_script(tmp_path / "Program Files" / "py wrapper",
                             'exec "%s" "$@"\n' % fwd(sys.executable))
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"AW_HOME": fwd(home), "AW_PYTHON": fwd(wrapper)})
    assert proc.returncode == 0, proc.stderr
    assert "PYVER" in proc.stdout
    assert cache.read_text(encoding="utf-8").splitlines()[0] == fwd(wrapper)


# ------------------------------------------------------------------ the cache

@needs_bash
def test_py_sh_caches_the_interpreter_as_two_lines(home, probe, cache):
    assert not cache.exists()
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"AW_HOME": fwd(home), "AW_PYTHON": fwd(sys.executable)})
    assert proc.returncode == 0, proc.stderr
    assert cache.is_file()
    # line 1 the interpreter (which may contain spaces), line 2 its extra
    # arguments, empty when there are none
    lines = cache.read_text(encoding="utf-8").split("\n")
    assert lines[0] == fwd(sys.executable)
    assert len(lines) >= 2
    assert b"\r\n" not in cache.read_bytes()


@needs_bash
def test_py_sh_reuses_the_cached_interpreter(home, probe, cache, tmp_path):
    """A cache hit runs with no PATH at all, and is not rewritten."""
    cache.parent.mkdir(parents=True)
    cache.write_text(fwd(sys.executable) + "\n\n", encoding="utf-8", newline="\n")
    before = cache.read_bytes()
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"AW_HOME": fwd(home), "PATH": fwd(nowhere)})
    assert proc.returncode == 0, proc.stderr
    assert same_file(printed(proc.stdout)["PYEXE"], sys.executable)
    assert cache.read_bytes() == before


@needs_bash
def test_py_sh_replaces_a_cache_whose_interpreter_has_moved(home, probe, cache):
    cache.parent.mkdir(parents=True)
    cache.write_text("no-such-interpreter\n", encoding="utf-8", newline="\n")
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"AW_HOME": fwd(home), "AW_PYTHON": fwd(sys.executable)})
    assert proc.returncode == 0, proc.stderr
    assert "no-such-interpreter" not in cache.read_text(encoding="utf-8")


# ------------------------------------------------------------------ failures

@needs_bash
def test_py_sh_reports_a_script_that_is_gone_without_blocking(home, tmp_path):
    """`/plugin update` leaves an open session pointing at a scripts/ that is gone."""
    proc = run_sh(PY_SH, [fwd(tmp_path / "vanished.py")], {"AW_HOME": fwd(home)})
    assert proc.returncode == 0
    assert "missing (plugin updated?); restart the session" in proc.stderr
    assert proc.stdout == ""


@needs_bash
def test_py_sh_without_arguments_explains_itself(home):
    proc = run_sh(PY_SH, [], {"AW_HOME": fwd(home)})
    assert proc.returncode == 1          # a caller bug, never a hook condition
    assert "usage: bash py.sh" in proc.stderr
    assert proc.stdout == ""


def test_the_no_python_notice_is_one_line_and_is_followed_by_exit_zero():
    """Static, because the dynamic test below cannot run on every machine.

    A hook that exits non-zero can interfere with the session, so this branch
    must print one line to stderr and exit 0 - never exit 1, never print to
    stdout.
    """
    lines = PY_SH_PATH.read_text(encoding="utf-8").splitlines()
    hits = [i for i, line in enumerate(lines) if NO_PYTHON_NOTICE in line]
    assert len(hits) == 1
    notice = lines[hits[0]]
    assert ">&2" in notice
    assert "aw-setup" in notice
    assert lines[hits[0] + 1].strip() == "exit 0"


@needs_bash
def test_py_sh_exits_zero_when_no_python_can_be_found(home, probe, tmp_path):
    """Skipped on a machine that still has an interpreter py.sh can reach.

    Every rung whose location this test controls is neutered: AW_HOME, HOME and
    LOCALAPPDATA are temp directories (so the cache, `~/.local/bin/uv` and the
    per-user Python install are all absent) and PATH holds one empty directory.
    What remains is a short list of fixed absolute paths - /c/Windows/py.exe,
    /usr/bin/python3, /usr/local/bin/python3, /opt/homebrew/bin/python3 - which
    a test cannot hide. Where one of those exists, py.sh correctly finds it and
    there is nothing to assert, so the test skips rather than lying.
    """
    nowhere = tmp_path / "emptypath"
    nowhere.mkdir()
    fakehome = tmp_path / "fakehome"
    fakehome.mkdir()
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"AW_HOME": fwd(home), "HOME": fwd(fakehome),
                   "USERPROFILE": fwd(fakehome), "LOCALAPPDATA": fwd(fakehome),
                   "PATH": fwd(nowhere)})
    if NO_PYTHON_NOTICE not in proc.stderr:
        pytest.skip("this machine has a Python at one of py.sh's fixed absolute paths")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert len(proc.stderr.strip().splitlines()) == 1
    assert "aw-setup" in proc.stderr


# ------------------------------------------------------------------- the hook

def test_hooks_json_wraps_its_events_in_a_hooks_key():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    assert list(data) == ["hooks"]


def test_hooks_json_registers_session_start_and_nothing_else():
    """No Stop hook, no PreCompact: this plugin has no loop, by decision.

    docs/PLUGIN-DESIGN.md §5.5. AutoWeb's assertion commands exit non-zero on
    their own, so a goal is done when /autoweb:aw-run says every assertion
    passed; re-running is the user's call, not a hook's.
    """
    events = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
    assert list(events) == ["SessionStart"]


def test_the_session_start_hook_goes_through_py_sh_with_the_plugin_root():
    matchers = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    assert len(matchers) == 1
    hooks = matchers[0]["hooks"]
    assert len(hooks) == 1
    hook = hooks[0]
    assert hook["type"] == "command"
    assert hook["timeout"] == 15
    assert hook["command"] == (
        'bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" '
        '"${CLAUDE_PLUGIN_ROOT}/scripts/session_start.py"')


def test_every_script_the_hook_names_exists():
    command = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    command = command["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    for part in command.split('"'):
        if "${CLAUDE_PLUGIN_ROOT}/" in part:
            assert (REPO / part.replace("${CLAUDE_PLUGIN_ROOT}/", "")).is_file(), part

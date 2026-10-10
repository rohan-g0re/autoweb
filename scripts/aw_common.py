"""aw_common - the one place the AutoWeb plugin decides paths, discovery and state.

Every plugin script imports this so the SessionStart hook, the launcher, setup
and the skills agree by construction on:

  * where the plugin's own code is (`PLUGIN_ROOT`, the directory above scripts/)
  * what makes a directory an AutoWeb project (`autoweb.toml`)
  * where that project's identity, lanes, goals and runs live
  * where the plugin keeps its own machine-level state (`~/.autoweb-plugin/`)
  * how an installed `autoweb` binary, `uv` and Python are found

Standard library only, Python 3.10+ (the package's own floor). Configuration is
read through the shipped `autoweb.config` module, never parsed here, so the
plugin and the CLI cannot disagree about a key. Nothing here writes into a
project directory; the only writes are the plugin's own log and tool cache.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Windows consoles default to cp1252; hook output and goal text carry arrows.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

APP = "autoweb"

# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parent  # the repo when developing, the plugin cache when installed

HOME = Path(os.environ.get("AW_HOME") or Path.home())
STATE_DIR = HOME / ".autoweb-plugin"
LOG_PATH = STATE_DIR / "autoweb.log"
TOOLS_CACHE_PATH = STATE_DIR / "tools.json"
PYTHON_CACHE_PATH = STATE_DIR / "python_path"

CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (HOME / ".claude"))
CLAUDE_JSON = HOME / ".claude.json"  # workspace trust lives here, not under CLAUDE_CONFIG_DIR

CONFIG_FILENAME = "autoweb.toml"
STATE_DIRNAME = ".autoweb"
GOALS_DIRNAME = "goals"
RUNS_DIRNAME = "runs"
AGENTS_DIR = Path(".claude") / "agents"
SETTINGS_LOCAL = Path(".claude") / "settings.local.json"
LANE_GLOB = "lane-*.md"


def _package_importable() -> None:
    """Make the shipped `autoweb` package importable from the plugin root."""
    root = str(PLUGIN_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


_WARNED: set = set()


def _warn(msg: str) -> None:
    """Problems go to stderr, never stdout (stdout is model-facing). Once per process."""
    if msg in _WARNED:
        return
    _WARNED.add(msg)
    try:
        print(f"[{APP}] {msg}", file=sys.stderr)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# the project
# ---------------------------------------------------------------------------
def project_dir(cwd_from_payload: str | os.PathLike | None = None) -> Path:
    """The project directory: the hook payload's cwd when given, else the process cwd."""
    return Path(cwd_from_payload or os.getcwd()).resolve()


def is_autoweb_project(project: str | os.PathLike | None = None) -> bool:
    """A directory is an AutoWeb project when it holds `autoweb.toml`.

    The config file is the opt-in. A user-scope plugin sees every project on
    the machine, and a project that never asked for browser automation gets no
    status line and no advice.
    """
    return (project_dir(project) / CONFIG_FILENAME).is_file()


def load_project_config(project: str | os.PathLike | None = None):
    """The project's `autoweb.config.Config`, or None when it is missing or invalid.

    Never raises. The CLI's own `config check` is the place that explains an
    invalid file; the plugin only needs to know whether one is in force.
    """
    if not is_autoweb_project(project):
        return None
    try:
        _package_importable()
        from autoweb.config import Config  # noqa: WPS433  (shipped with the plugin)
        return Config.load(project_dir(project))
    except Exception as exc:  # ConfigError, ImportError, OSError - all mean "no config"
        _warn(f"{CONFIG_FILENAME} could not be read: {type(exc).__name__}: {exc}")
        return None


def project_paths(project: str | os.PathLike | None = None, cfg=None) -> dict:
    """Where this project's own files are. Keys: toml, root_json, autoweb_dir,
    goals_dir, runs_dir, agents_dir, settings_local. Paths are absolute; nothing
    is created."""
    root = project_dir(project)
    root_json = root / "root.json"
    try:
        if cfg is not None:
            root_json = (root / str(cfg.state.root)).resolve()
    except Exception:
        pass
    return {
        "toml": root / CONFIG_FILENAME,
        "root_json": root_json,
        "autoweb_dir": root / STATE_DIRNAME,
        "goals_dir": root / GOALS_DIRNAME,
        "runs_dir": root / RUNS_DIRNAME,
        "agents_dir": root / AGENTS_DIR,
        "settings_local": root / SETTINGS_LOCAL,
    }


def goal_files(project: str | os.PathLike | None = None) -> list:
    """`goals/*.md` except the template README, sorted by name."""
    goals = project_dir(project) / GOALS_DIRNAME
    try:
        return sorted(p for p in goals.glob("*.md") if p.name.lower() != "readme.md")
    except OSError:
        return []


def lane_files(project: str | os.PathLike | None = None) -> list:
    """Every `.claude/agents/lane-*.md`, generated or hand-written, sorted."""
    agents = project_dir(project) / AGENTS_DIR
    try:
        return sorted(agents.glob(LANE_GLOB))
    except OSError:
        return []


def generated_lane_files(project: str | os.PathLike | None = None) -> list:
    """The lane files that `autoweb lanes sync` wrote (they carry its marker)."""
    try:
        _package_importable()
        from autoweb.lanes import GENERATED_MARKER
    except Exception:
        GENERATED_MARKER = "generated by `autoweb lanes sync`"
    out = []
    for path in lane_files(project):
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        if GENERATED_MARKER in head:
            out.append(path)
    return out


def trust_accepted(project: str | os.PathLike | None = None):
    """True / False / None: whether Claude Code has recorded trust for this folder.

    Inline MCP servers in `.claude/agents/*.md` load only after trust, silently
    otherwise, so this is the first thing a doctor should print. Delegates to
    `autoweb.lanes.workspace_is_trusted`, which reads `~/.claude.json`.
    """
    try:
        _package_importable()
        from autoweb.lanes import workspace_is_trusted
        return workspace_is_trusted(project_dir(project))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
_UV_TOOL_BIN_CACHE: list = []  # [] = not asked yet; [None] or [Path]
_FIND_TOOL_CACHE: dict = {}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def uv_bin() -> str | None:
    """The uv executable: ~/.local/bin first (hooks may lack PATH), then PATH."""
    for cand in (HOME / ".local" / "bin" / "uv.exe", HOME / ".local" / "bin" / "uv"):
        if cand.exists():
            return str(cand)
    return shutil.which("uv")


def uv_output(argv: list, timeout: float = 10) -> str:
    """Run a uv query and return its stdout stripped of colour codes and whitespace.

    uv colours its output whenever FORCE_COLOR or CLICOLOR_FORCE is set, which
    Claude Code's tool shells do, so a captured path can arrive wrapped in
    escape sequences. Pass --color never, set NO_COLOR, and strip anyway.
    Returns "" on any failure.
    """
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env.pop("FORCE_COLOR", None)
    env.pop("CLICOLOR_FORCE", None)
    argv = [argv[0], "--color", "never", *argv[1:]]
    kwargs: dict = dict(capture_output=True, text=True, timeout=timeout, env=env)
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        out = subprocess.run(argv, **kwargs)
    except Exception:
        return ""
    if out.returncode != 0:
        return ""
    return _ANSI_RE.sub("", out.stdout or "").strip()


def _uv_tool_bin() -> Path | None:
    """`uv tool dir --bin`, asked at most once per process (it is a subprocess)."""
    if _UV_TOOL_BIN_CACHE:
        return _UV_TOOL_BIN_CACHE[0]
    result = None
    uv = uv_bin()
    if uv:
        out = uv_output([uv, "tool", "dir", "--bin"])
        if out:
            result = Path(out)
    _UV_TOOL_BIN_CACHE.append(result)
    return result


def uv_tool_env_dir() -> Path | None:
    """The uv tool environment that `uv tool install <plugin root>` creates for autoweb.

    Checked without a subprocess first (uv's two default locations), then via
    `uv tool dir`. Returns the environment directory, or None.
    """
    for cand in (HOME / "AppData" / "Roaming" / "uv" / "tools" / APP,
                 HOME / ".local" / "share" / "uv" / "tools" / APP):
        if cand.is_dir():
            return cand
    uv = uv_bin()
    if uv:
        out = uv_output([uv, "tool", "dir"])
        if out and (Path(out) / APP).is_dir():
            return Path(out) / APP
    return None


def uv_tool_env_python() -> Path | None:
    """The Python inside the autoweb tool environment (what `playwright install` must use)."""
    env_dir = uv_tool_env_dir()
    if env_dir is None:
        return None
    for cand in (env_dir / "Scripts" / "python.exe", env_dir / "bin" / "python"):
        if cand.exists():
            return cand
    return None


def _tool_cache_get(name: str) -> str | None:
    cached = read_json(TOOLS_CACHE_PATH, {})
    p = cached.get(name) if isinstance(cached, dict) else None
    return p if isinstance(p, str) and Path(p).exists() else None


def _tool_cache_put(name: str, path: str) -> None:
    try:
        cached = read_json(TOOLS_CACHE_PATH, {})
        if not isinstance(cached, dict):
            cached = {}
        if cached.get(name) != path:
            cached[name] = path
            write_json_atomic(TOOLS_CACHE_PATH, cached)
    except Exception:
        pass  # a cache that cannot be written is only a slower cache


def find_tool(name: str) -> str | None:
    """Absolute path of a CLI tool (autoweb, uv, node, npx, ...).

    Order: $AW_<NAME>_BIN; the path remembered in ~/.autoweb-plugin/tools.json
    from an earlier run (if it still exists); ~/.local/bin/<name>[.exe]; for
    `autoweb` the uv tool environment's own Scripts/ or bin/; `uv tool dir --bin`
    (a subprocess, asked at most once per process); PATH. Hooks do not inherit
    an interactive PATH, so explicit locations come first. None when nothing
    is found.
    """
    if name in _FIND_TOOL_CACHE:
        return _FIND_TOOL_CACHE[name]
    found = None
    env_key = "AW_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper() + "_BIN"
    env = os.environ.get(env_key)
    if env and Path(env).exists():
        found = env
    if not found:
        found = _tool_cache_get(name)
    if not found:
        exts = (".exe", ".cmd", "") if os.name == "nt" else ("",)
        candidates = [HOME / ".local" / "bin"]
        if name == APP:
            env_dir = uv_tool_env_dir()
            if env_dir:
                candidates += [env_dir / "Scripts", env_dir / "bin"]
        for d in candidates:
            for ext in exts:
                p = d / f"{name}{ext}"
                if p.exists():
                    found = str(p)
                    break
            if found:
                break
        if not found:
            uvbin = _uv_tool_bin()
            if uvbin and uvbin not in candidates:
                for ext in exts:
                    p = uvbin / f"{name}{ext}"
                    if p.exists():
                        found = str(p)
                        break
        if not found:
            found = shutil.which(name)
        if found and not env:
            _tool_cache_put(name, found)
    _FIND_TOOL_CACHE[name] = found
    return found


def python_exe() -> str:
    """The interpreter running this script, for spawning sibling scripts."""
    return sys.executable or "python"


def autoweb_argv(project: str | os.PathLike | None = None, *sub: str) -> list:
    """How to run the AutoWeb CLI against a project.

    `[<installed autoweb>, "-C", <project>, *sub]` when `uv tool install` has
    run, else `[<python>, scripts/aw.py, "-C", <project>, *sub]`, which runs the
    package in place from the plugin. Either way the project is named
    explicitly: the CLI must never guess it from the caller's cwd.
    """
    exe = find_tool(APP)
    head = [exe] if exe else [python_exe(), str(SCRIPTS_DIR / "aw.py")]
    return [*head, "-C", str(project_dir(project)), *sub]


# ---------------------------------------------------------------------------
# state helpers
# ---------------------------------------------------------------------------
def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json_atomic(path: Path, obj) -> None:
    """Write via a temp file and os.replace so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def append_log(line: str) -> None:
    """One timestamped line to ~/.autoweb-plugin/autoweb.log. Never raises."""
    try:
        ensure_state_dir()
        with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"{now_stamp()} {line.rstrip()}\n")
    except Exception:
        pass

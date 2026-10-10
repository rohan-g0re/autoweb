"""aw_setup - say what AutoWeb needs on this machine, install it, start a project.

Three jobs, one script, because they answer the same question in three voices:

    --check         (the default) the table: what is here, what is not, how to fix it
    --install       make the missing pieces exist, through uv and nothing else
    --init-project  turn the current folder into an AutoWeb project

What has to be true before a lane opens a browser:

    python >= 3.10        the package floor; this is the interpreter running now
    uv                    how the package and its playwright dependency arrive
    node >= 18            @playwright/mcp is a Node package, run through npx
    playwright mcp        the pinned MCP server, warm in the npx cache or not
    autoweb cli           the installed binary, or the package run in place
    playwright package    needed by `state export` and `state verify`, nothing else
    browser               a real browser on disk: Playwright's cache or Chrome's
    git bash              Windows only; the hooks and skills run through it
    trust                 NOTE - without it a lane's inline MCP server never starts
    lanes                 NOTE - how many agent files exist, and any profile leak
    identity              NOTE - root.json, the state every lane is seeded from

The last three are NOTE rows: they describe this project rather than this
machine, they change between one folder and the next, and a fresh project is
*expected* to have none of them. Failing the run on them would make the exit
code useless as a gate, which is the one thing a doctor's exit code is for.

Levels, exit codes
------------------
A NOTE never changes the exit code. Everything else does: 0 when no gating
check is MISSING, 1 when any is. `--json` prints the same rows as a document so
a skill can read them without parsing the table.

What this script will not do
----------------------------
It never installs uv or Node. Both are system-level installs with their own
official one-liners, and a setup script that reaches for a system package
manager on someone's behalf is how a "check my prerequisites" command ends up
owning a broken toolchain. When either is missing the row prints the official
command for this OS and stops.

It writes nothing into any settings file. The lane permission grants in
`.claude/settings.local.json` belong to `autoweb lanes sync`, which knows how
many lanes there are; `--init-project` touches only `autoweb.toml`, `goals/`
and `.git/info/exclude`.

Usage
-----
    python aw_setup.py                       # the check table
    python aw_setup.py --check --json
    python aw_setup.py --install [--skip-browser] [--browser chromium]
    python aw_setup.py --init-project [--cwd DIR]
"""
from __future__ import annotations

import os
import sys

# aw_common first: it reconfigures the streams to UTF-8 and is the only place
# that decides where things live. Imported by path so this script works whether
# it was started from the plugin, the repo, or a skill's bash block.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aw_common as aw  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent

MIN_PYTHON = (3, 10)
MIN_NODE = 18

# Labels are a contract with docs/PLUGIN-DESIGN.md section 5.4 and with the
# skills that grep this table. Changing one is a documentation change.
PYTHON_CHECK = "python >= %d.%d" % MIN_PYTHON
UV_CHECK = "uv"
NODE_CHECK = "node >= %d" % MIN_NODE
AUTOWEB_CHECK = "autoweb cli"
PLAYWRIGHT_CHECK = "playwright package"
BROWSER_CHECK = "browser"
GIT_BASH_CHECK = "git bash"
TRUST_CHECK = "trust"
LANES_CHECK = "lanes"
IDENTITY_CHECK = "identity"


def mcp_check_label(version: str) -> str:
    """`playwright mcp <version>`. The version is the pinned one, not a guess.

    It comes from the project's `lanes.mcp_version` when there is a config, else
    from `LaneConfig`'s default. The label carries it because the check is about
    one exact package in the npx cache: reporting a warm cache for 0.0.83 while
    the project pins something else would be a lie in the shape of a fact.
    """
    return f"playwright mcp {version}"


LEVEL_CHECK = "check"
LEVEL_NOTE = "note"

# uv's own installers, quoted from https://docs.astral.sh/uv/getting-started/.
UV_INSTALL_WINDOWS = ('powershell -ExecutionPolicy ByPass -c '
                      '"irm https://astral.sh/uv/install.ps1 | iex"')
UV_INSTALL_POSIX = "curl -LsSf https://astral.sh/uv/install.sh | sh"

NODE_INSTALL_WINDOWS = "winget install OpenJS.NodeJS.LTS"
NODE_INSTALL_MACOS = "brew install node"
NODE_INSTALL_LINUX = ("install Node 18 or newer from https://nodejs.org "
                      "(or your package manager)")

INSTALL_FIX = "run --install"
SETUP_SKILL = "/autoweb:aw-setup"
EXPORT_HINT = "autoweb state export <url>"
TRUST_FIX = "open this folder in Claude Code and accept the trust prompt"
LANES_FIX = "autoweb lanes sync"

# The probe is `importlib.metadata`, not `playwright.__version__`: the package
# has no such attribute, so the obvious one-liner imports fine and then raises
# AttributeError, which reads as "playwright is broken" when it is installed.
PLAYWRIGHT_PROBE = ("import importlib.metadata as m, playwright; "
                    "print(m.version('playwright'))")
PLAYWRIGHT_ONLY_FOR = "needed only by `autoweb state export` and `state verify`"

BROWSERS = ("chromium", "chrome", "msedge", "firefox", "webkit")
DEFAULT_BROWSER = "chromium"
# Browsers Playwright does not download: it drives the one already installed on
# the machine, so there is nothing in its cache to look for.
CHANNEL_BROWSERS = ("chrome", "msedge")
BROWSER_CACHE_DIRNAME = "ms-playwright"

CREATE_NO_WINDOW = 0x08000000  # a probe must never flash a console window up

EXCLUDE_LINES = (
    ".autoweb/",
    "root.json",
    "root.json.*",
    "lane-*.json",
    "runs/",
    ".claude/agents/lane-*.md",
    ".claude/settings.local.json",
)
EXCLUDE_HEADER = "# AutoWeb: per-project state, never committed."

# A generated lane under `isolated = false` gets its own profile directory under
# the project. One pointing anywhere else is the only way two projects can share
# a browser profile, so the lanes NOTE hunts for it in both spellings argv can
# carry it in.
USER_DATA_DIR_RE = re.compile(r'--user-data-dir(?:"\s*,\s*"|=)([^"\s,\]]+)')


def fwd(path) -> str:
    """One path with forward slashes: readable in a table, valid on Windows."""
    return str(path).replace("\\", "/")


def uv_install_hint() -> str:
    """The official uv installer for this operating system."""
    return UV_INSTALL_WINDOWS if os.name == "nt" else UV_INSTALL_POSIX


def node_install_hint() -> str:
    """The usual way Node arrives on this operating system."""
    if os.name == "nt":
        return NODE_INSTALL_WINDOWS
    if sys.platform == "darwin":
        return NODE_INSTALL_MACOS
    return NODE_INSTALL_LINUX


def package_defaults():
    """`(LaneConfig, StateConfig, CapsConfig)` from the shipped package.

    Defaults are read off the dataclasses rather than copied here. A number
    written twice is a number that will disagree with itself: the generated
    `autoweb.toml` and the version in the MCP label both have to be whatever
    `autoweb/config.py` says today.

    aw_common does the same path insertion privately; this repeats it with the
    public `PLUGIN_ROOT` rather than calling a private helper.
    """
    root = str(aw.PLUGIN_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from autoweb.config import CapsConfig, LaneConfig, StateConfig
    return LaneConfig(), StateConfig(), CapsConfig()


def pinned_mcp_version(cfg=None) -> str:
    """`lanes.mcp_version` for this project, else the package default."""
    try:
        if cfg is not None:
            return str(cfg.lanes.mcp_version)
        return str(package_defaults()[0].mcp_version)
    except Exception:
        return "0.0.83"  # the pin of record; only reachable if the package is broken


def _run(argv: list, timeout: float = 30):
    """Run a probe and hand back the CompletedProcess, or None if it never ran.

    Every caller treats None and a non-zero exit the same way, so a missing
    binary, a timeout and a crash all become one MISSING row with the reason in
    its detail rather than a traceback out of a doctor.
    """
    kwargs: dict = dict(capture_output=True, text=True, timeout=timeout)
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        return subprocess.run([str(a) for a in argv], **kwargs)
    except Exception:
        return None


def _tail(out) -> str:
    """The last line a probe printed, stderr first: that is where the reason is."""
    if out is None:
        return "did not start"
    for stream in ((out.stderr or ""), (out.stdout or "")):
        lines = [ln.strip() for ln in stream.strip().splitlines() if ln.strip()]
        if lines:
            return lines[-1]
    return f"exit {out.returncode}"


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
@dataclass
class Check:
    """One row: its label, whether it holds, what was found, how to fix it.

    `level` decides whether it is a gate. `LEVEL_NOTE` rows describe the project
    and never change the exit code; everything else does.
    """

    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    level: str = LEVEL_CHECK

    @property
    def gating(self) -> bool:
        return self.level != LEVEL_NOTE

    def as_dict(self) -> dict:
        # A fix on a passing row is noise: several checks carry their install
        # command whatever the answer, and a reader - a skill, usually - should
        # be able to treat a non-empty `fix` as "there is something to run".
        return {"check": self.name, "ok": self.ok, "detail": self.detail,
                "fix": "" if self.ok else self.fix, "level": self.level}


@dataclass
class Report:
    """Every row, and the one verdict drawn from the gating ones."""

    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.gating)

    def get(self, name: str):
        return next((c for c in self.checks if c.name == name), None)

    def missing(self, name: str) -> bool:
        c = self.get(name)
        return c is not None and not c.ok

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checks": [c.as_dict() for c in self.checks]}


# ---------------------------------------------------------------------------
# the machine
# ---------------------------------------------------------------------------
def python_check() -> Check:
    """The interpreter running this script, which is the one the hooks will use."""
    version = sys.version_info[:3]
    detail = "%d.%d.%d at %s" % (*version, fwd(sys.executable))
    return Check(PYTHON_CHECK, sys.version_info[:2] >= MIN_PYTHON, detail,
                 fix="install Python %d.%d or newer, or let uv supply one: "
                     "uv python install 3.12" % MIN_PYTHON)


def uv_check() -> Check:
    """uv, which is how the package and its playwright dependency arrive."""
    uv = aw.uv_bin()
    return Check(UV_CHECK, bool(uv), fwd(uv) if uv else "not found",
                 fix=uv_install_hint())


def node_check() -> Check:
    """Node, because `@playwright/mcp` is a Node package started through npx."""
    node = aw.find_tool("node")
    if not node:
        return Check(NODE_CHECK, False, "not found", fix=node_install_hint())
    out = _run([node, "--version"])
    if out is None or out.returncode != 0:
        return Check(NODE_CHECK, False,
                     f"{fwd(node)} did not run ({_tail(out)})",
                     fix=node_install_hint())
    printed = (out.stdout or "").strip() or (out.stderr or "").strip()
    match = re.search(r"(\d+)", printed)
    if not match:
        return Check(NODE_CHECK, False,
                     f"{fwd(node)} printed no version ({printed!r})",
                     fix=node_install_hint())
    major = int(match.group(1))
    detail = f"{printed.splitlines()[0]} at {fwd(node)}"
    if major < MIN_NODE:
        return Check(NODE_CHECK, False, f"{detail} - too old (need {MIN_NODE})",
                     fix=node_install_hint())
    return Check(NODE_CHECK, True, detail)


def mcp_cache_check(version: str) -> Check:
    """Is the pinned `@playwright/mcp` already in the npx cache?

    A NOTE, never a failure. A cold cache is not a broken machine: the first
    lane start downloads the package and then works. It is here because that
    download happens inside a browser session's startup, where it looks like a
    hang, and one warm-up command beforehand removes the surprise.

    `--no-install` is the whole probe. Without it npx would *fetch* the package
    to answer the question, which turns a check into an install.
    """
    name = mcp_check_label(version)
    spec = f"@playwright/mcp@{version}"
    npx = aw.find_tool("npx")
    if not npx:
        return Check(name, False, f"npx not found, so the {spec} cache cannot be read",
                     fix=node_install_hint(), level=LEVEL_NOTE)
    # 60 s: a warm answer is immediate, but npx on Windows can take tens of
    # seconds to decide it has nothing cached.
    out = _run([npx, "--no-install", spec, "--version"], timeout=60)
    if out is not None and out.returncode == 0:
        printed = (out.stdout or "").strip().splitlines()
        answered = printed[-1].strip() if printed else "no output"
        return Check(name, True, f"cache warm: {spec} answered {answered}",
                     level=LEVEL_NOTE)
    return Check(name, False,
                 f"cache cold: {spec} is not in the npx cache, so the first lane "
                 f"start downloads it ({_tail(out)})",
                 fix=f"npx {spec} --version", level=LEVEL_NOTE)


def autoweb_cli_check() -> Check:
    """The installed `autoweb` binary, or the package run in place.

    Both are OK. Running in place is what makes the repo usable before anything
    is installed, and `scripts/aw.py` falls back to it by design, so a missing
    binary is a fact about this machine rather than a fault.
    """
    exe = aw.find_tool(aw.APP)
    if exe:
        return Check(AUTOWEB_CHECK, True, fwd(exe))
    return Check(AUTOWEB_CHECK, True,
                 f"running in place from the plugin ({fwd(aw.PLUGIN_ROOT)}); "
                 f"--install exposes the `{aw.APP}` binary")


def playwright_package_check() -> Check:
    """Can the installed tool environment import playwright?

    Only two commands need it - `state export` and `state verify` - because
    everything else drives the browser through MCP. It is still a gate: an
    identity that cannot be exported is a project that cannot start.

    The interpreter asked is the one inside uv's `autoweb` tool environment, not
    this one. That is where `uv tool install` puts the dependency, and it is the
    interpreter the installed CLI runs under.
    """
    py = aw.uv_tool_env_python()
    if py is None:
        return Check(PLAYWRIGHT_CHECK, False,
                     f"uv's `{aw.APP}` tool environment is absent, so playwright "
                     f"is not installed there ({PLAYWRIGHT_ONLY_FOR})",
                     fix=INSTALL_FIX)
    out = _run([py, "-c", PLAYWRIGHT_PROBE], timeout=60)
    if out is not None and out.returncode == 0 and (out.stdout or "").strip():
        version = (out.stdout or "").strip().splitlines()[-1].strip()
        return Check(PLAYWRIGHT_CHECK, True,
                     f"playwright {version} in {fwd(py)} ({PLAYWRIGHT_ONLY_FOR})")
    return Check(PLAYWRIGHT_CHECK, False,
                 f"{fwd(py)} cannot import playwright ({_tail(out)}); "
                 f"{PLAYWRIGHT_ONLY_FOR}",
                 fix=INSTALL_FIX)


def browsers_cache_dir() -> Path:
    """Where Playwright keeps the browsers it downloads.

    `PLAYWRIGHT_BROWSERS_PATH` wins when it is set, because then nothing is in
    the default place. The one value it ignores is `0`, which means "beside the
    package" rather than "in this directory".
    """
    env = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env and env != "0":
        return Path(env)
    if sys.platform == "darwin":
        return aw.HOME / "Library" / "Caches" / BROWSER_CACHE_DIRNAME
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(aw.HOME / "AppData" / "Local")
        return Path(local) / BROWSER_CACHE_DIRNAME
    return aw.HOME / ".cache" / BROWSER_CACHE_DIRNAME


def channel_paths(browser: str) -> list:
    """Where a browser Playwright does *not* download is normally installed.

    `chrome` and `msedge` are channels: playwright-mcp launches the real browser
    already on the machine, so there is nothing in Playwright's cache to find
    and the install locations are the only evidence.
    """
    local = Path(os.environ.get("LOCALAPPDATA") or str(aw.HOME / "AppData" / "Local"))
    if browser == "chrome":
        if os.name == "nt":
            tail = Path("Google") / "Chrome" / "Application" / "chrome.exe"
            return [Path("C:/Program Files") / tail,
                    Path("C:/Program Files (x86)") / tail,
                    local / tail]
        if sys.platform == "darwin":
            tail = Path("Google Chrome.app") / "Contents" / "MacOS" / "Google Chrome"
            return [Path("/Applications") / tail, aw.HOME / "Applications" / tail]
        return [Path("/usr/bin/google-chrome"), Path("/usr/bin/google-chrome-stable"),
                Path("/opt/google/chrome/chrome")]
    if os.name == "nt":
        tail = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
        return [Path("C:/Program Files (x86)") / tail, Path("C:/Program Files") / tail,
                local / tail]
    if sys.platform == "darwin":
        tail = Path("Microsoft Edge.app") / "Contents" / "MacOS" / "Microsoft Edge"
        return [Path("/Applications") / tail, aw.HOME / "Applications" / tail]
    return [Path("/usr/bin/microsoft-edge"), Path("/usr/bin/microsoft-edge-stable")]


def channel_on_path(browser: str) -> str | None:
    """A channel browser on PATH, for a Linux box that installed it elsewhere."""
    names = (("google-chrome", "google-chrome-stable", "chrome")
             if browser == "chrome" else ("microsoft-edge", "msedge"))
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def browser_check(browser: str) -> Check:
    """Is there a browser on disk for lanes to launch?

    Two different questions behind one label. A downloaded browser lives in
    Playwright's cache as `<name>-<revision>/`; a channel browser lives wherever
    its own installer put it. Reporting either as "browser" keeps the table
    about the thing that matters - whether a lane can start.
    """
    if browser in CHANNEL_BROWSERS:
        tried = channel_paths(browser)
        for cand in tried:
            try:
                if cand.exists():
                    return Check(BROWSER_CHECK, True, f"{browser} at {fwd(cand)}")
            except OSError:
                continue
        on_path = channel_on_path(browser)
        if on_path:
            return Check(BROWSER_CHECK, True, f"{browser} at {fwd(on_path)}")
        return Check(BROWSER_CHECK, False,
                     f"{browser} is not installed where it is normally found "
                     f"(looked in {len(tried)} places, last {fwd(tried[-1])})",
                     fix=f"install {browser}, or set lanes.browser = \"chromium\" "
                         f"in autoweb.toml and {INSTALL_FIX}")
    cache = browsers_cache_dir()
    try:
        found = sorted(d.name for d in cache.glob(f"{browser}-*") if d.is_dir())
    except OSError:
        found = []
    if found:
        return Check(BROWSER_CHECK, True, f"{', '.join(found)} in {fwd(cache)}")
    return Check(BROWSER_CHECK, False, f"no {browser}-* in {fwd(cache)}",
                 fix=f"{INSTALL_FIX} (downloads {browser})")


def git_bash_candidates() -> list:
    """Every bash worth considering on Windows, best first.

    Git for Windows is the shell Claude Code runs hooks and skill bash blocks
    under, so what matters is that *its* bash exists - not that `bash` resolves
    to something.
    """
    cands: list = []
    env = (os.environ.get("AW_BASH_BIN") or "").strip()
    if env:
        cands.append(Path(env))
    bases = [Path("C:/Program Files/Git"), Path("C:/Program Files (x86)/Git"),
             aw.HOME / "AppData" / "Local" / "Programs" / "Git"]
    git = aw.find_tool("git")
    if git:
        # <install>/cmd/git.exe and <install>/bin/git.exe both sit one level
        # under the install root, so its parent's parent is that root.
        bases.append(Path(git).resolve().parent.parent)
    for base in bases:
        cands += [base / "bin" / "bash.exe", base / "usr" / "bin" / "bash.exe"]
    found = shutil.which("bash")
    if found:
        cands.append(Path(found))
    return cands


def is_wsl_shim(path) -> bool:
    """Is this the `bash.exe` that only launches WSL?

    Windows ships one in WindowsApps (and an older one in System32). It answers
    `bash --version` by trying to start a Linux distribution, so finding it and
    calling it Git Bash is worse than finding nothing: the hooks fail later,
    inside a shell that cannot see the Windows filesystem the way they expect.
    """
    low = fwd(path).lower()
    return "/windowsapps/" in low or "/system32/" in low


def git_bash_check() -> Check:
    """Windows only. Git Bash is how every hook and skill command is started."""
    shims: list = []
    for cand in git_bash_candidates():
        try:
            if not cand.exists():
                continue
        except OSError:
            continue
        if is_wsl_shim(cand):
            shims.append(cand)
            continue
        return Check(GIT_BASH_CHECK, True, fwd(cand))
    detail = "not found"
    if shims:
        detail += (f"; ignored the WSL launcher at {fwd(shims[0])}, which is not "
                   f"Git Bash")
    return Check(GIT_BASH_CHECK, False, detail,
                 fix="install Git for Windows (winget install Git.Git); the plugin's "
                     "hooks and skill commands run under its bash")


# ---------------------------------------------------------------------------
# the project
# ---------------------------------------------------------------------------
def trust_check(project: Path) -> Check:
    """Has Claude Code recorded trust for this folder?

    A NOTE, and the most consequential one in the table. An untrusted folder's
    agent files get their inline `mcpServers` skipped *silently*: lanes dispatch,
    no browser ever starts, and nothing says why. Not a gate only because it
    cannot be fixed from a script - a human has to accept the prompt.
    """
    trusted = aw.trust_accepted(project)
    if trusted is True:
        return Check(TRUST_CHECK, True, "granted", level=LEVEL_NOTE)
    if trusted is False:
        return Check(TRUST_CHECK, False,
                     "not granted - a lane's inline MCP server will not start, and "
                     "the skip is silent",
                     fix=TRUST_FIX, level=LEVEL_NOTE)
    return Check(TRUST_CHECK, False,
                 f"unknown - {fwd(aw.CLAUDE_JSON)} is absent or unreadable",
                 fix=TRUST_FIX, level=LEVEL_NOTE)


def leaking_profiles(project: Path, generated: list) -> list:
    """Generated lanes whose `--user-data-dir` points outside this project.

    The one cross-project leak the design admits: two projects with
    `lanes.isolated = false` aimed at the same profile directory share one
    browser identity. The generator never writes such a path, so finding one
    means a hand edit or a copied file, and it is worth a line in the table.
    """
    leaks: list = []
    root = project.resolve()
    for path in generated:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in USER_DATA_DIR_RE.findall(text):
            try:
                target = Path(raw)
                # A relative path in an agent file resolves against the MCP
                # server's working directory, which is the project; resolving it
                # against this script's cwd instead would invent a leak.
                target = (target if target.is_absolute() else root / target).resolve()
            except OSError:
                continue
            if not target.is_relative_to(root):
                leaks.append((path.name, fwd(target)))
    return leaks


def lanes_check(project: Path, cfg) -> Check:
    """How many lane agent files exist, and whether any of them leaks a profile."""
    generated = aw.generated_lane_files(project)
    every = aw.lane_files(project)
    hand = len(every) - len(generated)
    ceiling = None
    try:
        if cfg is not None:
            ceiling = int(cfg.lanes.max)
    except Exception:
        ceiling = None

    if ceiling is None:
        parts = [f"{len(generated)} generated, no autoweb.toml to set a ceiling"]
    else:
        parts = [f"{len(generated)} of {ceiling} generated"]
    if hand > 0:
        parts.append(f"+{hand} hand-written")

    leaks = leaking_profiles(project, generated)
    for name, target in leaks:
        parts.append(f"{name} points --user-data-dir outside the project: {target}")

    ok = not leaks and ceiling is not None and len(generated) == ceiling
    fix = ""
    if leaks:
        fix = (f"a profile shared with another project is the one way two projects "
               f"can leak into each other; {LANES_FIX} rewrites it")
    elif not ok:
        fix = LANES_FIX
    return Check(LANES_CHECK, ok, "; ".join(parts), fix=fix, level=LEVEL_NOTE)


def identity_check(paths: dict) -> Check:
    """Is there a `root.json` for lanes to be seeded from?"""
    root_json = paths["root_json"]
    if root_json.is_file():
        try:
            size = root_json.stat().st_size
        except OSError:
            size = 0
        return Check(IDENTITY_CHECK, True,
                     f"{fwd(root_json)} present ({size} bytes)", level=LEVEL_NOTE)
    return Check(IDENTITY_CHECK, False, f"{fwd(root_json)} missing",
                 fix=EXPORT_HINT, level=LEVEL_NOTE)


def resolve_browser(cfg, requested: str | None = None) -> str:
    """Which browser `--install` downloads, and which one the table looks for.

    `--browser` wins; then the project's `lanes.browser`; then chromium. An
    unrecognised name falls back to chromium rather than being passed through,
    because `playwright install <typo>` fails after the download has started.
    """
    for value in (requested, getattr(getattr(cfg, "lanes", None), "browser", None)):
        name = str(value).strip().lower() if value else ""
        if name in BROWSERS:
            return name
    return DEFAULT_BROWSER


def collect(project: Path) -> Report:
    """Run every check for one project. Reads only; writes nothing anywhere."""
    project = Path(project)
    cfg = aw.load_project_config(project)
    paths = aw.project_paths(project, cfg)

    rep = Report()
    rep.checks.append(python_check())
    rep.checks.append(uv_check())
    rep.checks.append(node_check())
    rep.checks.append(mcp_cache_check(pinned_mcp_version(cfg)))
    rep.checks.append(autoweb_cli_check())
    rep.checks.append(playwright_package_check())
    rep.checks.append(browser_check(resolve_browser(cfg)))
    if os.name == "nt":
        rep.checks.append(git_bash_check())
    rep.checks.append(trust_check(project))
    rep.checks.append(lanes_check(project, cfg))
    rep.checks.append(identity_check(paths))
    return rep


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------
STATUS = {True: "OK     ", False: "MISSING"}
NOTE_STATUS = "NOTE   "


def print_table(rep: Report, project: Path, out=None) -> None:
    """The table, with the fix under every row that has one."""
    out = out or sys.stdout
    width = max((len(c.name) for c in rep.checks), default=10)
    print("=" * 72, file=out)
    print("autoweb prerequisites", file=out)
    print(f"project: {fwd(project)}", file=out)
    print("=" * 72, file=out)
    for c in rep.checks:
        status = NOTE_STATUS if not c.gating else STATUS[bool(c.ok)]
        print(f"  [{status}] {c.name.ljust(width)}  {c.detail}", file=out)
        if not c.ok and c.fix:
            label = "hint" if not c.gating else "fix"
            print(f"            {' ' * width}  {label}: {c.fix}", file=out)
    print(file=out)
    print("  verdict:", "ready" if rep.ok else "missing pieces above", file=out)


def print_lines(lines: list, header: str, out=None) -> None:
    """A short block of what happened, for `--install` and `--init-project`."""
    out = out or sys.stdout
    print(header, file=out)
    for line in lines:
        print(f"  {line}", file=out)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------
def stream(argv: list, sink, label: str) -> int:
    """Run one install command with its output passed straight through.

    The output goes to `sink` (stderr) and only the summary reaches stdout, so
    `--json` stays one parseable document and a skill reading stdout is not
    handed half a megabyte of npm progress bars.

    A child process can only inherit a sink that owns a file descriptor. Under a
    captured stream - a test harness, or a session that redirected the plugin's
    output - there is none, so the output is captured and copied through instead
    of the command silently failing to start.
    """
    argv = [str(a) for a in argv]
    print(f"\n-> {label}", file=sink)
    print(f"   {' '.join(argv)}", file=sink)
    try:
        sink.flush()
        handle = sink if sink.fileno() >= 0 else None
    except Exception:
        handle = None
    try:
        if handle is not None:
            return subprocess.run(argv, stdout=handle,
                                  stderr=subprocess.STDOUT).returncode
        out = subprocess.run(argv, capture_output=True, text=True)
        for text in ((out.stdout or ""), (out.stderr or "")):
            if text:
                print(text.rstrip(), file=sink)
        return out.returncode
    except Exception as exc:
        print(f"   failed to start: {exc}", file=sink)
        return 1


def forget_autoweb_binary() -> None:
    """Drop what discovery remembered about `autoweb`, so the new binary is found.

    `find_tool` caches in memory for the process and on disk in
    `~/.autoweb-plugin/tools.json` across runs. Both answers predate the install
    that just happened: in memory the answer is "not found", and on disk it can
    name a binary uv has just replaced.
    """
    try:
        aw._FIND_TOOL_CACHE.clear()
        aw._UV_TOOL_BIN_CACHE.clear()
    except Exception:
        pass
    cached = aw.read_json(aw.TOOLS_CACHE_PATH, {})
    if isinstance(cached, dict) and aw.APP in cached:
        cached.pop(aw.APP, None)
        try:
            aw.write_json_atomic(aw.TOOLS_CACHE_PATH, cached)
        except Exception:
            pass  # a stale cache entry is a slower lookup, not a failure


def do_install(project: Path, browser: str, skip_browser: bool, sink) -> list:
    """Install the package through uv, then the browser. Returns the summary lines.

    Nothing else is installed. uv and Node are the two prerequisites a script
    should not take over, and `uv tool install` brings the package, its
    playwright dependency and the `autoweb` binary in one step, so there is only
    ever one command here plus the browser download.
    """
    summary: list = []
    uv = aw.uv_bin()
    if not uv:
        hint = uv_install_hint()
        print("uv is missing, and uv is how everything else installs. Install it "
              "with:", file=sink)
        print(f"  {hint}", file=sink)
        print(f"then run {SETUP_SKILL} again.", file=sink)
        summary.append(f"uv is missing - install it with: {hint}")
        return summary

    root = str(aw.PLUGIN_ROOT)
    rc = stream([uv, "tool", "install", "--force", root], sink,
                f"install the {aw.APP} package and its playwright dependency")
    summary.append(f"uv tool install --force {fwd(root)}: "
                   f"{'done' if rc == 0 else f'failed (exit {rc})'}")

    # Before anything looks for the binary again.
    forget_autoweb_binary()

    if skip_browser:
        summary.append("browser download skipped (--skip-browser)")
    else:
        py = aw.uv_tool_env_python()
        if py is None:
            summary.append(f"browser download skipped: uv's `{aw.APP}` tool "
                           f"environment has no python yet")
        else:
            brc = stream([py, "-m", "playwright", "install", browser], sink,
                         f"download the {browser} browser")
            summary.append(f"playwright install {browser}: "
                           f"{'done' if brc == 0 else f'failed (exit {brc})'}")

    out = _run(aw.autoweb_argv(project, "--version"))
    if out is not None and out.returncode == 0:
        printed = (out.stdout or "").strip().splitlines()
        summary.append(f"{aw.APP} --version: {printed[-1].strip() if printed else '?'}")
    else:
        summary.append(f"{aw.APP} --version: could not run ({_tail(out)})")
    return summary


# ---------------------------------------------------------------------------
# init a project
# ---------------------------------------------------------------------------
def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))


def _toml_line(key: str, value, comment: str = "", width: int = 21) -> str:
    text = f"{key} = {_toml_value(value)}"
    return f"{text.ljust(width)}# {comment}" if comment else text


def autoweb_toml_text() -> str:
    """The starter `autoweb.toml`: every default, with the reason for each.

    The values come from the dataclasses, so this file can never claim a default
    the loader does not have. The comments are the ones this repo's own
    `autoweb.toml` carries - they are what makes a generated config worth
    reading rather than worth deleting.
    """
    lanes, state, caps = package_defaults()
    pad = " " * 21
    return "\n".join([
        "# AutoWeb configuration.",
        "#",
        "# Every option here has a working default - this file exists so you can see "
        "what is",
        "# adjustable, not because anything must be set. Delete any line to take the "
        "default.",
        "# Options are documented at their definition in autoweb/config.py.",
        "",
        "[lanes]",
        _toml_line("max", lanes.max, "ceiling on concurrent lanes, never a target"),
        _toml_line("browser", lanes.browser,
                   "set to \"chromium\" on most Linux; Chrome is x86_64-only,"),
        f"{pad}# absent from Arch repos, and nonexistent for ARM Linux",
        _toml_line("mcp_version", lanes.mcp_version),
        _toml_line("isolated", lanes.isolated,
                   "nothing written to disk; what makes lanes safe in parallel"),
        "",
        "[state]",
        _toml_line("root", state.root,
                   "the base storageState every lane starts from"),
        _toml_line("indexeddb", state.indexeddb,
                   "false quietly loses Firebase/Supabase/Auth0 logins"),
        "",
        "[caps]",
        "# A safety valve, not a selection policy. Hitting one should be loud.",
        _toml_line("total_bytes", caps.total_bytes),
        _toml_line("max_origins", caps.max_origins),
        _toml_line("max_indexeddb_per_origin", caps.max_indexeddb_per_origin),
        "",
        "# Per-origin overrides. Most entries are written by the loop into",
        "# .autoweb/learned.json; anything you set here wins over what it learned.",
        "#",
        "# [origins.\"example.com\"]",
        "# indexeddb = false   # this site's IndexedDB is large and worthless",
        "# rotates   = true    # rotates tokens -> only ONE lane may hold it",
        "# sticky    = true    # never evict; dropping it forces a re-login",
        "",
    ])


def git_dir(project: Path) -> Path | None:
    """This project's `.git` directory, following a worktree's pointer file.

    A linked worktree has a `.git` *file* holding `gitdir: <path>`, and that is
    where its `info/exclude` lives. Returning None means there is no repository
    here, which is not an error: a project folder need not be one.
    """
    marker = project / ".git"
    try:
        if marker.is_dir():
            return marker
        if marker.is_file():
            text = marker.read_text(encoding="utf-8", errors="replace").strip()
            match = re.match(r"gitdir:\s*(.+)$", text.splitlines()[0] if text else "")
            if match:
                target = Path(match.group(1).strip())
                if not target.is_absolute():
                    target = project / target
                if target.is_dir():
                    return target.resolve()
    except OSError:
        return None
    return None


def update_git_excludes(project: Path) -> list:
    """Append AutoWeb's per-project state to `.git/info/exclude`.

    Never to `.gitignore`. That file is committed, and what belongs in it is the
    repository's decision, not a setup script's. `info/exclude` is local, does
    the same job, and leaves no diff for anyone to review.

    Only the lines that are not already there are written, so a second run
    changes nothing - including on a file somebody has reordered or commented.
    """
    gitdir = git_dir(project)
    if gitdir is None:
        return [f"no .git in {fwd(project)}, so nothing was excluded"]
    path = gitdir / "info" / "exclude"
    existing = ""
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"{fwd(path)} could not be read ({exc}); nothing was excluded"]

    present = {line.strip() for line in existing.splitlines()}
    missing = [line for line in EXCLUDE_LINES if line not in present]
    if not missing:
        return [f"{fwd(path)}: all {len(EXCLUDE_LINES)} lines already there"]

    block: list = []
    if EXCLUDE_HEADER not in present:
        block.append(EXCLUDE_HEADER)
    block += missing
    prefix = "" if (not existing or existing.endswith("\n")) else "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(prefix + "\n".join(block) + "\n")
    except OSError as exc:
        return [f"{fwd(path)} could not be written ({exc})"]
    return [f"{fwd(path)}: added {len(missing)} line(s) - {', '.join(missing)}"]


def init_project(project: Path) -> list:
    """Make *project* an AutoWeb project. Idempotent; says what it did.

    Three things, and deliberately no more: the config that opts the folder in,
    the directory goals live in, and the local git excludes. No identity (that
    needs a browser and a human), no lanes (`autoweb lanes sync` owns those and
    needs the config first), no settings file.
    """
    project = Path(project)
    actions: list = []

    toml_path = project / aw.CONFIG_FILENAME
    if toml_path.exists():
        actions.append(f"{aw.CONFIG_FILENAME} already there, left alone")
    else:
        try:
            project.mkdir(parents=True, exist_ok=True)
            toml_path.write_text(autoweb_toml_text(), encoding="utf-8", newline="\n")
            actions.append(f"wrote {aw.CONFIG_FILENAME}")
        except OSError as exc:
            actions.append(f"{aw.CONFIG_FILENAME} could not be written ({exc})")

    goals = project / aw.GOALS_DIRNAME
    if goals.is_dir():
        actions.append(f"{aw.GOALS_DIRNAME}/ already there")
    else:
        try:
            goals.mkdir(parents=True, exist_ok=True)
            actions.append(f"created {aw.GOALS_DIRNAME}/")
        except OSError as exc:
            actions.append(f"{aw.GOALS_DIRNAME}/ could not be created ({exc})")

    actions += copy_goal_template(goals)
    actions += update_git_excludes(project)
    return actions


def copy_goal_template(goals: Path) -> list:
    """Put the plugin's goal-file format next to the goals it describes.

    A goal without a checkable assertion is not a goal, and the template is
    where that is said. Copied rather than linked so the project owns its copy,
    and never over the top of one that is already there: by then it may be the
    reader's own notes.
    """
    template = aw.PLUGIN_ROOT / aw.GOALS_DIRNAME / "README.md"
    target = goals / "README.md"
    rel = f"{aw.GOALS_DIRNAME}/README.md"
    if target.exists():
        return [f"{rel} already there, left alone"]
    if not template.is_file():
        return [f"no goal template at {fwd(template)}, so {rel} was not written"]
    try:
        shutil.copyfile(template, target)
    except OSError as exc:
        return [f"{rel} could not be copied ({exc})"]
    return [f"copied {rel} from the plugin"]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="aw_setup.py",
        description="Check, install, or start an AutoWeb project.")
    ap.add_argument("--check", action="store_true",
                    help="print the check table and change nothing (the default)")
    ap.add_argument("--install", action="store_true",
                    help="install the autoweb package and a browser through uv")
    ap.add_argument("--init-project", dest="init_project", action="store_true",
                    help="write autoweb.toml, create goals/, extend .git/info/exclude")
    ap.add_argument("--json", dest="as_json", action="store_true",
                    help="print the result as JSON instead of a table")
    ap.add_argument("--cwd", default=None, metavar="DIR",
                    help="the project to look at (default: the current directory)")
    ap.add_argument("--skip-browser", dest="skip_browser", action="store_true",
                    help="--install only: do not download a browser")
    ap.add_argument("--browser", default=None, metavar="NAME",
                    help="--install only: which browser to download "
                         "(default: the project's lanes.browser, else chromium)")
    return ap


def main(argv: list | None = None) -> int:
    """Check, install then check, or init then stop. 0 when nothing gating is MISSING."""
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.install and (args.skip_browser or args.browser):
        ap.error("--skip-browser and --browser apply to --install only")
    if args.browser and args.browser.strip().lower() not in BROWSERS:
        ap.error("--browser must be one of %s" % ", ".join(BROWSERS))

    project = Path(args.cwd or os.getcwd()).resolve()
    sink = sys.stderr  # install narration never competes with stdout

    if args.init_project:
        actions = init_project(project)
        if not (args.install or args.check):
            if args.as_json:
                print(json.dumps({"ok": True, "project": fwd(project),
                                  "actions": actions}, indent=2, ensure_ascii=False))
            else:
                print_lines(actions, f"autoweb init: {fwd(project)}")
            return 0
        print_lines(actions, f"autoweb init: {fwd(project)}", out=sink)

    rep = collect(project)
    summary: list = []
    if args.install:
        if not args.as_json:
            print_table(rep, project, out=sink)
        cfg = aw.load_project_config(project)
        summary = do_install(project, resolve_browser(cfg, args.browser),
                             args.skip_browser, sink)
        rep = collect(project)

    if args.as_json:
        payload = rep.as_dict()
        if args.install:
            payload["install"] = summary
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if summary:
            print_lines(summary, "install summary:")
            print()
        print_table(rep, project)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())

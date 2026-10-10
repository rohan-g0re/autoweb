# AutoWeb as a Claude Code plugin — the build contract

This file is the contract for the plugin layer added in 0.2.0. Builders code
against it; nothing in it is aspirational. Where a fact depends on Claude Code
it carries the date it was verified. Behaviour of the `autoweb` package itself
is unchanged and documented elsewhere (`CLAUDE.md`, `docs/BUILD-SPEC.md`).

## 1. What the plugin is for

One install per machine, zero setup per project. Every app you automate is its
own project folder, and everything that belongs to that app stays in that
folder: its login identity, its config, its lanes, its goals, its runs. The
plugin ships the code and the instructions; it never ships anyone's state.

What ships, once:

| piece | path in this repo | what it does |
|---|---|---|
| the `autoweb` package | `autoweb/` | config, state export/verify, lane generator, merge, trace, CLI |
| CLI launcher | `scripts/aw.py` | runs the installed `autoweb` if present, else the shipped package in place |
| Python finder | `scripts/py.sh` | POSIX launcher that finds Python >= 3.10 for the hooks and skills |
| shared helpers | `scripts/aw_common.py` | paths, project detection, tool discovery, log |
| SessionStart hook | `scripts/session_start.py` | one status line, only in AutoWeb projects |
| setup and doctor | `scripts/aw_setup.py` | `--check`, `--install`, `--init-project` |
| skills | `skills/*/SKILL.md` | `autoweb` (model-invoked), `parallel-lanes`, `aw-setup`, `aw-doctor`, `aw-run` |
| command | `commands/setup-playwright.md` | the Playwright MCP smoke test, unchanged |
| MCP | `.mcp.json` | the shared `playwright` server, `npx @playwright/mcp@0.0.83`, for setup only |
| goal template | `goals/README.md` | the goal file format; no real goals ship |

What stays in each project, never in the plugin:

`autoweb.toml`, `root.json` (and `.bak`), `.autoweb/` (learned state, lane
profiles), `goals/*.md`, `runs/`, `lane-*.json`, `.claude/agents/lane-*.md`
(generated, carry an absolute path to that project's `root.json` and an inline
browser server), `.claude/settings.local.json` (the `mcp__laneN` grants).

## 2. Names

- Plugin `autoweb`; marketplace `autoweb`; this repository is the marketplace
  (`.claude-plugin/marketplace.json`, one entry, `source: "./"`).
- Skills are invoked as `/autoweb:<skill>`; the command as `/autoweb:setup-playwright`.
- Version lives in two places and must match: `pyproject.toml` and
  `.claude-plugin/plugin.json`. 0.2.0 is the first plugin release. A test
  asserts the match.

## 3. Layout added by 0.2.0

```
.claude-plugin/plugin.json            name autoweb, version, homepage, Apache-2.0
.claude-plugin/marketplace.json       repo doubles as the marketplace
hooks/hooks.json                      {"hooks": {"SessionStart": [...]}} and nothing else
scripts/py.sh                         POSIX, LF, invoked as `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" <script> ...`
scripts/aw_common.py                  the one place paths and discovery are decided
scripts/session_start.py
scripts/aw.py
scripts/aw_setup.py
skills/autoweb/SKILL.md               model-invoked
skills/parallel-lanes/SKILL.md        moved from .claude/skills/parallel-lanes
skills/aw-setup/SKILL.md              user-invoked (disable-model-invocation: true)
skills/aw-doctor/SKILL.md             user-invoked
skills/aw-run/SKILL.md                user-invoked
commands/setup-playwright.md          moved from .claude/commands
goals/README.md
tests/test_plugin_*.py
.github/workflows/ci.yml
AGENTS.md
CHANGELOG.md
```

The repo's own `.claude/skills/parallel-lanes` and `.claude/commands/setup-playwright.md`
are moved, not copied: with the plugin installed, a session inside this repo
would otherwise load each twice. `.claude/settings.json` stays (it is the repo's
own MCP enablement and allow list).

## 4. Verified platform facts the design leans on

All verified 2026-10-10 on Claude Code 2.1.273, Windows 11, unless marked.

- Plugin anatomy, hooks wrapper, `${CLAUDE_PLUGIN_ROOT}` substitution in hooks,
  `.mcp.json` and skill bodies (not in the Bash tool's env), SessionStart stdout
  injection, `claude plugin validate . --strict`: as recorded in the
  graph-mempalace plugin's `docs/DESIGN.md` §4, same machine, same day.
- Hooks run under Git Bash on Windows; the tool shells can carry a
  Windows-format PATH. Launchers resolve tools by absolute path.
- A plugin's stdio MCP server starts with cwd = the project directory and
  `CLAUDE_PROJECT_DIR` set. Not needed here (the only MCP server is stock
  `npx @playwright/mcp`), recorded so nobody re-tests it.
- Inline `mcpServers` in a project's `.claude/agents/*.md` load only after the
  folder is trusted (`~/.claude.json` → `projects[<path>].hasTrustDialogAccepted`),
  silently otherwise; `autoweb/lanes.py` already checks it. Whether a
  plugin-shipped agent with inline servers loads at all is UNTESTED, which is one
  more reason lanes are generated into the project and never shipped.
- The lane generator refuses to overwrite a hand-written `lane-N.md` (no
  generated marker) and prints "restart" after writing; agents and `.mcp.json`
  are read at session start only.

## 5. Runtime design

### 5.1 `scripts/aw_common.py`

Standard library only; Python 3.10+ (the package floor; no `tomllib`, config is
read through `autoweb.config` from the plugin root). Public API:

```
PLUGIN_ROOT                        this repository / the installed plugin dir
HOME, STATE_DIR (~/.autoweb-plugin), LOG_PATH, TOOLS_CACHE_PATH, PYTHON_CACHE_PATH
CLAUDE_CONFIG_DIR                  honours $CLAUDE_CONFIG_DIR
CLAUDE_JSON                        ~/.claude.json (trust lives here)
project_dir(cwd_from_payload=None) -> Path
is_autoweb_project(project) -> bool           autoweb.toml exists in that directory
load_project_config(project) -> Config|None   via autoweb.config.Config.load; None on any error, never raises
project_paths(project, cfg=None) -> dict      keys: toml, root_json, autoweb_dir, goals_dir, runs_dir, agents_dir, settings_local
goal_files(project) -> list[Path]             goals/*.md minus README.md
lane_files(project) -> list[Path]             .claude/agents/lane-*.md (any)
generated_lane_files(project) -> list[Path]   those carrying autoweb.lanes.GENERATED_MARKER
trust_accepted(project) -> bool|None          autoweb.lanes.workspace_is_trusted
find_tool(name) -> str|None                   $AW_<NAME>_BIN, tools.json cache, ~/.local/bin, uv tool env "autoweb", `uv tool dir --bin` once, PATH
uv_bin() -> str|None
uv_output(argv) -> str                        colour-stripped, NO_COLOR, --color never
autoweb_argv(project, *sub) -> list[str]      [installed autoweb, "-C", project, *sub] or [python, scripts/aw.py, "-C", project, *sub]
python_exe() -> str
append_log(line), read_json, write_json_atomic, now_stamp, _warn (stderr, deduped)
```

Rules: never print to stdout; `_warn` goes to stderr once per message; nothing
here creates project files.

### 5.2 `scripts/aw.py` — the CLI launcher

`python aw.py [autoweb args...]`. If `find_tool("autoweb")` finds an installed
binary, exec it (POSIX `os.execv`; Windows `subprocess.run` with inherited
stdio, exit with its code). Otherwise insert `PLUGIN_ROOT` at the front of
`sys.path` and call `autoweb.cli.main(argv)`. If that raises `ImportError`
for `playwright` (only `state export` and `state verify` need it), print one
line naming `/autoweb:aw-setup` and exit 2. Everything else is the package's
own behaviour and exit codes (0 pass, 1 gate failed, 2 your files are wrong).

### 5.3 `scripts/session_start.py`

Prints nothing unless `is_autoweb_project(cwd)`. Then one status line and one
instruction line, under 700 characters total, no subprocess, under two
seconds:

```
autoweb: identity root.json present · 4 of 5 lanes generated · 2 goals · trust ok
autoweb: this project automates websites with AutoWeb. Goals live in goals/, runs in runs/; use the autoweb skill or /autoweb:aw-run <goal>.
```

Variants: `identity missing (run: autoweb state export <url>)`, `no lanes
(run: autoweb lanes sync)`, `trust not granted (open the folder and accept the
prompt)`, `trust unknown`. Exits 0 always; errors go to the log.

### 5.4 `scripts/aw_setup.py`

`--check` (default), `--install`, `--init-project`, `--json`, `--cwd DIR`.

Checks, labels exact: `python >= 3.10`, `uv`, `node >= 18`, `playwright mcp
0.0.83` (npx cache warm: `npx --no-install @playwright/mcp@0.0.83 --version`
or equivalent; a cold cache is a NOTE, not a failure), `autoweb cli` (installed
binary found, or "running in place from the plugin"), `playwright package`
(importable from the installed tool env; required only for `state export`),
`browser` (per `lanes.browser` in the project's config when present, else
chromium; checks Playwright's cache or the Chrome install path), `git bash`
(Windows only), `trust` (NOTE: granted / not granted / unknown; never a failure),
`lanes` (NOTE: generated count vs `lanes.max`), `identity` (NOTE: `root.json`
present or not).

`--install`: `uv tool install --force "<PLUGIN_ROOT>"` (installs the package
and its `playwright` dependency into uv's `autoweb` tool environment and exposes
`autoweb`), then `<that env's python> -m playwright install <browser>` where
browser is `chromium` unless the project config says `chrome` and Chrome is
present. Never installs `uv` or Node; prints the official one-liners instead.
Writes nothing into any settings file.

`--init-project`: in the project directory, writes `autoweb.toml` with the
documented defaults if absent, creates `goals/` with the template if absent,
appends `.autoweb/`, `root.json`, `root.json.*`, `lane-*.json`, `runs/`,
`.claude/agents/lane-*.md`, `.claude/settings.local.json` to `.git/info/exclude`
(never to the repo's `.gitignore`), and says what it did. Idempotent.

Doctor is `--check` plus `autoweb config check` and `autoweb lanes list` run
through `aw.py`; the `aw-doctor` skill runs both.

### 5.5 Hooks

`hooks/hooks.json` has SessionStart only (timeout 15). No Stop hook. No loop.
That is a decision: AutoWeb's own assertion commands exit non-zero, so a goal is
"done" when `/autoweb:aw-run` reports every assertion passed; re-running is the
user's call.

### 5.6 Skills

- `autoweb` (model-invoked): what AutoWeb is in five lines, where things live,
  the command lines (through `aw.py`), the rule that a fabricated value fails
  the run, the lane rules in one paragraph, and a pointer to `parallel-lanes`.
- `parallel-lanes`: the existing skill, paths updated to `aw.py`.
- `aw-setup`, `aw-doctor`, `aw-run`: user-invoked, `disable-model-invocation:
  true`, script-first, each step ends in a completion criterion.
- `aw-run <goal>`: read `goals/<goal>.md`; run `autoweb config check`; run
  `autoweb state verify <target url>` when the goal names one; `autoweb lanes
  list` to see how many lanes exist; dispatch lanes per `parallel-lanes`; write
  the deliverable to `runs/<date>-<goal>/`; run the goal's assertion commands;
  report pass/fail per assertion. One pass; no loop.

### 5.7 Per-project isolation, stated

Each project is one folder. Identity (`root.json`) and lane profiles
(`.autoweb/profiles/`) are inside it; lanes run `--isolated` with
`--storage-state` and share nothing. Two projects can only leak into each other
if both set `lanes.isolated = false` and point at the same `--user-data-dir`,
which the generator never does on its own. The doctor's `lanes` NOTE prints any
`--user-data-dir` found in generated lanes that is outside the project folder.

## 6. Tests

`tests/test_plugin_*.py`, pytest, hermetic: `AW_HOME` and `CLAUDE_CONFIG_DIR`
point at temp dirs; fake tools are generated scripts on an empty PATH; nothing
touches the real `~/.autoweb-plugin`, `~/.claude.json` or the network. The
existing 282 package tests keep passing unchanged. `test_packaging.py`'s
promises (requires-python `>=3.10`, wheel packages `["autoweb"]`) stay true.

CI: `claude plugin validate . --strict` on ubuntu; pytest on ubuntu, windows
and macos across Python 3.10 and 3.12.

## 7. Rules carried over from graph-mempalace

- Hook scripts never raise and always exit 0; stdout is model-facing.
- Subprocesses get argv lists; tools by absolute path; `uv` output stripped of
  colour; `CREATE_NO_WINDOW` on Windows for probes.
- Skills reference scripts as `"${CLAUDE_PLUGIN_ROOT}/scripts/x.py"` inside a
  bash block; backtick paths, no markdown links to files.
- Version bumps happen in a release commit with a CHANGELOG entry.
- Nothing tracked names a person, a machine path, or a profile directory.

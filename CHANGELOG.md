# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Versions are
pinned: Claude Code treats `version` in `plugin.json` as the signal that an update
exists, so a release bumps it, bumps `pyproject.toml` and `autoweb/__init__.py` to
match, and adds an entry here, in one commit. A test asserts the manifest and the
package agree.

## [0.2.0] - 2026-10-10

First plugin release. The `autoweb` package itself is unchanged; everything below is the
layer that installs it. `docs/PLUGIN-DESIGN.md` is the contract.

### Added

- Claude Code plugin packaging: a `plugin.json` manifest and a single-entry
  `marketplace.json` in the same repository, installed as `autoweb@autoweb`.
- One hook in `hooks/hooks.json`: `SessionStart`. It prints nothing outside an AutoWeb
  project, and inside one prints a status line — identity, lanes generated, goals, trust
  — plus one line telling the model where things live. There is no `Stop` hook and no
  loop; that is a decision, recorded in the contract.
- `scripts/aw_common.py` — the one place paths, project detection, tool discovery and
  the log are decided. Standard library only.
- `scripts/py.sh` — POSIX launcher that finds a Python 3.10 or newer for the hooks and
  skills.
- `scripts/aw.py` — CLI launcher. Runs the installed `autoweb` binary when there is one,
  otherwise the shipped package in place, and names `/autoweb:aw-setup` when the one
  optional dependency (`playwright`, needed by `state export` and `state verify`) is
  missing.
- `scripts/session_start.py` — the SessionStart hook.
- `scripts/aw_setup.py` — `--check`, `--install`, `--init-project`. `--check` is the
  prerequisite report; `--install` installs the package and a browser through `uv`;
  `--init-project` writes `autoweb.toml`, `goals/` and the `.git/info/exclude` entries
  for one project, idempotently.
- Five skills: `autoweb` (model-invoked) and `parallel-lanes`, plus `aw-setup`,
  `aw-doctor` and `aw-run`, which are user-invoked only.
- `goals/README.md` — the goal file format, as a template. No real goals ship.
- `AGENTS.md` — the authoring standard for this repository, each rule with its reason.
- This changelog.
- Plugin tests (`tests/test_plugin_*.py`), hermetic: a temporary `AW_HOME` and
  `CLAUDE_CONFIG_DIR`, fake tools on an empty PATH, no network.
- CI: `claude plugin validate . --strict` on Linux, and pytest on Linux, Windows and
  macOS across Python 3.10 and 3.12.

### Changed

- The `parallel-lanes` skill moved from `.claude/skills/` to `skills/`, and the
  `setup-playwright` command from `.claude/commands/` to `commands/`. They are moved
  rather than copied: with the plugin installed, a session inside this repository would
  otherwise load each of them twice. `.claude/settings.json` stays, as the repository's
  own MCP enablement and allow list.
- The version now lives in two files that must agree, `pyproject.toml` and
  `.claude-plugin/plugin.json`, and `autoweb/__init__.py` tracks it.
- The project's own goal files were removed. Goals are per-project state and the plugin
  ships none.
- Documentation scrubbed: no personal paths, profile directories or account names in
  anything tracked. Measurements stay — a 1.9 GB Chrome profile exporting to 1.5 MB is a
  fact about Chrome, not about a person.

## [0.1.0] - 2026-10-03

The package as it stood before the plugin layer. Never tagged; recorded here so the
0.2.0 entry has something to be a change from.

### Added

- The `autoweb` Python package: `config.py`, `state.py`, `lanes.py`, `merge.py`,
  `trace.py`, `cli.py`. Flat layout, hatchling wheel, Python 3.10 floor.
- Nine CLI commands: `config show`, `config check`, `state export`, `state inspect`,
  `state verify`, `lanes sync`, `lanes list`, `trace`, `merge`. Four of them are
  assertions and exit non-zero when what they check does not hold.
- `root.json` as the shared identity: a `storageState` JSON file rather than a Chrome
  profile directory, because `launchPersistentContext({storageState})` launches, reports
  success and silently discards the state.
- Lanes: `lanes sync` generates one `.claude/agents/lane-N.md` per lane, each declaring
  its own inline MCP server under a per-lane name, so each lane gets its own server
  process, browser and current tab.
- Merge: a three-way fold of lane states back onto `root.json`, with root as the common
  ancestor, deletions never propagated, conflicts evicting an origin rather than picking
  a winner, and partitioned cookies keyed by their partition.
- `trace`: arithmetic on the lanes' own timestamps, deciding whether they overlapped
  rather than took turns.
- `autoweb.toml` configuration with a working default for every key, and per-origin
  `indexeddb` / `rotates` / `sticky` overrides.
- An offline test suite covering config parsing, state file accounting, lane file
  generation, merge arithmetic, trace judgement and the CLI's exit codes. None of it
  opens a browser, which is why every phase also has a gate that does.

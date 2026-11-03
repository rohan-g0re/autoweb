# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Versions are
pinned: Claude Code treats `version` in `plugin.json` as the signal that an update
exists, so a release bumps it, bumps `pyproject.toml` and `autoweb/__init__.py` to
match, and adds an entry here, in one commit. A test asserts the manifest and the
package agree.

## [0.3.0] - 2026-11-03

### Added

- First real run of `aw-run` end to end with `goal check`, and the first real merge
  under the recency rule (2026-11-02, four LinkedIn lanes, 0 evicted, 11 resolved,
  7 of 7 assertions against the merged identity). Windows smoke run; Linux repeat owed.
- A second plugin in this marketplace, `autoweb-loop` 0.1.0, at `plugins/autoweb-loop/`
  and installed on its own as `autoweb-loop@autoweb`. It adds `/autoweb-loop:aw-loop` and
  a Stop hook that re-runs a goal until every assertion in its `status.json` passes or a
  cap is hit. Experimental: not yet proven in a live session. `autoweb` itself is
  unchanged and still has no Stop hook. Its tests run in this suite (`testpaths`), and a
  root test holds its `scripts/py.sh` to this one but for the message prefix.
- `autoweb goal check <goal.md> --run <run-id> [--root PATH] [--dry-run]`. It reads the
  `toml` block under a goal's `## Assertions` heading, evaluates each
  `[[assert]]` (`url`, `text` and `element` in a fresh headless browser seeded from
  `root.json`, judged after the page settles; `command` through the shell in the project
  root), and writes `runs/<run-id>/status.json` with `schema`, `goal`, `run`,
  `checked_at`, `assertions` and `done`. Exit 0 only when every assertion passed, 1 when
  any failed or could not be evaluated, 2 for a malformed goal or a missing input.
  `--dry-run` parses and prints the plan and touches nothing.
- `autoweb/goal.py` and `tests/test_goal.py`.
- `aw_setup.py --init-project` writes `<project>/.mcp.json` with the `playwright` server
  pinned to `lanes.mcp_version`, merges it beside existing servers, leaves an existing
  `playwright` entry or an unparsable file alone, and adds `"playwright"` to
  `enabledMcpjsonServers` in `.claude/settings.local.json`.
- Doctor row `shared browser server`: OK when `.mcp.json` pins the configured version;
  MISSING (gating) when absent, unparsable or without a `playwright` entry; NOTE when the
  pin differs, and NOTE outside an AutoWeb project.
- The SessionStart status line adds `shared browser: not set up (run /autoweb:aw-setup)`
  when the project declares no `playwright` server.

### Removed

- The plugin-root `.mcp.json`. Claude Code loads a plugin's `.mcp.json` in every session
  on the machine, so `plugin:autoweb:playwright` started an npx Playwright server in every
  project of every user, including projects that never ran AutoWeb. The plugin now ships
  no MCP server. Projects that relied on it run `/autoweb:aw-setup` once and restart the
  session.
- `autoweb goal check` is what a loop plugin reads: `runs/<id>/status.json` is the
  contract between the two plugins, and nothing in `autoweb` references the loop.

### Changed (merge)

- `autoweb merge` no longer evicts a whole host when lanes change one value differently.
  It keeps the most recently issued value: later cookie `expires` first (session cookies
  rank lowest), then the lane file saved last. localStorage and IndexedDB use save time
  only. Each pick is printed as a `resolved:` line.
- Eviction now happens only on hosts marked `rotates = true` in `autoweb.toml`, where a
  conflict still drops cookies and storage together. `sticky` still overrides it, so a
  host that is both is resolved by recency.
- IndexedDB is merged per database and per object store. Lanes that changed different
  databases no longer conflict.
- `MergeResult.rotating_violations` and `MergeResult.volatile_conflicts` are gone,
  replaced by `MergeResult.resolved`. `merge()` takes a new optional `saved_at` argument.

### Removed (merge)

- The bot-management allowlist (`VOLATILE_COOKIE_NAMES`, `VOLATILE_STORAGE_MARKERS` and
  helpers). Any per-browser cookie it lacked, such as Google `SIDCC`, GA4 `_ga_*` or
  Datadog `_dd_s`, could sign the identity out on a read-only run.
- The "touched by N lanes" warning for `rotates` hosts. It fired on every multi-lane
  run, because isolated lanes export every root cookie.

### Changed

- The goal format: `## Assertion` (prose) becomes `## Assertions` (a `toml` block). The
  singular heading is still read. An observation is now a `command` that checks the
  deliverable.
- `/autoweb:aw-run` dry-runs the goal's assertions before dispatching lanes, and judges
  the run with `autoweb goal check` instead of reading lane reports. It is complete when
  `status.json` says `done: true` or the report names what failed.
- README: pass or fail is decided by `autoweb goal check`, not by the model. Before this,
  "no model grades its own work" was not true: `aw-run` had the model judge observation
  assertions from the lanes' own reports.

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
  `--init-project` writes `autoweb.toml`, `goals/`, a managed `.gitignore` block and
  the `.git/info/exclude` entries for one project, idempotently.
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

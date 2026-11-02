# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Claude Code
treats `version` in `plugin.json` as the signal that an update exists, so a release bumps
it and adds an entry here, in one commit. This plugin's version lives only in its
`.claude-plugin/plugin.json`; the repository's own `CHANGELOG.md` records `autoweb`.

## [Unreleased]

### Changed

- Moved into the `autoweb` repository as `plugins/autoweb-loop/` and listed in its
  marketplace, so it installs as `autoweb-loop@autoweb`. The standalone `autoweb-loop`
  marketplace is gone, and so are this plugin's own `pyproject.toml`, `uv.lock`,
  `.gitignore`, `.gitattributes` and `AGENTS.md`: the repository's govern. A
  `ruff.toml` extends the repository's ruff settings and keeps this plugin's rule
  selection, which the dropped `pyproject.toml` used to name.
- `scripts/py.sh` prints `autoweb-loop:` in its messages. Every code line is still the
  `autoweb` copy's, and a root test fails when the two drift.
- The tests run in the repository's suite. `test_manifests.py` is now
  `test_loop_manifests.py`, and the helpers the tests import moved from `conftest.py` to
  `loop_helpers.py`, because the root suite has a `conftest` module of its own.

## [0.1.0] - 2026-11-01

First release. `docs/DESIGN.md` is the contract.

### Added

- One skill and so one command, `/autoweb-loop:aw-loop <goal.md> [--max N]`, with
  `--stop` and `--status`. User-invoked only.
- One hook, `Stop`. It exits at once, silent, in any folder without an active loop. With
  one, it reads `runs/<run-id>/status.json` from `autoweb goal check` and either lets
  Claude stop (all passed, cap reached, or stalled) or blocks with the failing
  assertions as the reason.
- `scripts/aw_loop.py`: writes, removes and reports `.autoweb/active-loop.json`, and
  finds the installed `autoweb` plugin's `aw-run` skill.
- `scripts/loop_stop.py`, `scripts/loop_common.py`, and `scripts/py.sh` copied verbatim
  from `autoweb`.
- An offline test suite that runs the hook and the writer as subprocesses through
  `py.sh`.

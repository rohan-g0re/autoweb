# Authoring standard

Rules for anyone — person or agent — changing this repository. Each rule carries its
reason, because a rule without one gets dropped the first time it is inconvenient.
`docs/PLUGIN-DESIGN.md` is the contract for the plugin layer and `CLAUDE.md` holds the
design rules for the package; this file is how to write to them.

## Scripts (`scripts/*.py`, `scripts/*.sh`)

- [ ] Python 3.10 or newer, standard library only. Reason: 3.10 is the package's
      `requires-python` floor, so a script that needs 3.11 breaks an install the
      packaging promises works. A third-party import in a hook is a failure mode on a
      machine we do not control. Note `tomllib` arrived in 3.11: read config through
      `autoweb.config`, never directly.
- [ ] Every path, name, project test and tool lookup goes through `aw_common`. Reason:
      the hook, the launcher, setup and doctor have to agree by construction, not by
      four copies of the same string.
- [ ] Hook scripts never raise and always exit 0. Wrap `main()` in a `try/except` that
      logs under `~/.autoweb-plugin/`. Reason: a hook that fails blocks or spams the
      user's session, and the user cannot tell our bug from theirs.
- [ ] Hook stdout is model-facing. Print only what the model should read; debug goes to
      the log file. Reason: stdout is injected straight into context.
- [ ] Reconfigure stdout and stderr to UTF-8 at the top of every script. Reason: Windows
      consoles default to cp1252 and the status line contains `·`.
- [ ] Subprocesses get `argv` lists, never shell strings, and tools come from
      `find_tool` by absolute path. Reason: hooks run with a PATH we did not set, which
      on Windows can be in Windows format inside Git Bash; home directories contain
      spaces.
- [ ] `uv` output is read with colour stripped (`NO_COLOR`, `--color never`), and probes
      on Windows pass `CREATE_NO_WINDOW`. Reason: escape codes break parsing, and a
      console window flashing on every session start is a visible defect.
- [ ] Nothing in `scripts/` writes into a project except `aw_setup.py --init-project`,
      and that one is idempotent and says what it did. Reason: a plugin that edits your
      folder when you did not ask is not a plugin you keep installed.
- [ ] Shell launchers are POSIX `sh` with LF line endings, invoked as
      `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh"`, never relying on an exec bit or a
      shebang. Reason: NTFS has no exec bit and CRLF breaks `#!`.
- [ ] Unit tests for every decision branch, with fake tools on a temporary PATH, a
      temporary `AW_HOME` and a temporary `CLAUDE_CONFIG_DIR`. Reason: portability is a
      test, not an intention, and the suite must never touch a real `~/.claude.json`.

## Skills (`skills/<name>/SKILL.md`)

- [ ] `name` equals the directory name, kebab-case, no colons. Reason: the validator and
      Windows paths.
- [ ] `description` is one tight sentence of what and when, quoted if it contains a
      colon, carrying the literal phrases a user would type. Reason: every session pays
      for the description; triggering depends on those phrases.
- [ ] User-invoked skills set `disable-model-invocation: true`. Reason: they orchestrate
      a run; they should not fire on their own.
- [ ] Body: imperative steps, each ending in a completion criterion. Scripts do the
      work; the skill runs them and presents the result. Reason: deterministic work in a
      script costs a fraction of the tokens and never drifts.
- [ ] Reference bundled files as `"${CLAUDE_PLUGIN_ROOT}/scripts/x.py"` inside the bash
      block. Reason: that is the one place the variable is substituted — the Bash tool's
      own environment does not carry it.
- [ ] Supporting material goes in `references/*.md` beside `SKILL.md`, named by a
      backtick path. No markdown links to files. Reason: a link is read as a
      cwd-relative path and fails.
- [ ] Positive instructions. Say what to do, not what to avoid. Reason: a prohibition
      drags the forbidden behaviour into context.
- [ ] Delete sentences the model already obeys by default. Reason: a no-op costs load
      and says nothing.

## Hooks and manifests

- [ ] `hooks/hooks.json` is wrapped in a top-level `"hooks"` key. Reason: the unwrapped
      event map fails to load, silently, as a file.
- [ ] Shell-form commands quote the variable: `bash "${CLAUDE_PLUGIN_ROOT}/..."`.
      Reason: spaces in paths; the validator warns otherwise.
- [ ] Every manifest path starts with `./`. Reason: validation fails otherwise.
- [ ] `homepage` is a valid URL. Reason: it is the one metadata field that stops the
      plugin loading.
- [ ] Run `claude plugin validate . --strict` after touching any manifest, skill or
      hook. Reason: an unknown key loads silently and does nothing otherwise.
- [ ] Do not bump `version` in a feature change. A release does it, in one commit, with
      a `CHANGELOG.md` entry and the same number in `pyproject.toml`,
      `.claude-plugin/plugin.json` and `autoweb/__init__.py`. Reason: pin semantics —
      users update when the number changes, and a test asserts the files agree.
- [ ] A lane agent file is generated into a project, never shipped. Reason: it carries an
      absolute path to that project's `root.json`, and whether a plugin-shipped agent
      with inline `mcpServers` loads at all is untested.

## Docs

- [ ] Plain language. Short words. One idea per sentence. Reason: readers include people
      who are not native English speakers and agents with a token budget.
- [ ] Every claim about behaviour is either tested, or carries the date and the version
      it was verified on. Reason: harness and browser behaviour changes under you.
      `docs/CONSTRAINTS.md` labels each claim **live** or **read-from-source** and that
      distinction is the only reason the file is worth anything.
- [ ] Prove it, then claim it. "It works" means a browser opened and acted in a
      transcript you can point at, or a count taken from outside the run. Reason: every
      serious defect in this repository's history survived a green test suite.
- [ ] `README.md` is organised by the reader's situation — install, first project,
      running a goal, configuring, fixing — not by component. Reason: people look for
      things by their problem.
- [ ] Install commands appear in exactly one place in the README and are copied, not
      retyped, anywhere else. Reason: drift.
- [ ] Nothing tracked names a person, a machine path, a home directory or a browser
      profile directory. A measurement stays: "a 1.9 GB Chrome profile exported to
      1.5 MB" is a fact about Chrome. Reason: this repository is public, and content
      outlives the person who measured it.
- [ ] Do not restate the design in a second file. Point at
      `docs/PLUGIN-DESIGN.md`. Reason: two copies of a contract means one is wrong and
      nobody knows which.

## Quick checks

```bash
claude plugin validate . --strict
.venv/Scripts/python.exe -m pytest -q    # or: uv run pytest -q
git grep -n -i -E "rohan|pursuit|/home/|C:\\\\Users" -- . ':!uv.lock' ':!northstar.md'
grep -rnE '\]\((\./)?(references|scripts)/' skills/    # markdown links to files: none
```

The grep for personal data should print only the GitHub project URLs. The grep over
`skills/` should print nothing at all.

# autoweb-loop — the design

This is the contract for the plugin. Every claim about Claude Code carries its source
and the date it was read. Behaviour of the `autoweb` plugin belongs to its own contract,
`docs/PLUGIN-DESIGN.md` at the repository root; this file states only the part of it
this plugin depends on.

## 1. What it is

A Claude Code plugin with one skill, one hook and no state of its own outside the
project it runs in. `/autoweb-loop:aw-loop <goal.md> [--max N]` starts a loop; a Stop
hook keeps the session working until the goal's assertions pass or the cap is hit.

It is a separate product from `autoweb`. `autoweb` makes one pass per goal and never
references this plugin. Nothing in `autoweb` reads or writes `.autoweb/active-loop.json`.

## 2. Names

- Plugin `autoweb-loop`, in the `autoweb` marketplace (this repository, at
  `plugins/autoweb-loop/`), installed as `autoweb-loop@autoweb`.
- The one skill and so the one command: `aw-loop`, typed as `/autoweb-loop:aw-loop`.
  Every skill or command this plugin ever adds is prefixed `aw-`.
- Scripts: `scripts/py.sh` (Python finder), `scripts/loop_common.py` (names, paths,
  formats), `scripts/loop_stop.py` (the hook), `scripts/aw_loop.py` (the writer).

## 3. The contract with `autoweb`

Fixed. The `autoweb` plugin implements the other side (`docs/PLUGIN-DESIGN.md` §8 at the
repository root).

- `aw-run` ends every run with `autoweb goal check <goal> --run <run-id>`, which writes
  `runs/<run-id>/status.json`:

  ```json
  {"schema": 1, "goal": "goals/x.md", "run": "2026-11-01-x", "checked_at": "...Z",
   "assertions": [{"id": "feed-loads", "kind": "url", "status": "PASS|FAIL|ERROR", "detail": "..."}],
   "done": false}
  ```

  `done` is true if and only if every assertion is `PASS`.
- Run ids are `YYYY-MM-DD-<goal-slug>`, as `aw-run` names them. This plugin uses the goal
  file's stem as the slug, and `--run` overrides it.
- An AutoWeb project is a folder with `autoweb.toml`.

## 4. The loop file

`.autoweb/active-loop.json`, written only by `aw_loop.py --start`, removed only by the
hook or by `aw_loop.py --stop`:

```json
{"goal": "goals/x.md", "run": "2026-11-01-x", "max_iterations": 5, "iteration": 1,
 "started": "2026-11-01T10:00:00Z", "session": "<session id or null>"}
```

Two fields beyond the five the brief named, each for a failure it prevents:

- `session`: the session that owns the loop, from `${CLAUDE_SESSION_ID}` in the skill.
  A second session in the same folder is never held by someone else's loop. When the
  substitution did not happen, `--start` stores `null` and the first Stop that sees the
  loop claims it.
- `last_status`: added by the hook when it blocks, a fingerprint (`checked_at` plus
  mtime) of the `status.json` it blocked on, or `null` when there was none. §6 says why.

`iteration` counts passes, starting at 1. `--max N` therefore allows N passes in all.

## 5. The hook's decisions

`hooks/hooks.json` registers one Stop hook:
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/loop_stop.py"`,
timeout 10 seconds.

1. Find the loop: walk up from the payload's `cwd` to the first directory holding
   `autoweb.toml`, looking for `.autoweb/active-loop.json`. None: exit 0, no output, no
   write, no log. This is the path every Stop everywhere takes, so it is two `stat`
   calls per level and nothing else. The walk exists because `cwd` follows a `cd` (§8).
   A `cwd` this Python cannot see, such as an MSYS path handed to a Windows Python, falls
   back to the process directory.
2. Unreadable stdin, loop file or `status.json`: exit 0, no output, traceback to
   `.autoweb/loop.log`. Nothing is counted.
3. The loop belongs to another session: exit 0, no output.
4. `status.json` exists, `done` is true and no assertion is non-PASS: delete the loop
   file, exit 0, no output. A `checked_at` earlier than the loop's `started` counts as
   missing: it was written by an earlier run with the same id.
5. A stall (§6): delete, exit 0 with a `systemMessage`.
6. `iteration >= max_iterations`: delete, exit 0 with a `systemMessage` naming what
   still fails, or saying no `status.json` was written.
7. Otherwise: `iteration += 1`, record `last_status` and the session, write the file,
   print `{"decision": "block", "reason": "iteration k of N; failing: <id> [<status>]:
   <detail>; ... <what to do>"}`. A missing `status.json` blocks the same way, with
   "no status.json at runs/<run>/status.json" and the `goal check` line that writes it.

Details are cut at 300 characters each and the reason at 2,000. Every exit is 0. The
hook never uses exit 2, because Claude Code reads stderr from an exit 2 as the reason to
keep going (§8), and an error message is not an instruction.

## 6. `stop_hook_active` and the cap: why both

The hooks page says `stop_hook_active` is true "when Claude Code is already continuing
as a result of a stop hook", and to "check this value or process the transcript to avoid
blocking on a condition that will never resolve". A loop has to block while it is true;
that is what a loop is. So the hook does not step aside on it. It asks the narrower
question the guidance is about: has anything changed since I last blocked? When
`stop_hook_active` is true and `status.json` has the same fingerprint as at the last
block, Claude came back from the block and ended its turn without running a new check.
Blocking again would repeat the same instruction, so the loop ends and the user is told.
The check applies only after this hook has blocked once, because `stop_hook_active` is
also true when another plugin's Stop hook did the blocking.

The cap is the real guard, and the stall rule cannot replace it. A goal that is checked
every pass and fails every pass changes `status.json` each time, so by the stall rule it
is making progress, and it would run for ever. Claude Code's own cap does not cover it
either: it overrides a block after eight consecutive continuations, but "the count of
consecutive continuations resets each time Claude calls a tool", and every pass calls
many. The cap is on disk, so it also survives the session ending and resuming.

## 7. Running `aw-run` from the loop

`autoweb`'s `aw-run` skill sets `disable-model-invocation: true`. The skills page says
Claude "can't invoke the skill on its own", and that if it tries, Claude Code blocks the
call. So the loop cannot start `aw-run` through the Skill tool. Instead `aw_loop.py
--start` finds the installed `autoweb` plugin in `plugins/installed_plugins.json` under
the Claude config directory (an install for this project first, then a user install;
`AW_LOOP_AUTOWEB_ROOT` overrides it) and prints the path of `skills/aw-run/SKILL.md` and
the plugin root. The `aw-loop` skill reads that file and follows it, writing the printed
root wherever it uses the plugin-root variable and the printed run id for the run
directory. The user typed `/autoweb-loop:aw-loop`, which is the request to run `aw-run`
repeatedly. `--start` refuses when no `autoweb` install is found.

This is the weakest joint in the design. It depends on the registry's file layout
(read on Claude Code 2.1.299, 2026-11-01: `{"version": 2, "plugins":
{"autoweb@autoweb": [{"scope", "installPath", "version", ...}]}}`), which is not a
documented interface. A future `autoweb` that ships the `aw-run` steps as a plain
reference file would remove it.

## 8. Platform facts relied on

Read from the documentation on 2026-11-01. The machine runs Claude Code 2.1.299.

From https://code.claude.com/docs/en/hooks:

- Stop input: the common fields (`session_id`, `transcript_path`, `cwd`,
  `permission_mode`, `hook_event_name`) plus `stop_hook_active`,
  `last_assistant_message`, `background_tasks` and `session_crons`.
- "`cwd` follows Claude: the `cwd` field in the hook's input JSON is the worktree root
  after Claude enters a worktree, and the new directory after Claude runs `cd`."
- `decision: "block"` "prevents Claude from stopping"; `reason` is "Required when
  `decision` is `"block"`. Tells Claude why it should continue". Omitting `decision`
  lets Claude stop.
- Exit 0 is "the intended exit code when you print JSON for structured control"; stdout
  counts as JSON "only when you print one JSON object ... with nothing around it but
  whitespace". A Stop hook's other stdout goes to the debug log only.
- Exit 2 on Stop "sends Claude back to keep working", with stderr as the reason.
- `systemMessage`: "Warning message shown to the user."
- Claude Code's own cap: "after stop hooks have continued the turn eight times in a row,
  Claude Code overrides the next block and ends the turn. The count of consecutive
  continuations resets each time Claude calls a tool." `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`
  raises it.
- Stop "does not run if the stoppage occurred due to a user interrupt".
- `timeout` is in seconds.
- Plugin hooks live in `hooks/hooks.json`, wrapped in a top-level `"hooks"` key.

From https://code.claude.com/docs/en/plugins-reference (now titled "Plugin manifest
reference"):

- `${CLAUDE_PLUGIN_ROOT}` is the "absolute path of the plugin's installed version", and
  it "changes when the plugin updates, so don't write state there". It is substituted in
  hook commands and in skill bodies, and is not in the Bash tool's environment.
- "A file that contains only the event map, without that wrapper, fails to load."
- `plugin.json`: only `name` is required; `homepage` "must parse as a URL, or the plugin
  fails to load".

From https://code.claude.com/docs/en/plugin-marketplaces and the marketplace reference
it links to: `name`, `owner` and `plugins` are required; a relative `source` "must start
with `./`" and resolves from the directory that holds `.claude-plugin/`.

From https://code.claude.com/docs/en/skills: plugin skills are invoked as
`/plugin-name:skill-name`; `disable-model-invocation: true` means Claude "can't invoke
the skill on its own"; `$ARGUMENTS` holds the arguments; `${CLAUDE_SESSION_ID}` is "The
current session ID".

Not confirmed by the documentation, and so not relied on beyond what is stated:

- Where a Stop hook's `systemMessage` appears. The Stop section does not say; the
  general rule is that it is shown to the user unless an event's section says otherwise.
- Whether a resumed session keeps its `session_id`. If it does not, a resumed session
  no longer matches its loop and is let stop, which is the safe direction.
- The default timeout for a Stop command hook. This plugin sets 10 seconds.

## 9. `py.sh`

The `autoweb` plugin's `scripts/py.sh`, 3.11 floor included, so the two plugins find
Python the same way. Only the message prefix (`autoweb-loop:`) and two comment lines
differ; `tests/test_plugin_manifests.py` at the repository root fails when any code line
drifts, so a fix to one is a fix to both. It shares `autoweb`'s interpreter cache at
`~/.autoweb-plugin/python_path`, which is harmless because both want the same thing.

## 10. Tests

`tests/`, pytest, collected by the repository's own `uv run pytest` (its `testpaths`
names this directory). The fixtures locate the scripts relative to their own file. The hook
and the writer are run as subprocesses through `bash scripts/py.sh`, the command the
hook file names, with crafted stdin and temporary projects. `AW_HOME`, `AW_PYTHON` and
`CLAUDE_CONFIG_DIR` point at temporary values, so nothing touches the developer's
machine state. Covered: the silent path (exit 0, empty output, nothing written, under one
second), done, cap, failing, missing and stale `status.json`, the stall rule, another
plugin's block, another session's loop, a `cwd` reached by `cd`, malformed input of
every kind, `--start`, `--stop`, `--status`, and the manifests.

Not covered: a live session. No test starts Claude Code, so that the hook is loaded,
blocks a real turn and is obeyed is a claim from the documentation, not a measurement.

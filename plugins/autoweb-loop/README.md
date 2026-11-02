# autoweb-loop

A Claude Code plugin that keeps a session working on one AutoWeb goal until every
assertion in the goal passes, or until an iteration cap is hit.

The `autoweb` plugin runs a goal once. It ends each run with
`autoweb goal check`, which writes `runs/<run-id>/status.json`. This plugin adds a Stop
hook that reads that file when Claude tries to end its turn. When an assertion still
fails, the hook sends Claude back to work, and its reason names what failed. When every
assertion passes, it lets Claude stop.

It is a separate plugin on purpose. `autoweb` makes one pass and leaves re-running to
you. Install this one only if you want the re-running done for you. Both plugins live in
one repository and one marketplace, and each is installed on its own.

Experimental: the hook and the writer are covered by an offline test suite, but no live
Claude Code session has yet been seen to load the hook, block a turn and obey it.

## Before you install

You need the `autoweb` plugin installed and set up in the project, in a version that has
`autoweb goal check`. This plugin reads the files it writes and runs its `aw-run` skill; it does
nothing on its own. Python 3.11 or newer is found the same way `autoweb` finds it.

## Install

```
/plugin marketplace add rohan-g0re/autoweb
/plugin install autoweb-loop@autoweb
```

The first line is the one that installs `autoweb` too; skip it when that marketplace is
already added.

Start a new session afterwards. Hooks are read when a session starts.

## Run a goal until it passes

In an AutoWeb project (a folder with `autoweb.toml`):

```
/autoweb-loop:aw-loop goals/daily-digest.md
/autoweb-loop:aw-loop daily-digest --max 3
```

This is the only command. The goal can be written as `daily-digest`, `daily-digest.md` or
`goals/daily-digest.md`. `--max` is the most passes the loop makes, counting the first;
the default is 5.

What happens:

1. The skill writes `.autoweb/active-loop.json` with the goal, the run id
   (`YYYY-MM-DD-<goal>`), the cap and the iteration count.
2. Claude runs the `aw-run` skill for the goal, to the end, and ends its turn.
3. The Stop hook reads `runs/<run-id>/status.json`. If something failed, it blocks the
   stop and tells Claude which assertions failed and why. Claude changes its approach
   and does another pass with the same run id. The goal file is never edited.
4. This repeats until the loop ends.

`/autoweb-loop:aw-loop --status` shows the loop, the last check and the log.

## How the loop stops

The hook removes `.autoweb/active-loop.json` and lets the session stop when:

- every assertion passes. It says nothing.
- the cap is reached. It tells you which assertions still fail.
- Claude comes back from a block and ends its turn without running a new
  `autoweb goal check`. A second block would repeat the same instruction, so it tells
  you the loop stalled.

To stop it yourself at any time, run `/autoweb-loop:aw-loop --stop`, or delete
`.autoweb/active-loop.json`. Pressing Esc also works: Claude Code does not run Stop hooks
after a user interrupt.

## What the hook does in every other folder

The hook is registered for every Stop, in every project and every session, because
Claude Code has no way to register a hook for one folder from a plugin. Each time, it
checks whether `.autoweb/active-loop.json` exists in the session's folder or a parent of
it up to the project root. When it does not, the hook exits at once: no output, no file
written, no log. That costs one Python start, measured at about 0.4 s on a Windows
laptop. A loop is also tied to the session that started it, so a second session in the
same folder is never held by it.

## When something goes wrong

- The hook never blocks on its own errors. On any error it lets Claude stop and writes
  the traceback to `.autoweb/loop.log`, but only in a folder where a loop is active.
- `/autoweb-loop:aw-loop --status` reads the loop file and the run's `status.json`, and
  prints the last lines of `loop.log`.
- "the autoweb plugin is not installed": install `autoweb@autoweb` first. A checkout
  loaded with `--plugin-dir` can be named with `AW_LOOP_AUTOWEB_ROOT=<its folder>`.

## Files

| Path | Written by | Holds |
|---|---|---|
| `.autoweb/active-loop.json` | this plugin only | the active loop; removed when it ends |
| `.autoweb/loop.log` | this plugin only | hook errors, only while a loop is active |
| `runs/<run-id>/status.json` | `autoweb goal check` | the assertions and `done`; read here |

`docs/DESIGN.md` is the design, with the Claude Code documentation it relies on and the
date each fact was read.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.

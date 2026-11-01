---
name: autoweb
description: "Drives a real browser from a goal file, with a saved login and optional parallel lanes, and reports every fact with the URL it came from. Use for 'automate this website', 'log in and collect', 'run the goal', 'browser lanes', 'fill the form on', or any task that needs a browser session the user already signed into."
---

# AutoWeb

AutoWeb is the thinnest connection between this session and a real browser. The engine
is Claude Code; the browser is `@playwright/mcp`; everything in between is markdown on
disk. A login done by hand once is exported to a JSON file, and every later browser —
including each parallel lane — is seeded from that file instead of logging in again.
A goal is an end state plus the assertion that proves it was reached.

## Where things live

Everything belongs to the project directory, and **nothing is shared between
projects**. Two projects that automate two sites keep two identities, two configs and
two sets of lanes, and neither can see the other's.

| path | what it is |
|---|---|
| `autoweb.toml` | the project's config: `lanes.max`, `lanes.browser`, `lanes.isolated`, `state.root`, caps, per-origin flags |
| `root.json` | the identity. Cookies, localStorage and IndexedDB from a login done by hand. Never committed |
| `.autoweb/` | learned state and, when `lanes.isolated = false`, the per-lane profile directories |
| `goals/` | one markdown file per goal: target, resources, the work, the assertion. `goals/README.md` is the format |
| `runs/` | one directory per run, `runs/<YYYY-MM-DD>-<goal>/`, holding the deliverable and `run-log.md` |
| `.claude/agents/lane-N.md` | the lane subagents, generated. Each carries an absolute path to this project's `root.json` and its own inline browser server |

## The commands

All of them run through the plugin's launcher, which uses the installed `autoweb` when
there is one and the shipped package otherwise. `-C "$PWD"` names the project, because
the config and the identity are found from there and not from the plugin:

```bash
# effective config, defaults included, and where root.json is
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" config show
# assertion: autoweb.toml parses and every key is one that exists
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" config check
# headed browser; the user logs in by hand, then the session is saved
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" state export <url>
# measure the identity: origins, cookies, IndexedDB, bytes
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" state inspect
# assertion: a brand new isolated browser, given only the JSON, is still signed in
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" state verify <url>
# write .claude/agents/lane-N.md from config, one per lane
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" lanes sync
# which lane files exist, and which of them are generated
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" lanes list
# assertion: did the lanes really overlap, from the marks they took
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" trace <trace.json>
# fold lane state back onto root.json, three-way, --dry-run first
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" merge <lane.json>...
```

Exit codes: **0** the command did its job, **1** a gate it checks did not hold, **2** the
user's files are wrong and name the file or the key. `config check`, `state verify` and
`trace` are assertions rather than tools, so a non-zero exit from one of those is the
answer, not an error to retry. `state export` joins them when it captures nothing at all,
because a state file that parses and holds no session is the failure most easily mistaken
for a success.

`state verify` takes `--expect-url` and `--expect-text`, and `merge` takes `--dry-run`.
Read the dry run before writing: an `evicted` line means two lanes changed the same value
differently, and that origin is dropped rather than guessed at.

## Rules for the run

- **A sub-goal that cannot be completed is reported as blocked, with the reason.** "This
  needed a login I do not have" is a true and useful result. Say which sub-goal, and what
  stopped it.
- **A fabricated value fails the whole run**, not just the step it appears in. A plausible
  number nobody read off a page is worse than a gap, because the gap is visible and the
  number is not.
- **Confirm `location.href` before asserting.** A click that silently did nothing, a
  redirect to a login wall, and a success page all read the same way in a snapshot taken
  from memory. Check where the browser actually is, then assert.
- **Every fact carries the URL it came from.** A price, a status, a row count: name the
  page. When two sources disagree, believe neither until you know which one loaded.

## When to use lanes

Read the `parallel-lanes` skill when the task names **several independent sites, accounts
or records** that each get the same treatment — four price checks on four retailers is
four lanes. One chain of steps on one site is one lane, and splitting it wins nothing
because step two cannot start until step one finishes.

```bash
cat "${CLAUDE_PLUGIN_ROOT}/skills/parallel-lanes/SKILL.md"
```

That skill holds the parts that are easy to get wrong: dispatching every lane in one
message, the generated-lane ceiling, keeping a token-rotating site in one lane, harvesting
each lane's state before its browser closes, and merging once at the end.

Two prerequisites are load-bearing. A lane only gets its own browser once the **project
folder is trusted** — Claude Code refuses to start an agent file's inline `mcpServers`
block otherwise, and it does so silently, so an untrusted folder dispatches lanes and
starts zero browsers. And a lane that calls `mcp__playwright__*` instead of its own
`mcp__laneN__*` tools has landed in the session's shared browser, which voids its
isolation and everything it reports about which page it was on.

## When there is no identity

`config show` prints `root.json`'s path and whether it exists. When it does not, tell the
user to create it and stop:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" state export <url>
```

That opens a headed browser and waits. **The user logs in by hand; AutoWeb never sees a
password** — it reads the session afterwards and writes the JSON. This is not something to
work around: without `root.json` an isolated lane does not merely start logged out, every
browser call it makes fails with ENOENT and it comes back having done nothing.

## There is no loop

One run, then the assertions decide. AutoWeb's own assertion commands exit non-zero when
they fail, so a goal is done when every assertion in it passed and reported pass. Nothing
here retries, heals or re-plans on a failure: report which assertion failed and why, and
leave re-running to the user. `/autoweb:aw-run <goal>` is that single pass.

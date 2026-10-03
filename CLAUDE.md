# AutoWeb

A stub for web automation. Not a framework.

## What this is

AutoWeb gives you the smallest honest starting point for driving a browser with an
LLM, and then gets out of the way. You are expected to open every file and rewrite
it. There are no private internals, no base classes to satisfy, no plugin registry
to appease.

The engine, for now, is **Claude Code itself**. The only hard dependency is
Microsoft's `@playwright/mcp`. Everything else is markdown.

Later — and only later — the loop moves into a standalone **Python** harness that can
pick its own models, route between them, and run simulations in parallel. That is not
this version. Do not build for it.

## Language

**Python. Decided, final.** Not up for re-litigation in a later session.

Node is in the stack regardless — `@playwright/mcp` is a Node package and Claude Code
is a Node process — so AutoWeb is polyglot whether or not it writes a line of
TypeScript. The decision that matters is therefore not "which language" but **where
the seam sits**, and the rule is: *there is exactly one seam.*

- **Python owns** the loop, the config, the CLI, and every `.md` artifact.
- **TypeScript appears only** where the real Playwright API is required and the MCP
  tool surface is not enough — `page.frameLocator`, `pressSequentially`,
  `addInitScript`,
  `storageState({indexedDB: true})`.
- **A TS module is reached through MCP stdio or a JSON-in/JSON-out CLI subprocess.**
  Never imported. Never FFI, never node-gyp, never a Python package vendoring a Node
  tree.
- A long-lived localhost HTTP service is allowed for one thing only: holding state
  that must outlive a call, e.g. a browser pool.

Minimum versions, non-negotiable on Windows: `mcp` **≥ 1.6.0** (below that
`command="npx"` cannot resolve at all), Python ≥ 3.10. See `docs/CONSTRAINTS.md` §9
for the 2.x rename list and the child-env allowlist.

## Design rules

These are load-bearing. Violating them is how this becomes another framework.

1. **Don't wrap the LLM. Don't wrap its tools either.** How many browser tools you
   get is a function of flags, not a fixed number. `@playwright/mcp@0.0.83` ships 83
   tool definitions and `filteredTools()` keeps one only if its `capability` starts
   with `core` or is named in `--caps`, then drops every `skillOnly` tool whatever you
   pass. Zero flags, which is what this repo runs, yields 25; all eleven capabilities
   yield 72. Check what you actually have before deciding the surface is too small,
   and do not build a nicer API over it.
2. **One bullet at a time.** `northstar.md` is ordered. Finish a line, prove it
   works, then move. No speculative scaffolding for line 6 while line 2 is unproven.
3. **Markdown is the database.** Targets, resources, directions, goals, skills, run
   logs — all files on disk, human-readable, git-diffable. No hidden state.
4. **Self-contained repo.** A clone must work. Nothing may depend on the author's
   global config, global MCP registration, or a profile directory that only exists
   on one machine.
5. **Pin your versions.** `@latest` is how you get a hang on first run and a silent
   break on the next.
6. **Design against tool-call count, not step count.** Each browser tool call is an
   LLM round trip. Measured elsewhere: ~11s per call. A 155-call page walk is 28
   minutes. Batch aggressively; snapshot once, plan, execute, verify once.

## Current state

| | |
|---|---|
| Engine | Claude Code |
| Browser | `@playwright/mcp@0.0.83` over stdio, project-scoped `.mcp.json`, zero flags |
| Language | Python (decided; TS only behind MCP or a CLI) |
| Code | `autoweb/`: `config.py`, `state.py`, `lanes.py`, `cli.py`. 144 tests, ruff clean |
| CLI | `autoweb config show/check`, `state export/inspect/verify`, `lanes sync/list` |
| Done | northstar lines 1 to 4. Line 5 is built and unproven, line 6 is not built |

`northstar.md` is the task list and the source of truth for sequencing.

### What "proven" means here

**Lines 1–2.** `navigate` → `snapshot` → `click` (by snapshot `ref`) → `snapshot` →
`screenshot` → `close`, against `example.com` through to `iana.org`. Clean teardown, no
errors, no profile or lock messages, screenshot verified by eye rather than by the tool
reporting success.

**Line 3.** `goals/daily-digest.md` handed to a Sonnet worker with **no steps given**.
It chose its own route across Yahoo Finance, google.com, doodles.google, Medium and
Typefully; wrote its own deliverable to `runs/`; and produced its own assertion table.
53 browser calls. Result was 4 PASS / 1 FAIL — and the FAIL is why it counts: no Google
Doodle existed that day, and it reported that rather than passing off a plain-logo
screenshot as one. It also found a real Typefully bug (bolding a selection over ~80
characters drops a space at the cut point), reproduced it, and worked around it in the
open.

**Line 4.** `autoweb state export` opened a headed browser, a human logged in by hand,
and `storageState({indexedDB: true})` wrote `root.json`. Everything was then killed, and
a fresh `--isolated` browser seeded from that JSON alone was still inside the secure
area, while a control run with no seed was redirected back to the login page. An
independent tester ran both a gate and a re-gate. The gate found thirteen defects and
the re-gate four, among them a `state verify` that exited 0 on a 404 and a `.tmp`/`.bak`
pair that would have put live session cookies in a committable file in a public repo.
All are fixed, and the fixes were verified by running them.

**Line 5, not proven.** `autoweb lanes sync` generates the lane agent files and 44 tests
assert the frontmatter that ships. The tests are offline: they prove the right text was
written, not that five subagents get five browsers. A re-gate found real defects there,
the fixes are in but not re-verified, and nothing has yet run three lanes on three sites
at once, which is the actual test.

The 144 tests are all offline. They cover config parsing, state file accounting and lane
file generation; none of them opens a browser, and `cli.py` has no tests at all. Passing
them is not evidence that a browser works.

Note on scope: `playwright` is also defined at user scope on the author's machine with
a `--user-data-dir`. Project scope wins here, and `claude mcp list` reports the
collision. The repo does not depend on the user-scope entry.

## Vocabulary

Fix these meanings now; they drift otherwise.

- **Target** — a website, or set of websites, a simulation acts on.
- **Resources** — what the run is given: data to fill, directions to navigate.
- **Goal** — the end state, *plus the assertion that proves it was reached*. A goal
  without a checkable assertion is not a goal.
- **Run** — one attempt at a goal against a target. Produces a transcript.
- **Simulation** — a sequence of runs. Runs are **not** independent: after every
  run the skill documents are updated, so run N+1 is better informed than run N.
- **Skill** — a `.md` document distilled from runs. Holds moves to take and moves
  to avoid. Gets revised and pruned, never appended to forever.

### The two blocks

A business problem decomposes into two independently-refined document sets. They
are not a pipeline; they are two blocks, each improved by simulation.

1. **Resources → extraction → business logic.** How to read what you were given
   (pdf, excel, report) and what that implies. Structured against the simulation's
   target.
2. **Actions.** What to do in the browser, given the business logic.

Both are products of iterating on a specific business problem. Both live as `.md`.

## Scoring

A run passes or fails on an **assertion declared in the goal spec** — URL match,
visible text, element state. Deterministic. No LLM judge, no manual labelling.
If you cannot state the assertion up front, the goal is not specified yet.

## Hard constraints

Read `docs/CONSTRAINTS.md` before touching browser configuration, parallelism, or
profiles. It records measured behaviour, not guesses — Windows profile locking,
`@playwright/mcp` flag conflicts, and why cross-machine profile copy is dead.

Load-bearing summary:

- One MCP session = **one browser context**. Only `browser_tabs` addresses a tab, so
  concurrent calls in one session race on the current tab.
- Default persistent-profile mode is **single-client only**.
- Parallelism requires `--isolated` (N sessions, N contexts) or N server processes.
- On Windows, a second browser on the same profile fails **silently** — exit 0 with
  a handoff, or exit 21. There is no error to catch.
- A subagent that **inherits** the session's server shares its one browser and its one
  current tab, so concurrent lanes there fight over the same page. A subagent whose
  agent file declares an **inline** `mcpServers` block gets its own server process and
  its own browser. Running in the background changes nothing: a background subagent
  keeps its MCP tools. The earlier claim that it loses them is retracted.
- Claude Code de-duplicates inline servers **by name** across subagents running at the
  same time. Two agent files that both declare a server called `lane` collapse into one
  process and one browser, which is the shared-tab failure by another route. Lane
  servers are named per lane for that reason: `lane1`, `lane2`, and so on.

## Working in this repo

- Prove it, then claim it. "Connected" means a browser opened and took an action in
  a transcript you can point at.
- MCP config is read **only at startup**. Editing `.mcp.json` needs a full restart.
- Windows paths in JSON: forward slashes. `\U` and `\A` break the parse.
- Everything here is public. No tokens, no credentials, no machine-specific paths
  in anything tracked by git.
- Personal instruction files are gitignored, and nothing tracked may reference them.

## Non-goals, explicitly

Not in this version: model routing, parallel simulations, profile merging, a GUI,
a scheduler, a results server, retry/healing layers. Each is a real feature and
each one waits.

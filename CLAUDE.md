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
  `addInitScript` (the only way to capture sessionStorage),
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

1. **Don't wrap the LLM. Don't wrap its tools either.** Playwright MCP already
   exposes 25 browser tools. Do not build a nicer API over them.
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
| Browser | `@playwright/mcp` over stdio |
| Language | Python (decided; TS only behind MCP or a CLI) |
| Code | none yet, by design |
| Done | nothing verified |

`northstar.md` is the task list and the source of truth for sequencing.

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
- Background subagents lose MCP access. Browser work is foreground only.

## Working in this repo

- Prove it, then claim it. "Connected" means a browser opened and took an action in
  a transcript you can point at.
- MCP config is read **only at startup**. Editing `.mcp.json` needs a full restart.
- Windows paths in JSON: forward slashes. `\U` and `\A` break the parse.
- Everything here is public. No tokens, no credentials, no machine-specific paths
  in anything tracked by git.
- `CLAUDE.local.md` is the author's personal instructions and is gitignored. Do not
  reference it from tracked files.

## Non-goals, explicitly

Not in this version: model routing, parallel simulations, profile merging, a GUI,
a scheduler, a results server, retry/healing layers. Each is a real feature and
each one waits.

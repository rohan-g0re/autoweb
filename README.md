# AutoWeb

A browser automation harness for Claude Code, deliberately small. The engine is Claude
Code; the browser is Microsoft's `@playwright/mcp`; between them, markdown. One install
per machine, one folder per app you automate, nothing shared between those folders.

## Why not plain Playwright

- **No scripts.** You write a goal in markdown; the agent drives the browser through
  Playwright MCP, snapshot by snapshot, and adapts when the page does not match.
- **Log in once, by hand.** `autoweb state export <url>` opens a real browser, you sign
  in, and the session is saved as a storage-state file. Every later browser starts
  signed in. AutoWeb never sees a password.
- **Parallel lanes, decided at run time.** The agent splits a task across isolated
  browsers that all start from that one identity; how many is its call, up to your cap.
  Lanes share nothing on disk, so they cannot trip over each other.
- **Session merge-back.** What the lanes learned (rotated cookies, refreshed tokens) is
  merged three-way into the base identity, with conflicts evicted rather than guessed.
- **Deterministic pass or fail.** A goal declares its assertions: URL matches, visible
  text, element state and commands that exit non-zero. `autoweb goal check` evaluates
  them and writes `runs/<run>/status.json`; the model never grades.
- **One folder per app.** Identity, config, lanes, goals and runs live in the project;
  four apps are four folders with nothing shared.

## Install

One marketplace, two plugins. `autoweb` is the harness; `autoweb-loop` is optional.

In Claude Code. The first two lines run once ever; the third once per machine:

```
/plugin marketplace add rohan-g0re/autoweb
/plugin install autoweb@autoweb
/autoweb:aw-setup
```

It checks Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 20+, the pinned
`@playwright/mcp` and a browser, and with your say-so installs the `autoweb` CLI and a
Chromium. Run it again inside each project you automate; that run only needs the project
step, `--init-project`.

**Restart the session afterwards.** Hooks, agent files and `.mcp.json` are read at
session start only. Forking instead: clone the repo, `uv sync`, same package and CLI.

**Optional: `autoweb-loop`.** Same marketplace, separate install:

```
/plugin install autoweb-loop@autoweb
```

It adds `/autoweb-loop:aw-loop <goal>` and a Stop hook. After each pass of `aw-run`, the
hook reads `runs/<run>/status.json`; while an assertion fails it sends Claude back to
work, until every assertion passes or an iteration cap (default 5) is hit. `autoweb`
itself stays one pass per goal, so install this only if you want the re-running done for
you. The hook runs on every Stop in every project and exits at once, silently, unless a
loop is active there. Experimental: an offline suite covers it, but it is not yet proven
in a live session. Its guide is
[plugins/autoweb-loop/README.md](plugins/autoweb-loop/README.md).

## Your first project

`--init-project` writes `autoweb.toml` with the defaults below, creates `goals/` with the
template, and adds the private files — `root.json`, `.autoweb/`, `lane-*.json`, `runs/`,
the lane agents, `.claude/settings.local.json`, `.playwright-mcp/` — to a managed block
in the project's `.gitignore`, and to `.git/info/exclude` when a repository exists. It
also writes the project's `.mcp.json` with one server, `playwright` (`npx
@playwright/mcp@<lanes.mcp_version>`, no flags), and enables it in
`.claude/settings.local.json`. The plugin ships no MCP server of its own, so a session
has this browser only in a project that ran setup. An existing `.mcp.json` keeps its
servers; one that does not parse is left alone. Idempotent. `--check` then confirms the
server is declared and `root.json` is ignored and untracked.

Then the one step nobody can do for you:

```sh
autoweb state export https://the-app-you-automate.example
```

A headed browser opens, **you log in by hand**, and the session is written to
`root.json`. AutoWeb never sees, stores or types a password. That file holds live
cookies, so it stays out of git, and `export` needs a real desktop, not a headless VM.

```sh
autoweb lanes sync      # writes .claude/agents/lane-N.md, one own browser per lane
```

**Accept the trust prompt for the folder.** Claude Code refuses to start the MCP servers
declared in an agent file from an untrusted folder, and refuses silently: the lane runs
and its browser tools are simply absent. `lanes sync` checks and tells you.

Every later session there opens with one status line, each part of which becomes an
instruction when it is missing:

```
autoweb: identity root.json present · 4 of 5 lanes generated · 2 goals · trust ok
```

A project whose `.mcp.json` has no `playwright` entry also gets
`· shared browser: not set up (run /autoweb:aw-setup)`.

## Running a goal

```
/autoweb:aw-run <goal>
```

It reads `goals/<goal>.md`, checks the config, verifies the identity against the target
the goal names, counts the lanes, dispatches them, writes the deliverable to
`runs/<date>-<goal>/`, then runs the goal's assertion commands and reports each result.

A goal is an end state **plus the assertion that proves it was reached** — a URL match,
visible text, an element state. Deterministic; no LLM judge. If you cannot state the
assertion up front, the goal is not specified yet. `goals/README.md` is the format, and
no real goals ship. One run per invocation: there is no retry loop, by decision — the
assertions decide, and re-running is your call, or the optional `autoweb-loop` plugin's.

## One project per app

Identity, config, lanes, goals and runs all live in the project folder. Lanes run
`--isolated`, seeded from `root.json` and writing nothing to disk, so they share nothing
with each other either. Two projects can leak into one another in exactly one way: both
set `lanes.isolated = false` and point at the same `--user-data-dir`. The generator never
does that on its own, and `/autoweb:aw-doctor` reports any it finds outside the folder.

## The CLI

Ten commands, through the installed `autoweb` binary or the shipped `scripts/aw.py` when
nothing is installed. Each prints its own help; `-C DIR` runs as if started elsewhere.

| command | what it does |
|---|---|
| `autoweb config show` | the effective config, defaults included |
| `autoweb config check` | validate `autoweb.toml` |
| `autoweb state export URL` | headed browser, you log in, the session is saved |
| `autoweb state inspect [PATH]` | origins, cookies, IndexedDB, bytes |
| `autoweb state verify URL [PATH]` | seed a fresh browser from the JSON, report what it sees |
| `autoweb lanes sync` | write `.claude/agents/lane-N.md`, one per lane |
| `autoweb lanes list` | which lane files exist, generated or hand-written |
| `autoweb trace TRACE_JSON` | did the lanes really overlap? |
| `autoweb merge LANE_JSON...` | fold lane state back into `root.json`, three-way |
| `autoweb goal check GOAL --run RUN` | evaluate the goal's assertions, write `runs/RUN/status.json` |

`state export --from-profile DIR --visit URL` reads a profile already logged in. Five
commands are assertions rather than tools and exit non-zero when what they check does not
hold: `config check`, `state verify`, `trace`, `goal check`, and `state export` when it
captures nothing at all — an empty state file that parses is the failure most mistaken
for a success.

## Configuring

`autoweb.toml`, in the project folder. Every key has a working default, so the file
exists to show what is adjustable.

| key | default | |
|---|---|---|
| `lanes.max` | `5` | ceiling on concurrent lanes, never a target |
| `lanes.browser` | `"chrome"` | `"chromium"` on most Linux: Chrome is x86_64-only, absent on ARM |
| `lanes.mcp_version` | `"0.0.83"` | pinned; `@latest` is how you get a hang on first run |
| `lanes.isolated` | `true` | nothing written to disk; what makes lanes safe in parallel |
| `state.root` | `"root.json"` | the base storageState every lane starts from |
| `state.indexeddb` | `true` | `false` quietly loses Firebase, Supabase and Auth0 logins |
| `caps.total_bytes` | `2000000` | a safety valve on the merge, not a selection policy |
| `caps.max_origins` | `50` | hitting a cap blocks the write, loudly |
| `caps.max_indexeddb_per_origin` | `5` | per-origin store ceiling |

Per-origin overrides go in `[origins."example.com"]`: `indexeddb` (this site's is large
and worthless), `rotates` (only one lane may hold it), `sticky` (never evict). Each key
is documented where it is defined in `autoweb/config.py`, not in a reference that rots.

## Fixing

`/autoweb:aw-doctor` runs every prerequisite check plus `autoweb config check` and
`autoweb lanes list`, one fix beside each failing line. `/autoweb:setup-playwright`
smoke-tests the browser on its own.

## Status, and what this is not

Pre-alpha. `northstar.md` is the ordered task list: lines 1 to 5 are proven, line 6
(merge-back) holds for a cookie-session site and so is scoped per target, and line 7,
`SIMULATION`, is open. "Proven" means a browser opened and acted in a transcript you can
point at — every serious defect in this repo's history survived a green test suite.

Deliberately absent: model routing, parallel simulations, a GUI, a scheduler, a results
server, retry and healing layers, and a loop inside `autoweb` (the separate,
experimental `autoweb-loop` plugin is one). The plugin contract is
`docs/PLUGIN-DESIGN.md`, the design rules `CLAUDE.md`, the tour `docs/HANDOFF.md`, the
measured browser facts `docs/CONSTRAINTS.md`, the releases `CHANGELOG.md`.

## Prior art

Shape owed to [Pi](https://github.com/earendil-works/pi) (*"there are many agent
harnesses but this one is yours"* — the ownership model),
[browser-use/browser-harness](https://github.com/browser-use/browser-harness) (*"don't
wrap the LLM, don't wrap its tools either"* — the restraint),
[OpenClaw](https://github.com/openclaw/openclaw) (a versioned skill lifecycle, here in
markdown rather than 140 SQL tables), and
[Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) with
[Petri](https://github.com/meridianlabs-ai/inspect_petri) (a small documented stub rather
than a scaffold generator).

## License

[Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE).

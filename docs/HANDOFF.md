# Start here

For whoever picks this up on a machine that has not seen it before. Read this, then
`CLAUDE.md`, then `docs/CONSTRAINTS.md`. Fifteen minutes, and you will know as much as
the last session did.

## Where things run

**Code is written on a Windows ARM64 laptop. Nothing is run there.** Running and testing
happen on an x86_64 Arch Linux box. That is a decision, not an accident: the Windows
machine has no Google Chrome story that matches any deployment, and its Python setup
fights back. A green test suite on Windows is not evidence of anything.

The two machines never exchange identity. Each exports its own `root.json` from its own
browser profile, locally. Git carries code and documents; `root.json` is gitignored
because it holds live session cookies and IndexedDB refresh tokens. If you find yourself
about to copy one between machines, stop: OAuth refresh tokens rotate, and two machines
using one token family can trip replay detection and revoke it everywhere.

## What this is

A stub for driving a browser with an LLM, deliberately small. The engine is Claude Code
itself; the only hard dependency is `@playwright/mcp`, pinned. Everything else is
markdown. `northstar.md` is the ordered task list and nothing gets built ahead of it.

## State as of this document

| | |
|---|---|
| Proven | northstar lines 1 to 5 |
| Not built | line 6, profile merge |
| Tests | 184, all offline, none opens a browser |
| Commands | `autoweb config show\|check`, `state export\|inspect\|verify`, `lanes sync\|list` |

Phases 1 to 3 each passed an independent gate. The gates matter more than the tests:
**every serious defect in this repo's history survived a green suite.** Phase 1's gate
found 14, phase 2's found 13 across two rounds, phase 3 took three gates.

## The four facts that decide the design

1. **State flows out of a browser profile, never in.** `launch_persistent_context()`
   accepts a `storage_state` argument, launches, and silently ignores it. So the shared
   identity is a JSON file, not a profile directory.
2. **`storageState()` omits IndexedDB unless asked**, and most real login state lives
   there: Firebase, Supabase and Auth0 all keep refresh tokens in IndexedDB or
   localStorage rather than cookies.
3. **Claude Code de-duplicates inline MCP servers by name** across subagents running at
   the same time. Two lane files naming their server `lane` collapse into one process,
   one browser, one tab, and the lanes then overwrite each other's page with no error
   raised. Hence `lane1`, `lane2`, and a test that the names differ.
4. **Agent files and `.mcp.json` are read once, at startup.** Editing them mid-session
   changes nothing, and the stale session cannot tell. `lanes sync` says so.

## Getting running

```sh
git clone https://github.com/rohan-g0re/autoweb.git && cd autoweb
uv sync                       # installs the dev group: pytest, ruff, pyyaml
uv run pytest -q              # offline, a couple of seconds
uv run ruff check .
npx playwright install chromium
```

**Without uv**, which is the case on a machine where installing it is somebody else's
decision. The dev tools are declared as both a dependency group and an extra for exactly
this reason, so pip can set up the same environment:

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q && ruff check .
python -m playwright install chromium
```

A test asserts those two dependency lists stay identical, because if they drift then one
of the two installers quietly produces a clone that cannot test itself.

On most Linux set `browser = "chromium"` in `autoweb.toml`. Google ships Chrome as a
.deb/.rpm for x86_64 only, it is absent from Arch's official repositories, and for ARM
Linux it does not exist at all.

## Then the part that needs a human

```sh
uv run autoweb state export https://a-site-you-use.example   # headed, you log in by hand
uv run autoweb state inspect                                 # bytes and origins, split
uv run autoweb state verify https://a-site-you-use.example/secure --expect-text "your name"
```

AutoWeb never sees, stores or types a password. You log in; it reads the session
afterwards. `export` needs a real desktop session, so it cannot run on a headless VM.

`inspect` is also the open question: it prints how much of your session lives in cookies
versus localStorage versus IndexedDB, and that number decides phase 4. If IndexedDB is a
rounding error on the sites that matter, lanes can harvest through MCP alone and a much
larger piece of work never has to be built.

## Lanes

```sh
uv run autoweb lanes sync     # writes .claude/agents/lane-N.md, grants mcp__laneN
                              # then RESTART Claude Code, or the new files are ignored
uv run autoweb lanes list     # which are generated, which were hand-written
```

A lane is a subagent with its own browser. Dispatch several in one message. They cannot
see each other's tabs, cookies or storage, and all start from the same `root.json`.

## What is owed

- A lane run seeded with a real identity instead of an empty storageState.
- Phase 2 against a site with MFA and IndexedDB-backed auth. The public test site has
  neither.
- Phase 4, teardown and harvest, blocked on the `inspect` number above. Note that every
  storage tool sits behind `--caps=storage`, which the lane argv now passes, and that
  `browser_storage_state` calls `storageState()` bare, so it captures no IndexedDB.
- Linux has never run any of this. That is the point of the Arch box.

## How to not get fooled

The repo's own history is the argument for this list.

- Assert the artifact that ships, parsed, not grepped. A test asserting
  `"mcpServers:" in body` passed 121 times while the feature was dead.
- Prove isolation by counting browser processes, never by reading the file that was
  supposed to cause it.
- Run a control. "Still logged in" means nothing until an unseeded browser is shown
  getting bounced to the login page.
- Check your measurement before you believe a defect. `claude -p --output-format
  stream-json` emits one event per content block, and grouping by event instead of
  `message.id` invented a dispatch bug that did not exist.
- `docs/CONSTRAINTS.md` labels every claim **live** or **read-from-source**. Keep doing
  that. The file is worth something only because the distinction is maintained.

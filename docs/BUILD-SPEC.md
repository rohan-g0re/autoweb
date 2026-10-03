# Build spec

Everything finalised, in one place. Decisions only — the reasoning lives in
`CONSTRAINTS.md`, `STORAGE-EXPORT.md`, `PROFILE-LANES.md`, `SERVERS.md`.

# Stack

- Python. one seam only.
- TypeScript ONLY where the real Playwright API is needed, reached over MCP stdio or a
  JSON-in/JSON-out CLI. never imported, never FFI, never node-gyp.
- deps: `playwright`, `mcp>=1.6.0` (below 1.6 cannot spawn `npx` on Windows at all),
  `claude-agent-sdk`. python >= 3.10. uv + pyproject.
- `@playwright/mcp` pinned. never `@latest`.

# Architecture

    ORCHESTRATOR — whoever is driving. Claude Code for now (fastest to test).
                   later a model router / any harness. IT DOES NOT MATTER.
        - reads a skill .md -> decides how many lanes THIS task decomposes into
        - lane count is NEVER hardcoded. decided at runtime, from task complexity.
        - spawns lanes by calling the CLI. that is the whole interface.

    LANE — a Claude Code subagent with its OWN inline MCP server.
        `.claude/agents/lane-N.md` frontmatter:
            mcpServers:
              playwright:
                command: npx
                args: ["@playwright/mcp@0.0.83", "--isolated", "--storage-state", "root.json"]

    - docs, verbatim: "Inline servers defined here are connected when the subagent
      starts ... and disconnected when it finishes. String references share the parent
      session's connection."
    - so: INLINE definition = its own `npx @playwright/mcp` process = its own browser.
      a bare string reference = shares the parent's. inline is the whole trick.
    - inline also keeps the tool descriptions OUT of the orchestrator's context.
      the docs name this as the reason to prefer it over `.mcp.json`.
    - background subagents KEEP every MCP tool. the "background loses MCP" note in
      CLAUDE.local.md is stale and wrong.

    LANE COUNT — dynamic within a ceiling
    - agent files must be PRE-AUTHORED. there is no documented way to create server
      instance #6 at runtime.
    - whether ONE definition invoked N times concurrently yields N connections or 1 is
      UNDOCUMENTED -> author distinct files, lane-1..lane-N.
    - so: author a pool (10). orchestrator picks 1..10 per task at runtime.
    - subagent concurrency caps at 20 by default (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`).
    - truly unbounded/dynamic needs the Agent SDK. that is the later harness, not now.

    TRUST CAVEAT
    - inline servers in a PROJECT `.claude/agents/` load only after the folder is trusted.
      `~/.claude/agents/` skips the check but breaks the self-contained-repo rule.
      we take the trust prompt.

    LATER — when the orchestrator becomes a model router
    - lanes move to Agent SDK processes, each with its own `mcp_servers`.
    - needs `ANTHROPIC_API_KEY`; a Claude Code subscription does NOT cover SDK usage.
    - windows: `CLIConnectionError: Refusing to execute batch script` if it resolves
      npm's `claude.cmd` -> native install or explicit `cli_path`.

# root.json

- the base identity. a JSON file, NOT a Chrome profile folder.
- why: `launchPersistentContext({storageState})` does not throw — it launches and
  SILENTLY DISCARDS the state. JSON goes into a lane fine; it never goes back into a
  profile dir. that one asymmetry decides the whole design.

    ONE TIME — build it
    - open your real Chrome profile ONCE, alone, log in to what you need
    - export: `context.storageState({ indexedDB: true })`
        - the flag DEFAULTS TO FALSE. without it you lose 30 of 31 MB of login state
          (firebase / supabase / auth0 all live in IndexedDB + localStorage)
        - `browser_storage_state` (MCP) CANNOT do this — it calls `storageState()` bare.
          proven by source + live test.
        - there is NO `browser_indexeddb*` MCP tool. none. not a gap in our config.
        - in-band fallback: `browser_run_code_unsafe` works, but is RCE-equivalent.
          prefer the library.
    - close it. never open that folder again.

# Lane lifecycle

    SPAWN
    - `playwright-mcp --isolated --storage-state root.json`
    - `--isolated` = nothing written to disk, all in RAM -> no cache junk ever
    - `--isolated` and `--user-data-dir` are mutually exclusive. error, not a warning.
    - import restores IndexedDB correctly. only EXPORT has to leave MCP.
    - spawn / kill / respawn any time. no locks, no profile conflicts.

    RUN
    - record top-level navigations AS THEY HAPPEN. see teardown.

    TEARDOWN — the decision to kill is NOT the kill
    - 1. settle: let in-flight navigation finish, accept no new actions
    - 2. harvest, BEFORE anything closes (--isolated keeps it in RAM only):
        - `storageState({ indexedDB: true })` -> lane-N.json
        - the top-level origins this lane visited
          (storageState CANNOT tell you this — it lists every origin with storage,
           trackers included. ~800 hosts in a real profile. only nav history knows.)
        - which cookie values CHANGED vs root -> rotation detection
        - the trace -> feeds the skill .md
    - 3. close in order: `context.close()` does NOT release the lock. `browser.close()` does.
    - 4. deregister. lane-N.json is now immutable input to the merge.

# Merge

    see rohan_questions.md — steps 1-5 stand as written.
    - ancestor is always available: root.json is immutable for the whole run.
    - never propagate deletions.
    - conflict -> evict the origin, force re-login. never pick a winner.
    - ONE writer, after all lanes are dead. atomic .tmp -> rename, keep .bak.

# Config

- `autoweb.toml` — what a human sets.
- separate JSON — what the loop learns. keep hand-authored and machine-authored apart.
- per-origin: `indexeddb` (inherits `state.indexeddb`), `rotates` (-> single-lane
  only), `sticky` (never evict). `state.indexeddb` defaults ON: off loses most logins.
- caps: total bytes, max origins, max IndexedDB stores per origin. hitting a cap is LOUD.

# Infra

- any plain linux VM. 2 vCPU / 4 GB. no GPU, no display, no special instance type.
- glibc only. Alpine fails in the dynamic loader before your code runs.
- headless needs no display. "seeing" = the accessibility tree, not pixels.
- `npx playwright install --with-deps chromium`
- docker only: `--ipc=host` + `--init`, else `--shm-size=2g`. 64 MB default kills tabs.
- docker sandbox: `--user pwuser` + playwright's `utils/docker/seccomp_profile.json`.
  NOT `--no-sandbox`.
- a human watching a lane = CDP screencast over websocket. Xvfb alone has no viewer.
- design against TOOL-CALL COUNT, not step count. ~10 s per call.

# Still open

- which site line 4 is tested against (not gmail — google blocks automation and you end
  up debugging their bot defences instead of our export).

# Build plan

Phases. Each one: **plan → build → test by an independent Fable subagent → commit on
pass**. A phase that fails goes back to Fable for diagnosis, the foreground worker
fixes, and the same phase re-runs. No phase starts before the one above it is green.

Target host for now: **Windows (ARM64)**, which is already proven — Node 24.11.1,
Python 3.11.9, uv 0.11.7, Chrome and a cached Chromium. **Linux/WSL testing is
deferred**, not abandoned; the code stays POSIX-clean so it ports later.

---

## Phase 0 — environment and corrections

- [x] `~/.claude/CLAUDE.md` and `CLAUDE.local.md`: replace the two wrong browser rules
      (background subagents keep MCP; one-worker-at-a-time applies only to *shared*
      servers), drop the `ddpat` profile path.
- [x] Windows toolchain verified: node 24.11.1, npm 11.6.2, python 3.11.9, uv 0.11.7,
      git 2.52, `playwright` (python) present, Chrome installed, chromium-1243 cached.
      `mcp` not installed — not needed until the Agent SDK phase.
- [x] Browser chain proven end to end today: navigate → snapshot → click → screenshot
      → close, on the project-local `.mcp.json`.

### Facts discovered in Phase 0, carried into the build

- **Node ≥ 20 is the real floor, not ≥ 18.** `@playwright/mcp@0.0.83` declares
  `engines: {node: ">=18"}`, but its bundled playwright-core 1.64 refuses to start:
  *"You are running Node.js 18.19.1. Playwright requires Node.js 20 or higher."*
  The package metadata is wrong. Our setup command and docs must say 20.
- **ARM Linux has no Google Chrome**, so playwright-mcp's default `channel=chrome`
  cannot work there. Any Linux lane needs `--browser chromium`. Windows ARM is fine —
  Chrome ships for it.
- **WSL sudo needs a password here**, so `playwright install --with-deps` cannot run
  unattended. A Linux setup path must install the binary first and treat system libs
  as a separate, human-run step.
- WSL Ubuntu 24.04 aarch64 is available (8 cpu, 7.9 GB RAM, `/dev/shm` 3.8 G) and
  stays the eventual target.

**Test:** done — a browser opened and acted via the project's own config.

## Phase 1 — repo skeleton and config

- `pyproject.toml` (uv), `src/autoweb/`, `tests/`, `docs/`.
- `autoweb.toml` — human-authored config. Separate JSON for what the loop learns.
- Config options from `BUILD-SPEC.md`: lane ceiling, caps (total bytes, max origins,
  max IndexedDB stores/origin), per-origin `indexeddb` / `rotates` / `sticky`.
- Every option documented where it is defined, not in a separate reference that rots.

**Test:** `autoweb --help` runs; a malformed `autoweb.toml` fails loudly with a message
naming the offending key.

## Phase 2 — root.json (northstar line 4)

The smallest honest unit, and it needs none of the lane machinery.

- `autoweb state export` — opens a headed browser on **Windows**, waits for the human
  to log in, then `storageState({ indexedDB: true })`.
- `autoweb state inspect` — origin count, bytes, which origins carry cookies vs
  IndexedDB. Needed before you can tune caps honestly.

**Test (Fable):** log into one site by hand, export, kill everything, start a fresh
`--isolated` browser seeded from the JSON, assert still logged in. Site TBD — not
Gmail; Google blocks automation and you end up debugging their bot defences.

## Phase 3 — lanes (northstar line 5)

- `.claude/agents/lane-1.md` … `lane-N.md`, each with an **inline** `mcpServers`
  block, `--isolated --storage-state root.json --browser chromium`.
- A skill doc telling the orchestrator how to decide lane count from task shape, and
  how to dispatch. Lane count is never hardcoded; the file pool is the ceiling.

**Test (Fable):** spawn 3 lanes concurrently on 3 different sites; assert 3 distinct
browser processes, no shared current tab, all 3 logged in from the same root.

## Phase 4 — teardown and harvest

The window between "kill it" and killing it.

- settle → harvest (`storageState({indexedDB:true})` → `lane-N.json`, top-level
  origins visited, cookies changed vs root, trace) → `context.close()` then
  `browser.close()` → deregister.

**Test (Fable):** a lane that navigates to 3 sites yields a `lane-N.json` plus a
navigation list containing exactly those 3 origins and none of the third-party ones.

## Phase 5 — merge (northstar line 6)

- Three-way against `root.json` as ancestor. Filter → compare → conflict → caps → write.
- Conflicts evict the origin; deletions never propagate; one writer, atomic rename.

**Test (Fable):** synthetic lane files covering disjoint origins (clean union), the
same origin unchanged (ignored), and the same origin changed by two lanes (evicted,
recorded as `rotates`).

## Phase 6 — documentation

- Skill docs for the orchestrator and for lanes.
- Config reference, Playwright/infra guide, quickstart.
- Style informed by the skill libraries being researched; structure informed by how
  Pi, OpenDots, OpenClaw and browser-use lay out a repo people are meant to fork.

**Test (Fable):** a reader who has never seen the repo can get from clone to a passing
line-4 test using only the docs.

---

## Standing rules

- One phase at a time. No scaffolding for phase 5 while phase 2 is unproven.
- Every test is a deterministic assertion, stated before the build starts.
- Fable tests independently; the foreground worker does not grade its own work.
- Commit on green. Never push.

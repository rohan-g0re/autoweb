# Build plan

Phases. Each one: **plan → build → test by an independent Fable subagent → commit on
pass**. A phase that fails goes back to Fable for diagnosis, the foreground worker
fixes, and the same phase re-runs. No phase starts before the one above it is green.

Target host for now: **Windows (ARM64)**, which is already proven — Node 24.11.1,
Python 3.11.9, uv 0.11.7, Chrome and a cached Chromium. **Linux/WSL testing is
deferred**, not abandoned; the code stays POSIX-clean so it ports later.

---

## Phase 0 — environment and corrections

- [x] Personal instruction files corrected: replace the two wrong browser rules
      (background subagents keep MCP; one-worker-at-a-time applies only to *shared*
      servers) and drop a hardcoded browser-profile path belonging to one machine.
      Those files are gitignored, so they are not named here.
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
- **Most Linux has no Google Chrome**, not only ARM. It ships as a .deb/.rpm for
  x86_64, is absent from Arch's official repositories (AUR only), and does not exist
  for ARM Linux at all. Measured on x86_64 Arch: only `/usr/bin/chromium`. So
  playwright-mcp's default `channel=chrome`
  cannot work there. Any Linux lane needs `--browser chromium`. Windows ARM is fine —
  Chrome ships for it.
- **WSL sudo needs a password here**, so `playwright install --with-deps` cannot run
  unattended. A Linux setup path must install the binary first and treat system libs
  as a separate, human-run step.
- WSL Ubuntu 24.04 aarch64 is available (8 cpu, 7.9 GB RAM, `/dev/shm` 3.8 G) and
  stays the eventual target.

**Test:** done — a browser opened and acted via the project's own config.

## Phase 1 — repo skeleton and config

**Status: passed, commits `610854d` and `8ae3d31`.** The first gate returned 14 defects.
The load-bearing one: TOML is typed and dataclasses are not, so `Klass(**data)` accepted
any value matching a key name. `indexeddb = "false"` is a truthy string, so a user
turning IndexedDB off silently got it on while `config check` reported `ok`. Fixed with
an explicit per-field type spec.

- `pyproject.toml` (uv), `autoweb/`, `tests/`, `docs/`.
  Flat package at root, not `src/`: both Python comparables (hermes-agent,
  browser-use) do this, and a stub people fork should import without an
  install step.
- `autoweb.toml` — human-authored config. Separate JSON for what the loop learns.
- Config options from `BUILD-SPEC.md`: lane ceiling, caps (total bytes, max origins,
  max IndexedDB stores/origin), per-origin `indexeddb` / `rotates` / `sticky`.
- Every option documented where it is defined, not in a separate reference that rots.

**Test:** done. `autoweb --help` runs and a malformed `autoweb.toml` names the
offending key.

## Phase 2 — root.json (northstar line 4)

**Status: passed, commits `a1f3f36`, `9a7a7b3`, `d46c4dc`.** Two gates. The first found
`root.json.bak` and `root.json.tmp` were not gitignored, which in a public repo means a
committable live session cookie, and that `verify` asserted nothing: it printed the
title and exited 0, while the test site serves the same title on its login and secure
pages.

The smallest honest unit, and it needs none of the lane machinery.

- `autoweb state export` — opens a headed browser on **Windows**, waits for the human
  to log in, then `storageState({ indexedDB: true })`.
- `autoweb state inspect` — origin count, bytes, which origins carry cookies vs
  IndexedDB. Needed before you can tune caps honestly.

**Test (Fable):** done. Logged into one site by hand, exported, killed everything,
started a fresh `--isolated` browser seeded from the JSON, still in the secure area. A
control run confirmed an unseeded browser is redirected to `/login`, which is what makes
the first result mean anything. Not Gmail: Google blocks automation and you end up
debugging their bot defences instead of the export.

Still owed: a second run against a site with MFA and IndexedDB-backed auth. The public
test site has neither.

## Phase 3 — lanes (northstar line 5)

**Status: built, two gates failed, fixes in. Not green until a third gate passes.**
Both failures were the same shape, and both were invisible to a passing test suite:

- Gate 1: `mcpServers` was emitted as a YAML **mapping**. Claude Code wants a list and
  ignores a mapping silently, so lanes fell back to the session's shared server. 121
  tests passed while the feature was dead, because the test grepped for `"mcpServers:"`
  instead of parsing the YAML.
- Gate 2: every lane named its inline server `lane`. Claude Code de-duplicates inline
  servers **by name** across concurrently-running subagents, so all lanes collapsed into
  one process and one tab. Hence `lane1`, `lane2`, and a test that the names differ.

The lesson both times: assert the parsed artifact, and prove isolation by counting
browsers rather than by reading the file that was supposed to cause it.

- `.claude/agents/lane-1.md` … `lane-N.md`, each with an **inline** `mcpServers`
  block as a **list** of single-key mappings, a **per-lane** server name, and
  `--isolated --storage-state <abs>/root.json --browser chrome`.
- `autoweb lanes sync` also grants `mcp__laneN` in the gitignored
  `.claude/settings.local.json`. A permission rule names one server, so `mcp__lane`
  does not cover `mcp__lane1`, and an ungranted lane stalls on approval nobody sees.
- A skill doc telling the orchestrator how to decide lane count from task shape, and
  how to dispatch. Lane count is never hardcoded; the file pool is the ceiling.

**Test (Fable):** spawn lanes concurrently on different sites; assert distinct browser
processes, no shared current tab, and each logged in from the same root. Counting
processes is the assertion that matters: both failures above looked correct in the
generated file. **Passed at N=2** on the third gate: two live
`playwright_chromiumdev_profile-*` directories at once, sixteen extra chrome processes,
neither lane able to see the other's tab.

**Test (discovery): passed.** A fresh session told nothing about lanes dispatched four of
them anyway, finding them through the generated agent descriptions. A second, told the
work split four ways, loaded the skill, ran `autoweb lanes list`, inspected the one
hand-written lane to see why it was excluded, and dispatched the four generated lanes.
Four distinct browser profiles per run, correct per-lane tool isolation, real headlines
off four sites.

One defect found, in the skill rather than the code: both runs sent each delegation in
its own message, so peak concurrency was two of four and then three of four. Both then
claimed all four ran at once. The skill now leads with single-message dispatch and says
why you cannot verify it from your own transcript.

**Four browsers alive simultaneously: proven.** Four lanes dispatched in one message,
each holding its browser for thirty seconds, gave four concurrent browser profiles, 16
playwright `node` processes against a baseline of 6, and every lane still on its own page
afterwards with zero URL drift. Each verified `location.href` itself rather than trusting
the trailing page report of `browser_wait_for`, which is the check the lane body now
prescribes.

A correction worth keeping, because the mistake was in the measurement and not the
system: three earlier runs were written up as dispatching lanes one per message. They had
not. `claude -p --output-format stream-json` emits one event per content block, so one
assistant message holding four `Agent` calls arrives as four events, and grouping by
event instead of by `message.id` turned a correct parallel dispatch into a fabricated
defect. The orchestrators were right and the analysis was wrong. Low concurrency in those
runs was task length: a browser takes seconds to start and a one-page read is about three
calls, so short lanes come and go.

Still owed at this line: a run where the lanes are seeded with a real identity rather
than an empty storageState.

**The original reasoning, kept because it generalises.** Every gate before this was told
that lanes exist and which ones to dispatch. That tests the mechanism and not the feature, because in real use
nobody tells the orchestrator. So: hand a fresh Opus agent that knows nothing about
lanes a task that happens to decompose, say nothing about lanes or parallelism, and see
whether it finds the `parallel-lanes` skill, reads `autoweb lanes list`, and dispatches
on its own. Then repeat with the decomposition stated out loud ("the same thing on four
different sites") to separate "cannot discover" from "cannot execute". Both runs assert
the same thing the mechanism test does, by counting browsers rather than by believing
the agent's own account of what it did.

A skill nobody invokes is a skill that does not work, however correct its contents.

## Phase 4 — teardown and harvest

The window between "kill it" and killing it.

- settle → harvest (`storageState({indexedDB:true})` → `lane-N.json`, top-level
  origins visited, cookies changed vs root, trace) → `context.close()` then
  `browser.close()` → deregister.

**The blocker is resolved, and in the cheap direction.** Measured on a real 1.9 GB Chrome
profile: the whole identity exports to 1.5 MB, of which IndexedDB is **527 bytes**, being
three LinkedIn telemetry databases. Google contributes none. So teardown harvests through
MCP with `--caps=storage` and the CDP seam does not get built. Phase 4 is the small option.
One profile is not every profile, so an origin whose IndexedDB was not collected is still
recorded as not collected rather than as empty; that rule stays whatever the measurement
says.

**The original reasoning, kept because the mechanism still holds.** Every storage tool
declares `capability: "storage"`, and `filteredTools()` ships a tool only if its
capability starts with `core` or is named in `--caps`. The lane argv passes no `--caps`,
so a lane today cannot save or restore anything. Worse, `browser_storage_state` calls
`storageState()` bare, so even with `--caps=storage` it captures no IndexedDB, which is
where most real login state lives. Import is the complete direction:
`setStorageState` deletes and recreates every database.

So Phase 4 has to decide between harvesting cookies and localStorage only through MCP,
or putting Python in charge of the context over CDP so it can ask for IndexedDB. An
un-harvested origin must be recorded as un-harvested: merging an empty `indexedDB: []`
over a good root entry is indistinguishable from a deletion, and Phase 5 must never
propagate deletions.

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

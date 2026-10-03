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

**Status: passed. Three gates, the first two failed, and the run owed at this line —
lanes carrying a real identity — was done on 2026-10-03 and is recorded below.**
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

**Four lanes on one real identity: proven**, 2026-10-03, Arch Linux x86_64, repo at
`/home/rohan/Desktop/autoweb`. This is the run that was owed here: the lanes carried an
identity instead of an empty storageState. A copy of a real Pursuit LinkedIn Chrome
profile was logged into by hand and exported with `autoweb state export --from-profile`
to a 1.5 MB `root.json`; the four lanes took the four read-only tasks in
`goals/four-lanes-one-identity.md`.

The dispatcher was a fresh `claude -p` process told nothing about AutoWeb or lanes. It
found the `parallel-lanes` skill, ran `autoweb lanes list`, and dispatched all four lanes
in **one** assistant message. Unaided, for the second time.

Counted from outside the run by a process not taking part: peak 4 distinct
`--user-data-dir` values, at 4 or more continuously from 17:51:38 to 17:52:22 UTC — 44
seconds — and 56 chrome processes at peak. `autoweb trace` exited 0 with all four lanes
alive together for 21.6 seconds, 17:51:45.128 to 17:52:06.690. The `T0_start` marks were
staggered about 4.5 seconds per lane, so browser startup serialises and the overlap comes
after it. Memory was not a constraint: 13,441 MB free with no browsers, 8,393 MB minimum
during the run.

No lane drifted onto another lane's page, and there was no authwall and no checkpoint.
One lane ended on `/mynetwork/grow/` because LinkedIn redirects there itself, not because
of drift. `li_at` and `JSESSIONID` were byte-identical across `root.json` and all four
lane files: four concurrent uses of one session rotated nothing.

**A prerequisite that cost a whole earlier run.** The workspace folder must be trusted.
`projects["/home/rohan/Desktop/autoweb"].hasTrustDialogAccepted` was false, and an
identical run with it false dispatched four lanes and started zero browsers, silently.
Flipping it to true is what made this run work.

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

**Status: harvest ran, inside the four-lane real-identity run of 2026-10-03.** Each of
the four lanes wrote its own `lane-N.json`, about 1.5 MB, and all four were matched by
`.gitignore`. Against root the cookies came back unchanged: `li_at` and `JSESSIONID`
byte-identical in `root.json` and in all four lane files. `autoweb trace` read the marks
back and exited 0. What that run did not measure is the origin assertion in the test at
the foot of this phase.

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

**Status: the merge has been written to a real `root.json` and the identity still works -
and doing it exposed a defect that neither dry run could.** Three dry runs and one real
merge against the four-lane output of 2026-10-03:

The first exited 0 and wrote nothing, and what it proposed is why the dry run exists. It
would have evicted `www.linkedin.com`, `accounts.google.com`, `www.google.com` and
`li.protechts.net`, leaving **0 origins** and evicting 59 cookies. Nothing had rotated.
Every conflict was a per-browser bot-management value: `__cf_bm`, `_px3`, `pxcts`,
`__Secure-3PSIDCC`. Writing it would have signed `root.json` out of LinkedIn as the
result of a read-only run.

Fixed in two commits. A conflict confined to `VOLATILE_COOKIE_NAMES` (cookies) or
`VOLATILE_STORAGE_MARKERS` (localStorage keys) now keeps root's copy and evicts nothing.
A conflict on anything else still evicts the whole host.

The second dry run, after the cookie fix: 269 tests passed, `www.linkedin.com` survived
as "updated", 3163 cookies kept, 1 added, 3 evicted, and 6 of the kept ones
expiry-refreshed only. Only `li.protechts.net` was still evicted, over
`localStorage[PXdOjV695v_px-ff]` — the same class of value in a different store, which is
what the storage-key fix addresses.

The third dry run, after the storage-key fix: 271 tests passed, ruff clean, **nothing
evicted at all**, all four origins kept or updated.

**The real merge.** `root.json` written, previous kept as `root.json.bak`, with a manual
`root.json.before-merge` taken first because a second merge would overwrite the `.bak`.
Output: 3166 cookies kept, 1 added, 0 evicted, 6 expiry-refreshed; `www.linkedin.com`
updated by lanes 1 and 3; 4 origins, 1.3 MB, no cap complaint. Then
`autoweb state verify https://www.linkedin.com/feed/ --expect-text <first name>` landed on
`/feed/`, HTTP 200, title `Feed | LinkedIn`, **exit 0**. For a cookie-session site,
northstar line 6 holds: an identity survives a merge of four concurrent lanes.

**And the merge silently lost 737 cookie rows while reporting `0 evicted`.** 3903 rows in,
3167 out. The cookie identity was `(name, domain, path)`, which under CHIPS is not an
identity - a partitioned cookie is scoped to the top-level site it was set under, so 36
names existed in several partitions each and all but one copy of each was dropped. Mostly
ad-tech (`.youtube.com`, `.rubiconproject.com`, dotomi, contextweb, adnxs), one auth-ish
name among them (`__Secure-ROLLOUT_TOKEN`). LinkedIn and Google were untouched, which is
why `state verify` passed and why nothing looked wrong.

The report is the part worth remembering. "3166 kept" counted identities rather than rows,
so the arithmetic that should have caught the collapse was the thing hiding it. Fixed by
putting a canonicalised `partitionKey` in the key, and by requiring that every ancestor
cookie end up either in the result or in the evicted count - `lost_cookies` is always
empty, and a non-empty list refuses the write with the previous root untouched.

Still owed on this line: the re-merge on the fixed code, and a second run against a site
whose auth lives in IndexedDB or behind MFA rather than in a cookie. Everything proven so
far is proven for one cookie-session site on one machine.

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

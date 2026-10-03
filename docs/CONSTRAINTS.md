# Hard constraints

Measured or source-verified facts about `@playwright/mcp`, Chromium profiles, and
Windows. Read before designing anything around parallelism or profiles. Not advice —
constraints. Verified against `@playwright/mcp` **0.0.83** (2026-10).

---

## 1. Where the code actually lives

Source has moved **out** of `microsoft/playwright-mcp` into the Playwright monorepo:
`packages/playwright-core/src/tools/{mcp,backend}`. The `playwright-mcp` README table
is generated and is stale in places (profile dir name, `--port` help, the
capabilities enum). Read the monorepo source, not the README.

The npm package is a shim. The shipped logic is bundled into
`playwright-core/lib/coreBundle.js`, so to read what your pinned version actually does,
locate that file in the resolved `playwright-core`: the npx/npm cache entry for the
pinned `@playwright/mcp`, or `playwright/driver/package/lib/coreBundle.js` under any
local Playwright install. Claims below marked **source** were read there; claims marked
**live** were observed in a running session. Both are evidence. They are not the same
strength, and keeping them apart is this file's job.

## 2. Tool surface

25 tools are always on:

```
browser_click  browser_close  browser_console_messages  browser_drag  browser_drop
browser_emulate_media  browser_evaluate  browser_file_upload  browser_fill_form
browser_find  browser_handle_dialog  browser_hover  browser_navigate
browser_navigate_back  browser_network_request  browser_network_requests
browser_press_key  browser_resize  browser_run_code_unsafe  browser_select_option
browser_snapshot  browser_take_screenshot  browser_type  browser_wait_for
browser_tabs
```

Behind `--caps`:

| cap          | adds | notable                                                                                |
| ------------ | ---- | -------------------------------------------------------------------------------------- |
| `storage`  | 17   | `browser_storage_state`, `browser_set_storage_state` — in-band auth export/import |
| `devtools` | 13   | `browser_start_tracing`, `browser_start_video`, `browser_start_recording`        |
| `network`  | 4    | `browser_route`, `browser_unroute`                                                 |
| `vision`   | 6    | mouse XY                                                                               |
| `testing`  | 5    | verify / locator                                                                       |
| `pdf`      | 1    | `browser_pdf_save`                                                                   |
| `config`   | 1    | `browser_get_config`                                                                 |

`--caps` is a bare comma list with **no enum validation** — a typo is silently
ignored. `tracing` is shimmed to `devtools`.

**The gate has no default, so storage is off unless you ask for it** (source).
`filteredTools(config)` keeps a tool only when
`tool.capability.startsWith("core") || config.capabilities?.includes(tool.capability)`,
and `config.capabilities` is taken straight from `--caps`. 17 tools declare
`capability: "storage"`: `browser_storage_state`, `browser_set_storage_state`,
`browser_cookie_*`, `browser_localstorage_*`, `browser_sessionstorage_*`. **None of them
exist in a server started without `--caps=storage`**, and that includes this repo's
flagless `.mcp.json`. `--help` advertises only `vision, pdf, devtools` for `--caps`, so
`storage` is undocumented but accepted. That last point is read from the option
definition and the filter, and has **not** been confirmed against a live tool listing
from a server started with `--caps=storage`.

  **Confirmed live.** `tools/list` over stdio against `@playwright/mcp@0.0.83 --isolated` returns **25** tools; adding `--caps=storage` returns **42**, and the difference is exactly the 17 storage tools named above. So the undocumented capability is accepted and works. The 25 also confirms the figure quoted for a zero-flag server.

Gone: `browser_install` (now `cli.js install-browser`), `--save-trace`,
`--save-video` (removed ~0.0.60 — use `--caps=devtools`). `--save-session` still
exists.

## 3. Flag conflicts and silent precedence

Errors:

- `--isolated` + `--user-data-dir` → `Error: Browser userDataDir is not supported in isolated mode.`
- `--mobile` + `--device`
- `--mobile` + firefox
- device emulation + `--cdp-endpoint`

**Silent** precedence (`browserFactory.ts`) — losers are inert, with no warning:

```
--endpoint  >  --cdp-endpoint  >  --isolated  >  --extension  >  persistent
```

With `--cdp-endpoint`, your `--browser`, `--headless`, and `--user-data-dir` do
nothing. This is the single easiest way to spend an hour confused.

Defaults in MCP mode: **persistent profile**, **headed**, `channel=chrome`.
Headless only on Linux without `DISPLAY`. Every flag has a `PLAYWRIGHT_MCP_*` env
twin.

`--storage-state` is documented as *"path to the storage state file for isolated
sessions"* (source: the option definition). What a persistent-mode server does when
handed one anyway has not been measured, so AutoWeb does not pass it there and seeds
persistent lanes from their own `--user-data-dir` instead. The neighbouring *library*
fact is measured and is not the same fact: `launchPersistentContext({ storageState })`
launches, reports success, and restores nothing (`docs/STORAGE-EXPORT.md`).

Default profile dir, from code:
`%LOCALAPPDATA%\ms-playwright-mcp\mcp-{channel}-{sha256(cwd)[:7]}`. The hash is of
the **client workspace root**, not the session — so a different cwd silently gets a
different profile.

## 4. Concurrency — the real ceiling

**One MCP session = one BrowserContext.** `Context` holds a singular
`_rawBrowserContext`, `_tabs[]`, `_currentTab`. Only `browser_tabs`
(`list|new|close|select`) addresses a tab; **no other tool takes a tab or context
id**. Concurrent calls inside one session therefore race on `_currentTab`.

Parallelism has exactly two shapes:

| mode                         | result                                                                                  |
| ---------------------------- | --------------------------------------------------------------------------------------- |
| `--isolated`               | one browser process,**one context per MCP session**. The supported parallel mode. |
| N server processes           | N browsers, N contexts. Over **stdio** there is no port, so nothing can collide there; the only shared resource is the profile directory, so each process needs `--isolated` or its own `--user-data-dir`. `EADDRINUSE` is an HTTP-transport problem only (`--port 0`). |
| `--shared-browser-context` | all clients share ONE context. Tabs bleed across clients.`browser_close` is refused.  |

**Default persistent mode breaks with ≥2 clients.** Second client fails its first
browser action:

```
Browser is already in use for <dir>, use --isolated to run multiple instances of the same browser
```

A Playwright maintainer on this: *"works fine with single client, but falls apart
when you have 2 or more… We should have made one of the two options required for
http transport."* ([playwright-mcp#1631](https://github.com/microsoft/playwright-mcp/issues/1631))

**Correction (2026-10-03):** an earlier note here said background subagents lose
MCP access. They do not: the docs state a background subagent keeps every MCP
tool. What is true is that a subagent *inheriting* the session's server shares
its single browser and current tab. A subagent whose agent file declares an
**inline** `mcpServers` list gets its own server process, and those run in
parallel safely. The shape matters: a mapping is ignored silently.

### Lanes: one subagent, one browser

AutoWeb's lanes are the N-stdio-processes shape. `.claude/agents/lane-N.md` declares an
inline `mcpServers` block, so each lane gets its own server process, browser, context
and current tab. These things about that, all **live**:

- **Claude Code de-duplicates inline servers by name across concurrently-running
  subagents.** Two lane files that each declared a server named `lane` collapsed into
  **one** server process and one browser: lanes listed each other's tabs, one lane
  navigated the other's current tab, and closing the first browser left the second with
  `Error: No open pages available.` A process poller saw at most one
  `playwright_chromiumdev_profile-*` user-data-dir alive at a time, and only +2
  `node.exe` over baseline. The control run, identical except that one lane's server was
  renamed, gave two profile dirs alive concurrently, 16 to 17 chrome processes, +4 node,
  and each lane seeing only its own tabs. Hence a distinct name per lane (`lane1`,
  `lane2`, ...) and tools named `mcp__lane1__browser_*`. A permission rule names one
  server, so `mcp__lane` does not cover `mcp__lane1`; each lane needs its own rule or
  its first browser call stops for an approval nobody is watching for.
- **A lane also sees the orchestrator's `mcp__playwright__*` tools.** They are not
  hidden and cannot be revoked, and a lane that calls one lands in the single shared
  browser alongside every other lane. The generated lane file says so in as many words,
  which is the whole defence.
- **Inside a subagent, MCP tools arrive deferred**: the names are visible, the schemas
  are not. One `ToolSearch` call before the first browser call is enough, verified
  across four lane executions. Budget it as a call.

- **Discovery works, and it is worth testing separately from the mechanism.** **live.**
  Two fresh sessions were given only a task over four news sites. The first was told
  nothing about lanes or parallelism and still dispatched four of them, finding them
  through the generated agent descriptions alone. The second was told the work split
  four ways; it loaded the `parallel-lanes` skill, ran `autoweb lanes list`, read the
  one hand-written lane file to see why it was excluded, checked `root.json` existed,
  and then dispatched the four generated lanes. Four distinct
  `playwright_chromiumdev_profile-*` directories per run, every lane calling only its
  own `mcp__laneN__*` tools, no lane touching `mcp__playwright__*`.

- **Four lane browsers do run at the same instant.** **live.** Four lanes dispatched in
  one message, each holding its browser open for thirty seconds, produced four
  concurrent `playwright_chromiumdev_profile-*` directories (plus one left over from an
  earlier run), 16 playwright `node` processes against a baseline of 6, and around 100
  chrome processes. Every lane then verified `location.href` after the hold and all four
  still held their own page. Zero drift.

  Earlier runs of the same shape peaked at only two or three concurrent browsers, and
  the reason is task length rather than dispatch: a browser takes a few seconds to
  start and a one-page read is about three calls, so short lanes come and go. A single
  message buys overlap, not a guarantee of it.

- **And they do it from one real logged-in identity, which is the claim that matters.**
  **live, 2026-10-03, Arch Linux x86_64.** Every lane run before this one started from an
  empty `storageState`, so none of them proved the thing the feature exists for. This one
  started from a copy of a real Pursuit LinkedIn Chrome profile, logged in by hand and
  exported to a 1.5 MB `root.json`.

  Four lanes, four read-only LinkedIn and Google tasks, dispatched in one assistant
  message by a fresh `claude -p` told nothing about AutoWeb. A poller outside the run saw
  **four distinct `--user-data-dir` values held continuously for 44 seconds**, 17:51:38 to
  17:52:22 UTC, 56 chrome processes at peak. `autoweb trace` exited 0 with all four alive
  together for **21.6 seconds**, 17:51:45.128 to 17:52:06.690. No lane drifted onto
  another's page; one ended on `/mynetwork/grow/` because LinkedIn redirects there itself.

  Two numbers worth keeping. `T0_start` was staggered about **4.5 seconds per lane**, so
  browser startup serialises and the overlap is what happens after it - which is the
  mechanism behind the earlier note that a single message buys overlap rather than
  guarantees it. And memory was not the constraint the lane count was being rationed
  against: 13,441 MB free with no browsers, **8,393 MB at the minimum** with four real
  sites loaded.

- **Four concurrent uses of one session rotated nothing.** **live, same run.** `li_at` and
  `JSESSIONID` came back byte-identical from `root.json` and all four lane files. No
  authwall, no checkpoint, no device-verification mail. This is one site on one run and
  not a general licence - §6 is still right that an OAuth refresh token is a different
  animal from a cookie session - but the specific fear that N browsers sharing one
  LinkedIn cookie trips a concurrency defence did not happen here.

- **Beware of how you measure the dispatch, not just the browsers.** **live, and it
  produced a false finding before it was caught.** `claude -p --output-format
  stream-json` emits one event per content block, so a single assistant message holding
  four `Agent` calls arrives as four separate events. Grouping tool calls by event makes
  a correct parallel dispatch look like four staggered messages. Group by
  `message.id`. Three runs were written up as staggered on the strength of the wrong
  grouping, and the orchestrators had been right all along.

- **An agent file's `mcpServers` are ignored unless the folder is trusted, and the
  refusal is silent.** **live, on a fresh clone, and this is the one that costs you a
  whole run.** Claude Code will not start the MCP servers declared in an agent file whose
  definition came from an untrusted folder. From inside the run there is no error: the
  lane starts, its browser tools are simply absent, and `ToolSearch` answers "No matching
  deferred tools found". The real message appears only in a debug log:

  ```
  Skipping frontmatter MCP servers for agent 'lane-1': the folder its definition file
  came from is not trusted (source: projectSettings)
  ```

  **`--dangerously-skip-permissions` does not bypass it.** Measured: on a fresh clone a
  naive agent found the lanes unaided, dispatched four of them correctly in a single
  message, and *zero* browsers ever started. The same code on a machine where the folder
  had been trusted months earlier ran four concurrent browsers.

  The trust flag lives in `~/.claude.json` under
  `projects["<abs path>"].hasTrustDialogAccepted`, keyed with forward slashes on every
  platform. `autoweb lanes sync` now checks it and says so, because a silent no-op that
  looks like a broken feature is worth one explicit check. Fix it by running Claude Code
  once in the folder and accepting the trust prompt.

  This also explains an asymmetry that had been read as a Linux problem: it was never
  about the platform, only about which folder had been trusted.

- **Agent files are read once, at session startup.** **live.** `autoweb lanes sync`
  rewrote five lane files mid-session. A session that was already running kept serving
  the definitions it had cached, so four lanes dispatched from it connected to one
  pre-change server named `lane`, while fresh sessions started afterwards correctly got
  `mcp__lane1__*` through `mcp__lane5__*` and four separate browsers. Same files on
  disk, opposite behaviour, decided only by when the session started. Treat lane files
  like `.mcp.json`: editing them needs a restart, and the stale session has no way to
  know it is stale.

- **Under a shared tab, the tool output itself lies, and quietly.** **live.** Four
  lanes on one server produced all of the following, with no error raised anywhere:
  a `browser_navigate` response whose URL was `news.ycombinator.com` and whose title
  was `Loading https://www.aljazeera.com/`, a torn read inside a single call; a
  `browser_wait_for` whose trailing page block still named the lane's own page after a
  sibling had already navigated the tab away; a `browser_snapshot` immediately after
  that wait returning a third site entirely; and `browser_evaluate` of `location.href`
  disagreeing with a `browser_navigate` response from three seconds earlier. A lane
  that trusts any single one of these reports one site's content under another site's
  name. This is the argument for a lane re-checking its own URL before it reports, and
  the reason the generated lane body now tells it to.

- **Cheap diagnostic for a shared server, no process poller needed.** **live.** Lanes
  that are really separate write their page snapshots to separate output directories.
  Interleaved timestamps in one `.playwright-mcp/` directory mean one server. Two lanes
  reporting a single tab at index 0 whose site keeps changing mean the same thing.

- **A Google session does not survive a storageState transplant.** **live, on a real
  profile.** An export carried 3903 cookies including non-empty `SID` and
  `__Secure-3PSID`, and a fresh browser seeded from it was still redirected from both
  `mail.google.com` and `myaccount.google.com` to
  `accounts.google.com/v3/signin/confirmidentifier`. LinkedIn from the same export worked
  on the first try, landing on `/feed/` with a 200 and the account's own name on the page.
  So the cookies are necessary and not sufficient: Google binds the session to something
  the JSON does not carry, most likely device-bound credentials or an IP and user-agent
  check. Inference, not measurement, for the cause; the redirect itself is measured.

  The practical rule is the one already in this repo for a different reason: do not build
  on Gmail. Phase 2's advice was about bot defences and it turns out to be right about
  session portability too.

- **IndexedDB holds almost nothing for a real logged-in profile.** **live.** A 1.9 GB
  Chrome profile exported to a 1.5 MB storageState with 819 cookie domains and exactly
  four origins carrying storage. Of that, cookies were about 1,045 KB, localStorage about
  319 KB, and **IndexedDB 527 bytes** - three LinkedIn telemetry databases named
  `beacons`, `grpcRestBeacons` and `sequenceNumber`. Google contributed no IndexedDB at
  all.

  This settles a design question that was open for the whole project. The fear was that
  roughly 30 of 31 MB of real login state lives in IndexedDB, which would have forced a
  CDP seam with Python owning the browser so it could call
  `storage_state(indexed_db=True)`. For this identity it is the other way round: a lane
  harvesting through MCP with `--caps=storage` loses 527 bytes of telemetry. One profile
  is not every profile, and a Firebase or Supabase app would look different, but the
  expensive design is no longer justified by the evidence available.

- **`Last Browser` in a profile directory is stale and misleading.** **live.** Both
  Pursuit profiles name `C:/Program Files/Google/Chrome/Application/chrome.exe`, which
  would suggest opening them with Chrome rather than Chromium. `Last Version` said
  `155.0.8059.12`, which is exactly Playwright's chromium-1247, and the cookies decrypted
  correctly under Chromium. Check `Last Version` against `browsers.json`, not
  `Last Browser`.

- **`browser_navigate` does not return the snapshot inline.** **live.** On 0.0.83 it
  writes the page to `.playwright-mcp/page-<timestamp>.yml` and returns a pointer, so a
  lane needs a separate `browser_snapshot` or a file read before it can act. The floor
  for "look at one page" is three calls, not two. This matters for the tool-call budget
  in CLAUDE.md design rule 6, and it is why those files accumulate in the repo root.

## 5. Windows profile locking — fails silently

This is the one that will cost you a day.

Chromium on Windows does **not** use POSIX `SingletonLock` symlinks. It uses:

- a startup mutex `Local\ChromeProcessSingletonStartup!`
- a hidden `Chrome_MessageWindow` whose **window title is the literal profile path**
- a file named **`lockfile`** at the profile root, opened
  `GENERIC_WRITE | FILE_SHARE_READ | FILE_FLAG_DELETE_ON_CLOSE` — so there is no
  stale lock after a crash
- `WM_COPYDATA` handoff, 20s timeout

Measured, second instance on the same profile:

| launch                      | result                                                                                              |
| --------------------------- | --------------------------------------------------------------------------------------------------- |
| headed                      | **exit 0**, stdout `Opening in existing browser session.` — handed off, you get no browser |
| `--headless=new`          | **silent exit 21** (`PROFILE_IN_USE`)                                                       |
| `chromium-headless-shell` | **starts anyway** — no ProcessSingleton at all                                               |

That last row is the dangerous one. `chromium-headless-shell` is Playwright's default
headless binary, so two headless workers on one profile both launch and **silently
corrupt** the SQLite and LevelDB stores.

The "profile appears to be in use" dialog is `is_posix`-guarded. **Windows shows
nothing.** No flag disables ProcessSingleton; `--profile-directory` does not help.

`launchPersistentContext` adds **no lock of its own** — it only `mkdir`s and asserts
an absolute path. Playwright's detection is post-hoc stderr scraping
(`profileInUseError()`), which Windows headless never emits. Playwright's own test
for this is `fixme`'d on win32.

**`context.close()` does not release the lock.** You must
`await context.browser().close()`. ([playwright#35466](https://github.com/microsoft/playwright/issues/35466))

Official position, dgozman: *"browsers do not allow that… This is by design."*

## 6. Profile fan-out and merge-back

### `storageState()` — what it does and doesn't capture

Always: cookies + localStorage.
Opt-in: `indexedDB: true` (1.51+), `opfs: true` (1.63), `credentials: true` (1.61).
**Never via `storageState`**: sessionStorage. It is absent from the serialised state, and the library workaround is `page.evaluate` plus `addInitScript`. With `--caps=storage` the MCP server does expose `browser_sessionstorage_list` and friends, so reading it per origin is possible without leaving MCP; what is not possible is getting it out as part of a storageState file,
service-worker / Cache API, HTTP auth (use `httpCredentials`), Chrome's Login Data
and autofill.

Known bug: `indexedDB: true` silently empties `Map` and `Set` values
([playwright#42703](https://github.com/microsoft/playwright/issues/42703)).

**The lossy direction is export only, confirmed at both call sites (source).**
`browser_storage_state` calls `browserContext.storageState()` bare, and the
implementation signature is
`storageState(progress, { indexedDB = false, opfs = false, credentials = false } = {})`,
so an in-band export drops IndexedDB, OPFS and WebAuthn credentials, and the tool
exposes no way to ask for them. Import is the opposite: `setStorageState` unregisters
service workers, calls `deleteDatabase` on every database it finds, then recreates each
one object store by object store with its `keyPath`, `autoIncrement` and indexes, and
clears localStorage before restoring it. So `browser_set_storage_state` and
`--storage-state` seed a lane faithfully; only the export step has to leave MCP
(`docs/STORAGE-EXPORT.md`).

`launchPersistentContext` has **no** `storageState` option.

The official parallel recipe is per-worker `.auth/${parallelIndex}.json`
([playwright.dev/docs/auth](https://playwright.dev/docs/auth)).

### Files holding session state

Copy only with the browser **fully** closed — including the tray process
(`chrome.exe --no-startup-window`).

```
Default/Network/Cookies          sqlite  (moved from Default/Cookies in Chrome 96)
Default/Local Storage/leveldb/   copy WHOLE dir: CURRENT, MANIFEST-*, *.ldb, *.log
Default/Session Storage/
Default/IndexedDB/<origin>/
Default/Service Worker/{CacheStorage,ScriptCache}
Default/Preferences
Default/Login Data (+ For Account)
Default/Web Data
Local State                      ← root, holds os_crypt.encrypted_key
```

**Correction on SQLite mode:** Chrome's databases are mostly **not** WAL — `sql/database.h`
defaults `wal_mode_ = false`, and `Cookies`, `Login Data` and `Favicons` use a rollback
journal (you can confirm by the 0-byte `Cookies-journal` sitting next to them). Carrying
`-wal`/`-shm` siblings costs nothing, but they are not the live risk. **Exclude** `lockfile`,
`Singleton*`, every LevelDB `LOCK`, and caches.

**`Local State` is load-bearing and looks like junk.** 6 KB of JSON at the profile root
holding `os_crypt.encrypted_key` — the AES-256-GCM key for every cookie, DPAPI-wrapped.
Omit it and Chromium mints a fresh key, fails to decrypt every `v10` row, and **drops the
cookies with no error**. The profile looks healthy and is logged out of everything.

**Windows locks `Network/Cookies` while Chrome runs** — a read attempt returns
`PermissionError: [Errno 13]`. Copy-while-running is not available; close the browser or
go through `storageState()`.

Locked-file escape hatch (VSS, no admin): `esentutl.exe /y <src> /d <dst> /o`

### Windows encryption — cross-machine copy is dead

Cookie `encrypted_value` is AES-256-GCM (prefix `v10`/`v11`, 12-byte nonce, 16-byte
tag). The AES key lives in `Local State` → `os_crypt.encrypted_key`, base64, 5-byte
`DPAPI` header, remainder wrapped with `CryptProtectData` — **bound to the Windows
user and machine**.

Chrome 127+ adds **`v20` App-Bound Encryption**: the key is held by a SYSTEM COM
elevation service that validates the calling signed `chrome.exe`. Google:
ABE *"will not function correctly in environments where Chrome profiles roam between
multiple machines."*

|                         |                                                                                                                                  |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| cross-machine           | **not viable** for cookies or passwords. LevelDB (localStorage, IndexedDB) does travel; cookies will not decrypt.          |
| same-machine, same user | viable for`v10`/`v11` — copy the whole closed profile *including* `Local State`. `v20` real-Chrome defeats raw reads. |

Playwright does not help here: its `--password-store=basic` and
`--use-mock-keychain` defaults are Linux/macOS-only no-ops on Windows. Its own
profiles are equally non-portable. **`storageState` JSON is the portable form.**

### The first thing that actually destroyed an identity was the merge rule

**live, 2026-10-03, and caught by a dry run rather than by a test.** Everything below
this is still true and still waiting. It is not what went wrong first.

The first merge of four real lane files proposed evicting `www.linkedin.com`,
`accounts.google.com`, `www.google.com` and `li.protechts.net` - **every origin**, down
to zero, and 59 cookies with them. Nothing had rotated: `li_at` and `JSESSIONID` were
byte-identical in all four lane files. The conflicts were entirely per-browser
bot-management values - `__cf_bm` (Cloudflare), `_px3` and `pxcts` (PerimeterX),
`__Secure-3PSIDCC` (Google) - which a browser mints for itself, so N fresh browsers
*always* produce N different ones.

The rule "a conflict evicts the whole host" was written to protect an OAuth token family
from a replayed refresh token. Applied to every cookie it instead signed `root.json` out
of LinkedIn **as the direct result of a successful read-only run**. A safety rule that
destroys the identity every time it works is not a safety rule.

Two further things fell out of it, both general:

- **The same vendors write the same state into localStorage.** With the cookie exemption
  in place, `li.protechts.net` was still evicted over `localStorage[PXdOjV695v_px-ff]`, a
  PerimeterX fingerprint keyed by their app id. Exempting the *host* is the wrong fix:
  evicting protechts.net costs nothing because nobody has an account there, and the case
  that matters is the identical key appearing under an origin somebody is logged in to.
  Match the key.
- **An eviction notice has to name what triggered it.** The first dry run said only "two
  lanes changed the same value to different things", which is exactly as consistent with
  token rotation as with a merge defect, and it was read as the former. It also named no
  lane, because conflicts were recorded against the cookie's host (`linkedin.com`) and
  reported against the origin's (`www.linkedin.com`).

**And a cookie's identity is not `(name, domain, path)`.** Writing the merge for real
cost 737 cookie rows, reported as `0 evicted`. Under CHIPS a partitioned cookie is scoped
to the top-level site it was set under, so 36 names existed in several partitions each
and all but one copy of each was dropped. The summary said "3166 kept" because it counted
identities rather than rows, so the arithmetic that should have exposed the collapse was
the thing concealing it. LinkedIn and Google were unaffected and `state verify` passed,
which is why nothing looked wrong.

Putting `partitionKey` in the key fixed most of it and left a narrower version standing,
found on the re-merge: Chromium's `CookiePartitionKey` is the **pair** (top-level site,
has-cross-site-ancestor), and Playwright carries that pair two ways - as an object with
`hasCrossSiteAncestor`, or as a bare site string with `_crHasCrossSiteAncestor` beside
it. Canonicalising the object kept the bit; the string form silently lost it. Two
`tag_user_id` rows on `.trueclassictees.com` differing only in that bit collapsed to one.
Total row counts balanced by coincidence, because one unrelated cookie was added in the
same merge.

Two rules fall out, and they generalise past cookies:

- **Normalise to the semantic thing, not to the encoding.** "Canonicalise the dict with
  sorted keys" is a statement about JSON. The identity is a pair, and a sibling field is
  part of it.
- **An accounting invariant must count rows, not test membership.** Both collapsed rows
  mapped to one key that *was* present in the output, so a set-membership check saw
  nothing. Row counts per key catch it, and a key that still arrives twice now refuses the
  write rather than quietly keeping whichever came last.

After both volatile fixes, across four concurrent read-only lanes on a real identity,
**the only conflicts anywhere were bot-management state**. No session cookie and no application
storage key disagreed at all. The honest reading of that is narrow: on a cookie-session
site, read-only lanes do not fight. It says nothing yet about a lane that writes.

### The merge killer is not cookies

Cookie merge is not semantic — last-write-wins per `(host, name, path)`, so a stale
value can clobber a rotated one. Survivable.

The actual killer is **OAuth refresh-token rotation with replay detection**. Two live
copies of one session refreshing the same token look like a replay, and the
authorization server SHOULD revoke the entire token family — killing *both* sessions.
(RFC 9700 §2.2.2, §4.14)

Also waiting for you: single-session-per-user eviction, CSRF tokens living in
sessionStorage (never captured), session-ID regeneration on privilege change,
fingerprint binding, and DPoP / token binding (RFC 9449), which makes a copied token
unusable *by design*.

**Implication for northstar lines 4–6:** "merge profile after each parallel spawn"
cannot be a generic operation. It works for cookie-session sites on one machine and
is actively harmful for OAuth sites. Scope it per-target or not at all.

## 7. Cost — design against tool-call count

From a measured production run elsewhere:

- 155 browser tool calls / 1,684,482 ms = **~10.9 s per call**. Each is an LLM round
  trip.
- A LinkedIn page-walk: **28 minutes**, 155 calls. The browser was ~88% of all
  measured work.
- Root cause of a 3-hour / 0-result run: a serial barrier chain with the single
  serial browser doing the most work first.

**Snapshot size was not the problem.** Those 155 calls burned only 37,591 tokens —
the lowest of seven agents in that run. A 6-call agent used 50,691. Do not
pre-optimise snapshots.

Doctrine that came out of it: **snapshot once → plan → execute atomically → verify
once.** Nothing in the browser except navigate, fill, submit, close.

## 8. Failure modes to expect

- **Dead pages dominate.** In one sample, 10 of 20 target URLs were unfetchable —
  404s, closed postings, JS-only portals, parked domains. Nothing to do with
  Playwright. Needs a cheap liveness probe *before* the browser opens.
- **MCP config is read only at startup.** Editing `.mcp.json` requires a full
  restart. Restarting without fixing the config changes nothing — a famously
  misleading combination.
- **Windows backslashes break JSON parsing.** `\U` and `\A` are invalid escapes. Use
  forward slashes everywhere.
- **An unused sibling MCP block with a placeholder token breaks server startup.**
- **`npx @playwright/mcp@latest` hangs on first run** while Chromium downloads, and
  may ask interactive install questions. Pin the version; run `--help` once manually.
- **Submit is *expected* to time out** on navigation. Confirm success by URL, not by
  the call returning.
- **File upload can reset form control state.** Re-verify every control after upload.
- **A user interrupt surfaces as `The tool use was rejected`**, not a tool failure.
  Do not build retry around it.
- **Some things are permanently defeated.** Invisible reCAPTCHA v3 scores automated
  sessions ≈0. Certain React combobox implementations cannot be driven over CDP.
  Correct handling is "mark BLOCKED and skip" — not retry.
- **Prebuilt search URLs rot.** Ids and param numbers rotate. Drive the UI.
- **Never open a tab per list item.** That pattern was the 28-minute sink above.

## 9. The Python harness

Python is the decided language for AutoWeb (see `CLAUDE.md` § Language). These are the
constraints that apply the moment the loop moves out of Claude Code — and the reason
TypeScript, where it appears at all, must sit behind MCP stdio or a JSON-in/JSON-out
CLI rather than being imported.

- Python MCP client: `mcp` (python-sdk), latest **2.2.0**, py≥3.10. On Windows you
  need **≥1.6.0** for `command="npx"` to resolve at all —
  `get_windows_executable_command` probes `.cmd/.bat/.exe/.ps1` via `shutil.which`.
  Never `cmd /c`.
- 2.x renamed: `isError`→`is_error`, `structuredContent`→`structured_content`,
  `inputSchema`→`input_schema`, `McpError`→`MCPError`,
  `streamablehttp_client`→`streamable_http_client` (now a **2-tuple**, was 3).
- **Child env is allowlisted, not inherited.** Win32 list: `APPDATA HOMEDRIVE HOMEPATH LOCALAPPDATA PATH PATHEXT PROCESSOR_ARCHITECTURE SYSTEMDRIVE SYSTEMROOT TEMP USERNAME USERPROFILE`. Pass `env={**os.environ, ...}` or the `PLAYWRIGHT_MCP_*`
  vars explicitly.
- Claude Agent SDK (python) supports `mcp_servers`. `permission_mode="acceptEdits"`
  does **not** auto-approve MCP tools — only `allowed_tools` or `bypassPermissions`.
  MCP results over 25k tokens spill to a file (`MAX_MCP_OUTPUT_TOKENS`) — relevant
  for `browser_snapshot`.
- Transport note: `--port N` serves both SSE and streamable HTTP; routing is literally
  `if (pathname.startsWith('/sse')) handleSSE() else handleStreamable()`. SSE is
  deprecated by the MCP spec but live in this repo. Binding `0.0.0.0` requires
  `--allowed-hosts` or you get `403` on the Host check.

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
| N server processes           | N browsers. Collides on port (`EADDRINUSE` — use `--port 0`) and on profile dir.   |
| `--shared-browser-context` | all clients share ONE context. Tabs bleed across clients.`browser_close` is refused.  |

**Default persistent mode breaks with ≥2 clients.** Second client fails its first
browser action:

```
Browser is already in use for <dir>, use --isolated to run multiple instances of the same browser
```

A Playwright maintainer on this: *"works fine with single client, but falls apart
when you have 2 or more… We should have made one of the two options required for
http transport."* ([playwright-mcp#1631](https://github.com/microsoft/playwright-mcp/issues/1631))

Also: **background subagents lose MCP access.** Browser work is foreground only.

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
**Never**: sessionStorage (no API — workaround is `page.evaluate` + `addInitScript`),
service-worker / Cache API, HTTP auth (use `httpCredentials`), Chrome's Login Data
and autofill.

Known bug: `indexedDB: true` silently empties `Map` and `Set` values
([playwright#42703](https://github.com/microsoft/playwright/issues/42703)).

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

sqlite files need their `-wal` / `-shm` / `-journal` siblings. **Exclude** `lockfile`,
`Singleton*`, every LevelDB `LOCK`, and caches.

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

---
description: Set up and verify Playwright MCP in the current directory, then prove a browser opens and acts
allowed-tools:
  - Bash(node -v)
  - Bash(claude mcp get playwright)
  - Bash(claude mcp list)
  - mcp__playwright__browser_navigate
  - mcp__playwright__browser_snapshot
  - mcp__playwright__browser_click
  - mcp__playwright__browser_take_screenshot
  - mcp__playwright__browser_close
---

# Set up Playwright MCP

Set up Microsoft's Playwright MCP server for Claude Code in the current working
directory, then prove it works by opening a real browser and taking actions in it.

This command is self-contained. It assumes nothing about the surrounding project —
drop it into any directory and it does the same thing. It is idempotent; re-running
it is always safe.

Work through the phases in order. **Stop at the first hard failure** and report it
plainly — a clear failure beats an improvised success. Report one short line per
check as you go, then print the Phase 5 summary.

---

## Phase 1 — Preflight (read-only, change nothing)

Run these and record results. Do not fix anything yet.

1. **Node ≥ 18** — `node -v`
   `@playwright/mcp` declares `engines: { node: ">=18" }`. Below 18 is a hard stop.

2. **Config present** — does `.mcp.json` exist in the current directory with an
   `mcpServers.playwright` entry? Expected content:
   ```json
   {
     "mcpServers": {
       "playwright": {
         "command": "npx",
         "args": ["@playwright/mcp@0.0.83"]
       }
     }
   }
   ```

3. **Chrome present.** The server's default is `channel=chrome` — *real Google
   Chrome*, not bundled Chromium. This is the most likely thing to be missing and it
   fails late, on the first browser call. There is **no `browser_install` tool** any
   more, so it cannot be fixed from inside a session.
   - Windows: `C:/Program Files/Google/Chrome/Application/chrome.exe` or
     `C:/Program Files (x86)/Google/Chrome/Application/chrome.exe`
   - macOS: `/Applications/Google Chrome.app`
   - Linux: `which google-chrome || which google-chrome-stable`

4. **Server status** — `claude mcp get playwright`
   Record the Scope and Status lines verbatim. If it reports the server defined in
   more than one scope, record that too — project scope wins, but the user should
   know about the collision.

   The `autoweb` plugin defines a `playwright` server of its own, so in a project with
   no `.mcp.json` at all this step can still report Connected. That is a pass: the
   server is there and the smoke test can run. Record which scope it came from.

---

## Phase 2 — Fix what is missing

Act only on what Phase 1 found missing.

1. **Node < 18 or absent** → STOP. Tell the user to install Node 18+ and re-run
   `/autoweb:setup-playwright`. Do not attempt to install Node.

2. **`.mcp.json` missing, or present without a playwright entry** → write exactly the
   JSON from Phase 1 step 2. If the file exists with *other* servers in it, add the
   playwright entry and leave the rest untouched.

   Skip this when Phase 1 step 4 already reported the server Connected from the plugin's
   own scope and the user did not ask for a project-scoped copy. Two definitions of the
   same server name is the collision Phase 1 asks you to record, not a thing to create.

   Add no flags. Not `--user-data-dir`, not `--isolated`, not `--headless`. Zero
   flags is the stock configuration from the official README, and browser-profile
   handling is deliberately out of scope here.

3. **Prewarm the package** — `npx @playwright/mcp@0.0.83 --help`
   Do this even when everything looks fine. It forces the npm download now instead of
   mid-task, where it surfaces as an unexplained hang. Allow up to a minute on a cold
   cache.

4. **Chrome missing** → `npx playwright install chrome`
   If that fails or Chrome cannot be installed, the fallback is
   `npx playwright install chromium` **plus** adding `"--browser", "chromium"` to the
   `args` array in `.mcp.json`. If you take the fallback, say so loudly — it is a
   deviation from stock and the user needs to know it is there.

---

## Phase 3 — Connection gate

Re-run `claude mcp get playwright`.

- **`Status: ✔ Connected`** → continue to Phase 4.

- **Pending approval, failed, or not found** → **STOP HERE.** This cannot be fixed
  from inside the running session. `.mcp.json` is read **only at Claude Code
  startup**, so a server that is not connected now will not connect until a restart,
  whatever `claude mcp` commands you run.

  Tell the user, in this order:
  1. Exit Claude Code.
  2. Restart it in this directory.
  3. **Trust the workspace** when prompted.
  4. **Approve the `playwright` server** at the project-scope prompt. Claude Code
     then writes `enabledMcpjsonServers` into `.claude/settings.local.json` itself.
  5. Re-run `/autoweb:setup-playwright`.

  Worth saying in the report: an `enabledMcpjsonServers` entry committed to a tracked
  `.claude/settings.json` is **ignored until the workspace is trusted**. It is a
  convenience, not the mechanism. The approval prompt is the mechanism.

---

## Phase 4 — Smoke test

Prove a browser opens and acts. Run exactly this sequence, nothing more:

1. `browser_navigate` → `https://example.com`
2. `browser_snapshot` — note the page title and the "Learn more" link's `ref`
3. `browser_click` on "Learn more", using the `ref` from the snapshot you just took
   — never a hardcoded ref, never one from an earlier snapshot; refs go stale
4. `browser_snapshot` — note the resulting URL and title
5. `browser_take_screenshot`
6. `browser_close`

Expected: `https://example.com/` → `https://www.iana.org/help/example-domains`, title
"Example Domains", and `browser_close` returning
`No open tabs. Navigate to a URL to create one.`

Then **verify the artifact yourself** — do not trust the tool's success message alone.
Confirm the screenshot exists under `.playwright-mcp/` and starts with the PNG magic
bytes `89 50 4E 47`.

If any step fails, report the exact error verbatim and stop. No retries, no substitute
URL or link. Errors mentioning a profile, a lock, "already in use", or a Chromium
download are diagnostic — quote them in full.

> If the user's own instructions require delegating browser tool calls to a subagent,
> follow that. The sequence and the verification are identical either way.

---

## Phase 5 — Report

Print this, filled in:

```
Playwright MCP setup
  Node                 <version>           pass/fail
  Chrome               <path or note>      pass/fail
  .mcp.json            <present/written>   pass/fail
  Server               <scope>, <status>   pass/fail
  Smoke test           <url before> -> <url after>   pass/fail
  Screenshot           <path>, <bytes>     pass/fail

Result: ready / blocked at phase <n>
```

If everything passed, say plainly that Playwright MCP is connected and verified, and
that a browser opened and took actions.

If anything is blocked, say which phase, why, and the single next action the user
should take. Do not offer a menu of options.

---

## Notes

- **On the `allowed-tools` grant.** It covers only the three read-only checks and the
  five browser tools the smoke test actually calls. Everything that *changes* the
  machine — writing `.mcp.json`, `npx ... --help`, `npx playwright install` — is
  deliberately left out, so it goes through the normal permission prompt. This matters
  because `allowed-tools` is **not** gated by workspace trust: a command shipped in a
  plugin, or checked into a repository, grants itself those tools even in a folder you
  have never trusted.
  Keep this list exact-match and minimal. Do not widen it to `Bash(npx:*)` or
  `mcp__playwright__*`.
- The server writes run artifacts (page snapshots, screenshots) to `.playwright-mcp/`
  in the working directory. If this directory is a git repo, add `.playwright-mcp/`
  to `.gitignore`. Deleting it between runs is safe and makes evidence unambiguous.
- Do not add retry or healing logic to this command. A failed setup should be
  legible, not papered over.
- The pin on `@playwright/mcp@0.0.83` is deliberate. `@latest` is how a first run
  hangs on an unexpected download, and how a working setup silently breaks later.

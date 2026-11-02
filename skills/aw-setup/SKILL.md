---
name: aw-setup
description: "Checks and installs what AutoWeb needs — Python, uv, Node, the pinned Playwright MCP, the `autoweb` CLI, a browser, Git Bash — then opts the current project in with an `autoweb.toml`, a `goals/` template, a `.gitignore` block that keeps the login out of git and the project's own `playwright` server in `.mcp.json`, and tells the user to restart the session. Handles 'aw setup', 'set up autoweb', 'install autoweb', 'prerequisites missing', 'init this project for autoweb'."
disable-model-invocation: true
---

# Set up AutoWeb

1. Check what is present.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw_setup.py" --check --cwd "$PWD"
   ```

   Show the table row for row, in the order it printed them. A `[NOTE]` row is an
   advisory and never a failure. Done when the user can see every row and its status.

2. If any row other than `shared browser server`, `identity ignored by git` or
   `identity untracked` reads MISSING, install the fixes the script can perform. Those
   three rows are about this project, not this machine; step 4 fixes the first two, and
   the third carries its own command.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw_setup.py" --install --cwd "$PWD"
   ```

   This installs the `autoweb` package and its `playwright` dependency into uv's tool
   environment, which is what puts the `autoweb` command on PATH, and then downloads the
   browser. The browser download is the slow part; add `--skip-browser` when a browser is
   already present or the user wants the install to finish now and the download later.
   It never installs `uv` or Node — for those it prints the official one-liner instead —
   and it writes nothing into any settings file. Done when the command has exited and its
   output is on screen.

3. Run the check again. For any row still MISSING, pass on the fix printed beside it
   verbatim: `uv` and Node are installed by the user from the one-liners the script
   prints for their platform, after which step 2 can run again. Done when every row
   except the three project rows named in step 2 reads OK or `[NOTE]`.

4. Opt this project in.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw_setup.py" --init-project --cwd "$PWD"
   ```

   This writes `autoweb.toml` with the documented defaults if it is absent, creates
   `goals/` with the template if it is absent, and writes a marked block into this
   folder's `.gitignore` naming every file that holds a login or a path on this machine:
   `root.json` and its `.tmp`/`.bak` pair, the lane files, `.playwright-mcp/` page
   snapshots, `runs/`, the generated lane agents and `.claude/settings.local.json`. A
   `.gitignore` in the folder works whatever the repository root is, so it covers a
   project in a subfolder of a repo, a linked worktree, and a folder that becomes a repo
   later. Inside a repo it also adds the same lines to the `info/exclude` git reads.

   It also gives the project its own browser server. The plugin ships none, so the
   session in this folder has a `playwright` server only when the project's `.mcp.json`
   declares one. The script writes `.mcp.json` with that one entry, `npx
   @playwright/mcp@<lanes.mcp_version>` and no flags, when the file is absent. It adds
   the entry beside any other servers already in the file. It leaves alone a file that
   already has a `playwright` entry, and one that is not valid JSON. It then adds
   `"playwright"` to `enabledMcpjsonServers` in `.claude/settings.local.json`, keeping
   every other key there.

   It is idempotent, rewrites only what it owns, and says what it did, one line per file.
   It exits 1 when `autoweb.toml` or `.gitignore` could not be written; show that line
   and stop there. Done when the script has exited 0 and listed what it created and what
   it left alone.

   Then run step 1's check once more. Done when `shared browser server` reads OK or
   `[NOTE]`, `identity ignored by git` reads OK or `[NOTE]`, and when `identity
   untracked` reads MISSING, its fix (`git rm --cached root.json`) has been passed on
   verbatim, because a file already in git's index is committed whatever `.gitignore`
   says. A `shared browser server` row still MISSING means `.mcp.json` did not parse:
   pass on its detail line and its fix.

5. Show the goal file format, so the new `goals/` has something to be filled from. Goals
   are per project and none of them ship with the plugin.

   ```bash
   cat "${CLAUDE_PLUGIN_ROOT}/goals/README.md"
   ```

   Done when the six headings a goal needs — `## Target`, `## Resources`, `## The work`,
   `## Assertions`, `## Constraints`, `## Reporting` — have been named, along with the
   rule that a goal whose assertion is not deterministic is not a goal yet.

6. Tell the user what is still theirs to do, in this order:
   - Create the identity by hand: `state export <url>` opens a headed browser, they log
     in themselves, and AutoWeb reads the session afterwards. It never sees a password.
   - Generate the lanes: `lanes sync`.
   - **Trust the folder.** A lane only gets its own browser once the project folder is
     trusted; Claude Code refuses to start an agent file's inline `mcpServers` block
     otherwise, and it does so silently.
   - **Restart the session** whenever this run created or changed lane agent files or
     `.mcp.json`. Agent files and MCP config are read at session start only, so a lane
     generated now does not exist for the session that generated it, and the
     `playwright` server step 4 wrote into `.mcp.json` does not start until the next
     session in this folder.

   Done when each of those four has been stated and the restart has been called for if
   lanes or `.mcp.json` changed.

Complete when `--check` reports no MISSING row, `--init-project` has run in this
project, and the user has been told about the identity, the lanes, the trust prompt and
the restart.

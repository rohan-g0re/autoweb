---
name: aw-setup
description: "Checks and installs what AutoWeb needs — Python, uv, Node, the pinned Playwright MCP, the `autoweb` CLI, a browser, Git Bash — then opts the current project in with an `autoweb.toml`, a `goals/` template and git excludes, and tells the user to restart the session. Handles 'aw setup', 'set up autoweb', 'install autoweb', 'prerequisites missing', 'init this project for autoweb'."
disable-model-invocation: true
---

# Set up AutoWeb

1. Check what is present.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw_setup.py" --check --cwd "$PWD"
   ```

   Show the table row for row, in the order it printed them. A `[NOTE]` row is an
   advisory and never a failure. Done when the user can see every row and its status.

2. If any row reads MISSING, install the fixes the script can perform.

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
   reads OK or `[NOTE]`.

4. Opt this project in.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw_setup.py" --init-project --cwd "$PWD"
   ```

   This writes `autoweb.toml` with the documented defaults if it is absent, creates an
   empty `goals/` if it is absent, and adds the runtime paths —
   `.autoweb/`, `root.json`, `lane-*.json`, `runs/`, `.claude/agents/lane-*.md`,
   `.claude/settings.local.json` — to `.git/info/exclude` rather than to the repo's own
   `.gitignore`, because those files hold live session cookies and an absolute path to
   this machine. It is idempotent and says what it did. Done when the script has listed
   what it created and what it left alone.

5. Show the goal file format, so the new `goals/` has something to be filled from. Goals
   are per project and none of them ship with the plugin.

   ```bash
   cat "${CLAUDE_PLUGIN_ROOT}/goals/README.md"
   ```

   Done when the six headings a goal needs — `## Target`, `## Resources`, `## The work`,
   `## Assertion`, `## Constraints`, `## Reporting` — have been named, along with the
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
     generated now does not exist for the session that generated it.

   Done when each of those four has been stated and the restart has been called for if
   lanes or `.mcp.json` changed.

Complete when `--check` reports no MISSING row, `--init-project` has run in this
project, and the user has been told about the identity, the lanes, the trust prompt and
the restart.

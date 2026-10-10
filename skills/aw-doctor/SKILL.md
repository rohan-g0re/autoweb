---
name: aw-doctor
description: "Diagnoses an AutoWeb project — prerequisites, the config, the identity, the lanes and the workspace trust — and puts the one printed fix beside each failing line. Handles 'aw doctor', 'check autoweb', 'is autoweb working', 'why did the lanes not open a browser', 'diagnose autoweb', 'autoweb is broken'."
disable-model-invocation: true
---

# Diagnose AutoWeb

1. Check the prerequisites.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw_setup.py" --check --cwd "$PWD"
   ```

   Done when the table is on screen, row for row.

2. Validate the project's config. This exits 2 when `autoweb.toml` is malformed, which
   is the answer rather than an error to retry.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" config check
   ```

   Done when the exit code and any named key or file have been recorded.

3. List the lanes.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" lanes list
   ```

   Read the **generated** count, not the file count. A hand-written `lane-N.md` with no
   inline `mcpServers` block is reported too, and it shares the session's one browser
   instead of owning its own — which looks like a lane and then collides with every other
   lane in a single tab. Done when the generated count and any hand-written file are
   both named.

4. For every row that reads MISSING, repeat the row and put the fix the script printed
   next to it. The fix for a missing prerequisite is `/autoweb:aw-setup`. A malformed
   `autoweb.toml` is fixed by the key the `config check` error named. Zero generated
   lanes is fixed by `lanes sync`, followed by a session restart, because agent files are
   read at session start only.

   A `[NOTE]` row is an advisory and never a failure. Pass each one on as it stands:
   - `trust` — granted, not granted, or unknown. Not granted is why four dispatched
     lanes can start zero browsers, silently; the fix is to open the folder and accept
     the prompt. Unknown means the trust record could not be read, not that it is absent.
   - `lanes` — the generated count against `lanes.max`, plus any `--user-data-dir` found
     in a generated lane that points outside the project folder.
   - `identity` — whether `root.json` exists. When it does not, the fix is
     `state export <url>`, where the user logs in by hand.
   - `playwright mcp 0.0.83` — a cold npx cache is a note, not a failure. It means the
     first browser call pays the download, which reads as an unexplained hang; warming it
     now is cheaper than debugging it later.

   Done when every MISSING row has been repeated with its fix and every `[NOTE]` row has
   been passed on.

5. When all three commands come back clean, say so in one line and give the numbers they
   printed: the generated lane count against `lanes.max`, whether `root.json` exists, and
   the trust state.

Complete when all three commands have run and every failing line has a fix beside it.

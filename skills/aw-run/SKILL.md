---
name: aw-run
description: "Runs one AutoWeb goal end to end: reads the goal file, checks the config and the identity, dispatches the browser lanes, writes the deliverable and the run log under `runs/`, then checks the goal's own assertions with `autoweb goal check` and reports pass or fail for each. Handles 'aw run', 'run the goal', 'run goals/x.md', 'execute this goal', 'do the daily digest goal'."
disable-model-invocation: true
---

# Run a goal

The argument is a goal name. It may be given with or without `.md`, and with or without
the `goals/` prefix: `daily-digest`, `daily-digest.md` and `goals/daily-digest.md` all
mean the same file. When no argument was given, list `goals/*.md` apart from `README.md`
and ask which one.

1. Read the goal file, and fix the run id: `<YYYY-MM-DD>-<goal>`, today's date from
   `date +%F`. The run's directory, `runs/<run id>/`, is named after it.

   ```bash
   ls goals/ && cat "goals/<goal>.md" && date +%F
   ```

   Hold on to its six sections: `## Target`, `## Resources`, `## The work`,
   `## Assertions`, `## Constraints` and `## Reporting`. `goals/README.md` is the format,
   not a goal. If the file does not exist, say so, list what `goals/` holds, and stop.

   Then confirm the assertions parse, before anything opens a browser:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" goal check "goals/<goal>.md" --run "<run id>" --dry-run
   ```

   Exit 2 means the goal has no checkable assertion or a malformed one; report its error
   line and stop, because the run would have nothing to be judged against. Done when the
   target URLs, the numbered sub-goals and the run id are in hand and the dry run exited
   0.

2. Validate the config. A non-zero exit here means `autoweb.toml` is wrong, and it names
   the key; stop and report that rather than running anything in a browser.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" config check
   ```

   Done when it exits 0, or when its named key has been reported and the run has stopped.

3. When `## Target` names a URL, prove the saved identity still works against it. This
   seeds a brand new isolated browser from `root.json` alone and reports what it sees, so
   a non-zero exit means the session did not survive and the lanes would all start logged
   out.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" state verify <target url>
   ```

   Keep its output; it goes in the run log. When it fails because there is no
   `root.json`, stop and tell the user to run `state export <target url>` and log in by
   hand — AutoWeb never sees a password. Done when the command has run and its exit code
   and output are recorded, or the run has stopped for a missing identity.

4. See how many lanes exist. The ceiling is the **generated** count, not the file count.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" lanes list
   ```

   Done when the generated count is known. Zero generated lanes means `lanes sync` and a
   session restart before this goal can run; say that and stop.

5. Read the lane rules, then dispatch.

   ```bash
   cat "${CLAUDE_PLUGIN_ROOT}/skills/parallel-lanes/SKILL.md"
   ```

   Split `## The work` into pieces that need nothing from each other, one lane each, up
   to the generated count. Dispatch **every lane in a single message** — a lane sent in
   its own message waits for the previous one to finish, so lanes meant to overlap take
   turns instead. Give each lane, in its own task text:
   - the target URL it owns, from `## Target`;
   - the resources it needs, from `## Resources` — the values to fill, the files to read;
   - its slice of `## The work`, numbered as the goal numbers it;
   - what counts as done for that slice, phrased as something checkable rather than a
     request;
   - the reporting rules: report a sub-goal it cannot complete as **blocked, with the
     reason**; never report a value nobody read off a page; confirm `location.href`
     before asserting; carry the URL behind every fact;
   - its last action, before it finishes: call `browser_storage_state` with `filename`
     set to the bare name `lane-<its number>.json`, which lands in the project root
     where the merge reads it, and report the path.

   Done when every independent piece of the work went to its own lane, in one message,
   and every lane has reported back.

6. Judge what came back before using any of it. **A lane that reports using
   `mcp__playwright__*` instead of its own `mcp__laneN__*` tools has landed in the
   session's shared browser; its isolation is void and so is everything it says about
   which page it was on.** Re-dispatch that lane's work rather than reconciling its
   results, and if several lanes did it, re-dispatch them one at a time. Treat a blocked
   sub-goal as a real result and keep it as blocked. Done when every lane's report is
   either usable or re-dispatched, and no fabricated value has been carried forward —
   one fabricated value fails the whole run.

7. Merge the lane states back onto `root.json`, once, after every lane has reported,
   re-dispatched lanes included. Name the lane files the usable lanes reported writing,
   and leave out any lane step 6 voided. Read the decisions first:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" merge lane-1.json lane-3.json --dry-run
   ```

   A `REFUSED:` line or a non-zero exit means the real merge would lose part of the
   identity: keep the output, skip the write, and report it. Otherwise read its
   `evicted` lines, then write:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" merge lane-1.json lane-3.json
   ```

   This writes `root.json` and keeps the previous one as `root.json.bak`. Report every
   origin it lists as needing a fresh login. Lane files carry cookies and localStorage
   only, so a login held in IndexedDB is not refreshed by a run. Done when the merge has
   run once and its output is kept, or the reason it did not run is recorded.

8. Write the deliverable.

   ```bash
   mkdir -p "runs/<run id>"
   ```

   The deliverable itself is whatever `## Reporting` in the goal asks for, written into
   that directory. Beside it write `run-log.md` holding: how the work was split and which
   lane took which slice; each lane's dispatch text in short; the `state verify` output
   from step 3; each lane's reported facts with the URL behind each one; every blocked
   sub-goal with its reason; any lane that was re-dispatched and why; and the merge
   output from step 7, or why it did not run. Done when both the deliverable and
   `run-log.md` exist under `runs/<run id>/`.

9. Check the goal's assertions. The command evaluates every `[[assert]]` in
   `## Assertions` (browser checks in a fresh browser seeded from `root.json`, command
   checks in the project root) and writes `runs/<run id>/status.json`.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" goal check "goals/<goal>.md" --run "<run id>"
   ```

   Its verdict is the run's verdict. Exit 0 means every assertion passed and
   `status.json` says `done: true`; exit 1 means at least one is `FAIL` or `ERROR`;
   exit 2 means the goal or an input is missing or malformed, and no `status.json` was
   written. Append its output, verbatim, to `run-log.md` under a `## Assertions` heading.
   Done when the command has run once and its output and exit code are in `run-log.md`.

10. Report, one line per assertion, copied from the command's output: its id, its kind,
    and `PASS`, `FAIL` or `ERROR` with the detail. Then one line for the run: where the
    deliverable is, the path of `status.json`, and how many assertions passed out of
    how many.

    **This is one pass. There is no loop.** A failed assertion reported plainly is the
    output. Leave the goal as written and the run as it ended; re-running is the user's
    call.

Complete when `runs/<run id>/status.json` exists with `done: true`, or the report names
each assertion that failed and why (or, on exit 2, the error that stopped the check); the
deliverable and `run-log.md` are under `runs/<run id>/`; the merge ran once or the report
says why it did not; and no retry was attempted.

---
name: aw-loop
description: "Keeps working on one AutoWeb goal, running /autoweb:aw-run again after every failed check, until every assertion in the run's status.json passes or the iteration cap is hit. Handles 'aw loop', 'loop this goal until it passes', 'keep going until the goal passes', 'stop the loop', 'loop status'."
argument-hint: "<goal.md> [--max N] | --stop | --status"
disable-model-invocation: true
---

# Loop a goal until it passes

Arguments: $ARGUMENTS

1. When the arguments are `--stop` or `--status`, run the script with that flag alone and
   show its output.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw_loop.py" -C "$PWD" --status
   ```

   `--stop` is the kill switch: it removes the loop file, and the next time you end your
   turn the session stops as it normally would. Done when the output is shown; there is
   nothing more to do.

2. Start the loop. The first argument is the goal (`daily-digest`, `daily-digest.md` or
   `goals/daily-digest.md`). `<N>` is the user's `--max`, or 5 when they gave none.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw_loop.py" -C "$PWD" --start "<goal>" --max <N> --session "${CLAUDE_SESSION_ID}"
   ```

   A non-zero exit means no loop was started: show its message and stop. Done when it
   exits 0 and you hold the four lines it printed: `run id`, `status file`,
   `aw-run skill` and `autoweb plugin root`.

3. Read the `aw-run` skill from the path it printed.

   ```bash
   cat "<aw-run skill path>"
   ```

   That skill is user-invoked only, so Claude Code keeps the Skill tool from starting it;
   the user started it by starting this loop, and you follow it from the file. Make two
   substitutions as you follow it: where its commands use the plugin-root variable, write
   the printed `autoweb plugin root`; and where it names the run directory from today's
   date, use `runs/<run id>/` with the printed run id. Done when its numbered steps are in
   hand.

4. Run one pass: every step of `aw-run`, in order, for this goal. Its last step runs
   `autoweb goal check <goal> --run <run id>`, which writes `runs/<run id>/status.json`.
   Finish with the report `aw-run` asks for and end your turn. `aw-run`'s own rule holds
   inside a pass: one attempt per assertion, reported plainly. Done when `status.json`
   has been written for this pass and the report is out.

5. When you end your turn, the Stop hook reads `status.json` and decides. When it blocks
   you, its reason says `iteration k of N` and names every failing assertion with its
   detail. Read that, then `runs/<run id>/run-log.md`, and work out why each one failed.
   Change the approach: the route, the waits, the lane split, what is read and where.
   The goal file and its assertions stay exactly as written. Then run step 4 again with
   the same run id, so `status.json` is overwritten. Done when a new pass has written a
   new `status.json`.

6. The Stop hook ends the loop, and removes `.autoweb/active-loop.json`, in one of three
   ways: every assertion passed (it lets you stop and says nothing); the cap was reached
   (it tells the user which assertions still fail); or you came back from a block and
   ended the turn without a new `autoweb goal check` (it tells the user the loop
   stalled). Leave the loop file for the hook. The user can end the loop at any time by
   deleting that file or running `/autoweb-loop:aw-loop --stop`.

Complete when the Stop hook has let the session stop: every assertion passed, or the cap
or a stall ended the loop and the user has been told what still fails.

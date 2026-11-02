"""Stop hook: keep the session on an AutoWeb goal until its assertions pass.

This runs on EVERY Stop, in every project, for every user who installed the
plugin. So the first thing it does is the cheapest test that can say "not here":
is there a `.autoweb/active-loop.json` in the session's directory, or above it up
to the project root? When there is not, it exits 0 with no output, no file
written and nothing logged. That path is the product's manners.

When a loop is active, it reads `runs/<run>/status.json`, which `autoweb goal
check` writes at the end of every `/autoweb:aw-run`, and decides:

  done                      remove the loop file, say nothing, let Claude stop
  not this session's loop   say nothing, let Claude stop
  stalled (see below)       remove the loop file, tell the user, let Claude stop
  iteration >= max          remove the loop file, tell the user what still fails
  otherwise                 count one more iteration and block, with a reason
                            that names each failing assertion

"Stalled" is how this hook honours `stop_hook_active`. That field is true when
Claude is already continuing because a Stop hook blocked it, and the docs say to
check it so a hook does not block on a condition that will never resolve. A loop
has to block while it is true - that is what a loop is - so it cannot simply
step aside. Instead it records which `status.json` it last blocked on, and when
Claude comes back from a block without having rewritten that file, nothing has
changed and blocking again would repeat the same instruction. The loop ends.

The iteration cap is the real guard and the stall rule is not a substitute for
it: a goal that is re-checked every time and fails every time makes progress by
the stall rule's measure and would run for ever without the cap. Claude Code's
own 8-continuation cap does not cover it either, because that count resets each
time Claude calls a tool, and every iteration calls many. docs/DESIGN.md has the
sources.

Every exception exits 0 with no output. A broken loop must never trap a user in
their session. The traceback goes to `.autoweb/loop.log`, and only when a loop
file exists: a project that never started a loop gets no log either.
"""
from __future__ import annotations

import json
import os
import sys


def _payload():
    """The hook JSON on stdin, or None when it is not a JSON object."""
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _session_matches(loop: dict, session: str) -> bool:
    """A loop belongs to the session that started it; another session in the same
    folder is not held. An unbound loop is claimed by the first Stop that sees it."""
    owner = loop.get("session")
    if not isinstance(owner, str) or not owner or "CLAUDE_SESSION_ID" in owner:
        return True
    return not session or owner == session


def _fingerprint(path, status: dict) -> str | None:
    """What identifies one write of status.json: its checked_at and its mtime.

    Either alone would do in practice; both together survive a clock with
    one-second resolution and a file system with coarse mtimes."""
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return None
    return f"{status.get('checked_at')}@{mtime}"


def decide(project, payload: dict) -> dict | None:
    """The hook's whole decision. Returns the JSON to print, or None for silence."""
    from pathlib import Path

    import loop_common as lc

    project = Path(project)
    active = lc.active_path(project)
    loop = lc.validate_loop(lc.read_json(active))
    session = str(payload.get("session_id") or "")

    if not _session_matches(loop, session):
        return None

    goal, run = loop["goal"], loop["run"]
    iteration, maximum = loop["iteration"], loop["max_iterations"]
    status_file = lc.status_path(project, run)

    status = None
    stale = False
    if status_file.is_file():
        status = lc.validate_status(lc.read_json(status_file))
        checked = lc.parse_z(status.get("checked_at"))
        started = lc.parse_z(loop.get("started"))
        if checked is not None and started is not None and checked < started:
            # Written before this loop began, by an earlier run with the same id.
            status, stale = None, True

    if status is not None and status["done"] is True and not lc.failing(status):
        active.unlink(missing_ok=True)
        return None

    if status is None:
        what = (f"runs/{run}/status.json predates this loop" if stale
                else f"no status.json at runs/{run}/status.json")
        still = what
    else:
        still = "failing: " + lc.describe(lc.failing(status))

    fingerprint = _fingerprint(status_file, status) if status is not None else None
    # Only after this hook has blocked once: `stop_hook_active` is also true when
    # some other plugin's Stop hook did the blocking.
    if (payload.get("stop_hook_active") is True and "last_status" in loop
            and fingerprint == loop["last_status"]):
        active.unlink(missing_ok=True)
        return {"systemMessage": lc.bounded(
            f"{lc.APP}: stopped at iteration {iteration} of {maximum} for {goal}: the "
            f"last continuation ended without a new `autoweb goal check`, so another "
            f"would repeat the same instruction. Still {still}. Loop file removed."
        )}

    if iteration >= maximum:
        active.unlink(missing_ok=True)
        return {"systemMessage": lc.bounded(
            f"{lc.APP}: iteration cap reached ({iteration} of {maximum}) for {goal}, "
            f"run {run}. Still {still}. Loop file removed; start a new loop to go on."
        )}

    loop["iteration"] = iteration + 1
    loop["last_status"] = fingerprint
    if session and not loop.get("session"):
        loop["session"] = session
    lc.write_json_atomic(active, loop)

    if status is None:
        todo = (f"Do a full aw-run pass for {goal}, following the aw-run skill file as "
                f"before, to the end; its last step, `autoweb goal check {goal} --run {run}`, "
                f"writes that file.")
    else:
        todo = (f"Fix what failed (the approach, never the goal file or its assertions), "
                f"then do another full aw-run pass for {goal}, following the aw-run skill "
                f"file as before, with run id {run}, so `autoweb goal check {goal} --run "
                f"{run}` rewrites runs/{run}/status.json.")
    reason = (f"iteration {iteration + 1} of {maximum}; {still}. {todo} "
              f"The loop ends when every assertion passes. Leave .autoweb/active-loop.json "
              f"alone; the Stop hook removes it.")
    return {"decision": "block", "reason": lc.bounded(reason)}


def _log_failure(project) -> None:
    """Append the traceback to `.autoweb/loop.log`. Never raises."""
    try:
        import traceback
        from datetime import UTC, datetime

        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        path = os.path.join(project, ".autoweb", "loop.log")
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"--- {stamp} loop_stop\n{traceback.format_exc()}")
    except Exception:
        pass


def main() -> int:
    project = None
    try:
        payload = _payload()
        start = (payload or {}).get("cwd") or os.getcwd()
        if not os.path.isdir(start):
            # e.g. an MSYS path such as /tmp/x handed to a Windows Python
            start = os.getcwd()
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import loop_common as lc

        project = lc.find_loop(start)
        if project is None:
            return 0
        if payload is None:
            # Unreadable input with a loop active: let Claude stop, and say why in
            # the log rather than guess at a session and block it.
            raise ValueError("hook input on stdin is not a JSON object")
        out = decide(project, payload)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    except Exception:
        if project is not None and os.path.isfile(
                os.path.join(project, ".autoweb", "active-loop.json")):
            _log_failure(project)
    return 0


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # a Stop hook must never hold the session, whatever happened
        pass
    sys.exit(0)

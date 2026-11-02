"""aw_loop - start, stop and inspect an autoweb-loop in one AutoWeb project.

    aw_loop.py [-C DIR] --start GOAL [--max N] [--run ID] [--session ID]
    aw_loop.py [-C DIR] --stop
    aw_loop.py [-C DIR] --status

`--start` is the only thing in this plugin that writes `.autoweb/active-loop.json`.
It refuses outside an AutoWeb project (no `autoweb.toml`), refuses a goal file that
is not there, and refuses to replace a loop that is already running. It also finds
the installed `autoweb` plugin and prints where its `aw-run` skill is, because that
skill sets `disable-model-invocation: true`: Claude cannot invoke it through the
Skill tool, so the loop skill reads it from disk and follows it.

`--stop` is the kill switch: it deletes the loop file. `--status` is a read-only
report: the loop, the run's `status.json`, and the last lines of `loop.log`.

Exit codes: 0 done, 1 refused (the message says why), 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loop_common as lc  # noqa: E402  (also reconfigures stdout/stderr to UTF-8)


def fwd(path) -> str:
    return str(path).replace("\\", "/")


# --------------------------------------------------------------------------- goal


def resolve_goal(project: Path, goal: str) -> Path | None:
    """`x`, `x.md`, `goals/x.md` or any relative path to an existing `.md`."""
    raw = goal.strip().replace("\\", "/")
    candidates = [project / raw]
    if not raw.endswith(".md"):
        candidates.append(project / f"{raw}.md")
    if not raw.startswith(f"{lc.GOALS_DIRNAME}/"):
        candidates.append(project / lc.GOALS_DIRNAME / raw)
        if not raw.endswith(".md"):
            candidates.append(project / lc.GOALS_DIRNAME / f"{raw}.md")
    for path in candidates:
        if path.is_file() and path.suffix == ".md":
            return path.resolve()
    return None


def run_id_for(goal_path: Path, today: date | None = None) -> str:
    """`YYYY-MM-DD-<goal>`: the directory `/autoweb:aw-run` names a run by."""
    return f"{(today or date.today()).isoformat()}-{goal_path.stem}"


# ------------------------------------------------------------- the autoweb plugin


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def find_aw_run(project: Path) -> Path | None:
    """The installed `autoweb` plugin's `skills/aw-run/SKILL.md`, or None.

    `$AW_LOOP_AUTOWEB_ROOT` names a plugin root directly (a `--plugin-dir`
    checkout). Otherwise `plugins/installed_plugins.json` under the Claude config
    directory: an install scoped to this project first, then a user install.
    """
    rel = Path("skills") / "aw-run" / "SKILL.md"
    override = os.environ.get("AW_LOOP_AUTOWEB_ROOT")
    if override:
        path = Path(override) / rel
        return path if path.is_file() else None
    registry = claude_config_dir() / "plugins" / "installed_plugins.json"
    try:
        plugins = json.loads(registry.read_text(encoding="utf-8")).get("plugins", {})
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(plugins, dict):
        return None
    ranked = []
    for key, entries in plugins.items():
        if not isinstance(key, str) or key.split("@", 1)[0] != "autoweb":
            continue
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not entry.get("installPath"):
                continue
            path = Path(str(entry["installPath"])) / rel
            if not path.is_file():
                continue
            here = entry.get("projectPath")
            mine = bool(here) and os.path.normcase(os.path.abspath(str(here))) == \
                os.path.normcase(str(project))
            ranked.append((0 if mine else 1 if entry.get("scope") == "user" else 2, path))
    ranked.sort(key=lambda pair: pair[0])
    return ranked[0][1] if ranked else None


# ------------------------------------------------------------------------- commands


def cmd_start(project: Path, goal: str, maximum: int, run: str | None,
              session: str | None) -> int:
    if not (project / lc.CONFIG_FILENAME).is_file():
        print(f"{lc.APP}: {fwd(project)} has no {lc.CONFIG_FILENAME}, so it is not an "
              "AutoWeb project. Run this from the project folder.")
        return 1
    if not 1 <= maximum <= lc.MAX_MAX:
        print(f"{lc.APP}: --max must be between 1 and {lc.MAX_MAX}, got {maximum}.")
        return 2
    goal_path = resolve_goal(project, goal)
    if goal_path is None:
        print(f"{lc.APP}: no goal file for {goal!r} under {fwd(project)}. Goals: "
              + (", ".join(sorted(p.name for p in (project / lc.GOALS_DIRNAME).glob("*.md")
                                  if p.name.lower() != "readme.md")) or "none"))
        return 1
    active = lc.active_path(project)
    if active.exists():
        try:
            current = json.loads(active.read_text(encoding="utf-8"))
            what = f"{current.get('goal')} (iteration {current.get('iteration')})"
        except (OSError, ValueError, AttributeError):
            what = "an unreadable loop file"
        print(f"{lc.APP}: a loop is already active here: {what}. "
              "Stop it first with /autoweb-loop:aw-loop --stop.")
        return 1
    aw_run = find_aw_run(project)
    if aw_run is None:
        print(f"{lc.APP}: the autoweb plugin is not installed, or has no aw-run skill. "
              "Install it first: /plugin install autoweb@autoweb")
        return 1

    try:
        goal_rel = goal_path.relative_to(project).as_posix()
    except ValueError:
        print(f"{lc.APP}: the goal file must be inside the project, got {fwd(goal_path)}.")
        return 1
    run_id = run or run_id_for(goal_path)
    if "/" in run_id or "\\" in run_id or run_id in (".", ".."):
        print(f"{lc.APP}: {run_id!r} is not a run id.")
        return 2
    if session and "CLAUDE_SESSION_ID" in session:
        session = None  # the substitution did not happen; the hook claims it instead
    loop = {
        "goal": goal_rel,
        "run": run_id,
        "max_iterations": maximum,
        "iteration": 1,
        "started": lc.now_z(),
        "session": session or None,
    }
    lc.write_json_atomic(active, loop)
    root = aw_run.parent.parent.parent
    print(f"{lc.APP}: loop started for {goal_rel}, iteration 1 of {maximum}.")
    print(f"run id: {run_id}")
    print(f"status file: runs/{run_id}/status.json")
    print(f"aw-run skill: {fwd(aw_run)}")
    print(f"autoweb plugin root: {fwd(root)}")
    print(f"loop file: {fwd(active)}")
    return 0


def cmd_stop(project: Path) -> int:
    active = lc.active_path(project)
    if not active.exists():
        print(f"{lc.APP}: no active loop in {fwd(project)}.")
        return 0
    try:
        loop = json.loads(active.read_text(encoding="utf-8"))
        what = f"{loop.get('goal')} at iteration {loop.get('iteration')} of " \
               f"{loop.get('max_iterations')}"
    except (OSError, ValueError, AttributeError):
        what = "an unreadable loop file"
    active.unlink(missing_ok=True)
    print(f"{lc.APP}: loop stopped: {what}. The next Stop lets the session end.")
    return 0


def _status_lines(project: Path, run: str) -> list:
    path = lc.status_path(project, run)
    if not path.is_file():
        return [f"status.json: none yet at runs/{run}/status.json"]
    try:
        status = lc.validate_status(lc.read_json(path))
    except (OSError, ValueError) as exc:
        return [f"status.json: unreadable ({exc})"]
    lines = [f"status.json: checked {status.get('checked_at')}, done {status['done']}"]
    bad = lc.failing(status)
    total = len(status.get("assertions", []))
    lines.append(f"assertions: {total - len(bad)} of {total} PASS")
    if bad:
        lines.append("failing: " + lc.describe(bad))
    return lines


def cmd_status(project: Path) -> int:
    print(f"project: {fwd(project)}")
    print(f"autoweb.toml: {'present' if (project / lc.CONFIG_FILENAME).is_file() else 'MISSING'}")
    aw_run = find_aw_run(project)
    print(f"aw-run skill: {fwd(aw_run) if aw_run else 'NOT FOUND (install autoweb@autoweb)'}")
    active = lc.active_path(project)
    code = 0
    if not active.exists():
        print("loop: none active")
    else:
        try:
            loop = lc.validate_loop(lc.read_json(active))
        except (OSError, ValueError) as exc:
            print(f"loop: UNREADABLE ({exc}); the Stop hook ignores it. "
                  "Remove it with --stop.")
            code = 1
        else:
            print(f"loop: {loop['goal']}, run {loop['run']}, iteration "
                  f"{loop['iteration']} of {loop['max_iterations']}, started {loop['started']}"
                  f", session {loop.get('session') or 'unclaimed'}")
            for line in _status_lines(project, loop["run"]):
                print(line)
    log = lc.log_path(project)
    if log.is_file():
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
        print(f"loop.log: {fwd(log)} (last lines below)")
        for line in tail:
            print(f"  {line}")
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="aw_loop.py", description=__doc__.split("\n")[0])
    parser.add_argument("-C", dest="project", default=None,
                        help="the AutoWeb project directory (default: the current one)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", metavar="GOAL", help="start a loop on this goal")
    mode.add_argument("--stop", action="store_true", help="remove the loop file")
    mode.add_argument("--status", action="store_true", help="report the loop's state")
    parser.add_argument("--max", type=int, default=lc.DEFAULT_MAX, dest="maximum",
                        help=f"iteration cap (default {lc.DEFAULT_MAX})")
    parser.add_argument("--run", default=None, help="run id (default YYYY-MM-DD-<goal>)")
    parser.add_argument("--session", default=None, help="the session that owns the loop")
    args = parser.parse_args(argv)
    project = Path(args.project or os.getcwd()).resolve()
    if args.start is not None:
        return cmd_start(project, args.start, args.maximum, args.run, args.session)
    if args.stop:
        return cmd_stop(project)
    return cmd_status(project)


if __name__ == "__main__":
    sys.exit(main())

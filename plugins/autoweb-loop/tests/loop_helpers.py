"""Paths and helpers the loop tests share: a real bash, and writers for the loop file,
`status.json` and the Stop payload.

A module of its own, not `conftest.py`, because the tests import from it by name, and the
repository's root suite has a `conftest` module too: `from conftest import ...` would get
whichever of the two was imported last. Every path is found from this file, so the tests
run the scripts of the plugin they sit in, wherever the repository is checked out.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import date
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN / "scripts"
PY_SH = str(SCRIPTS / "py.sh").replace("\\", "/")
LOOP_STOP = str(SCRIPTS / "loop_stop.py").replace("\\", "/")
AW_LOOP = str(SCRIPTS / "aw_loop.py").replace("\\", "/")


def _find_bash():
    """A POSIX bash. On Windows, Git Bash - never a WSL or Store stub."""
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe",
                          r"C:\Program Files\Git\usr\bin\bash.exe",
                          r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.isfile(candidate):
                return candidate
        found = shutil.which("bash")
        if found and not any(part in found.lower() for part in ("system32", "windowsapps")):
            return found
        return None
    return shutil.which("bash")


BASH = _find_bash()


def fwd(path) -> str:
    return str(path).replace("\\", "/")


RUN = "2026-11-01-feed"


def write_loop(project: Path, **over) -> Path:
    loop = {"goal": "goals/feed.md", "run": RUN, "max_iterations": 5, "iteration": 1,
            "started": "2026-11-01T10:00:00Z", "session": None}
    loop.update(over)
    path = project / ".autoweb" / "active-loop.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(loop), encoding="utf-8")
    return path


def read_loop(project: Path) -> dict:
    return json.loads((project / ".autoweb" / "active-loop.json").read_text(encoding="utf-8"))


def assertion(ident, status="PASS", detail="ok", kind="url"):
    return {"id": ident, "kind": kind, "status": status, "detail": detail}


def write_status(project: Path, assertions, run=RUN, checked_at="2026-11-01T10:30:00Z",
                 done=None) -> Path:
    if done is None:
        done = all(a["status"] == "PASS" for a in assertions)
    path = project / "runs" / run / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "goal": "goals/feed.md", "run": run,
                                "checked_at": checked_at, "assertions": assertions,
                                "done": done}), encoding="utf-8")
    return path


def payload(cwd, session="sess-1", active=False) -> str:
    return json.dumps({"session_id": session, "transcript_path": "/dev/null",
                       "cwd": fwd(cwd), "hook_event_name": "Stop",
                       "stop_hook_active": active})


TODAY = date.today().isoformat()

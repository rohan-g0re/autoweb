"""loop_common - the one place autoweb-loop decides names, paths and file formats.

The Stop hook (`loop_stop.py`) and the writer the skill calls (`aw_loop.py`) both
import this, so they agree by construction on where the loop file lives, what it
holds, where a run's `status.json` is, and how a failing assertion is described.

Standard library only, Python 3.11+ (the floor `py.sh` enforces). Imports are kept
to what the Stop hook's fast path can afford: it runs on every Stop, in every
project, for every user who installed the plugin.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Windows consoles default to cp1252; assertion details carry arbitrary text.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

APP = "autoweb-loop"
SCRIPTS_DIR = Path(__file__).resolve().parent

# The AutoWeb project marker, owned by the `autoweb` plugin. Read, never written.
CONFIG_FILENAME = "autoweb.toml"
STATE_DIRNAME = ".autoweb"
# The only file autoweb-loop owns. Nothing in `autoweb` reads or writes it.
ACTIVE_FILENAME = "active-loop.json"
LOG_FILENAME = "loop.log"
GOALS_DIRNAME = "goals"
RUNS_DIRNAME = "runs"
STATUS_FILENAME = "status.json"

DEFAULT_MAX = 5
MAX_MAX = 50

# Bounds on what one block reason carries. The reason is read by the model on every
# iteration, so it names what failed and stops; the run log holds the rest.
DETAIL_CHARS = 300
REASON_CHARS = 2000


def active_path(project: Path) -> Path:
    return project / STATE_DIRNAME / ACTIVE_FILENAME


def log_path(project: Path) -> Path:
    return project / STATE_DIRNAME / LOG_FILENAME


def status_path(project: Path, run: str) -> Path:
    return project / RUNS_DIRNAME / run / STATUS_FILENAME


def find_loop(start: str | os.PathLike) -> Path | None:
    """The project directory whose `.autoweb/active-loop.json` governs `start`.

    Walks up from `start`, so a session that has `cd`-ed into `runs/` is still the
    session in that project. It stops at the first directory holding `autoweb.toml`:
    that is the project, and a loop file further up belongs to some other folder.
    Two `stat` calls per level and no reads, because this is the path every Stop in
    every project takes.
    """
    here = Path(start)
    for directory in (here, *here.parents):
        if os.path.isfile(os.path.join(directory, STATE_DIRNAME, ACTIVE_FILENAME)):
            return directory
        if os.path.isfile(os.path.join(directory, CONFIG_FILENAME)):
            return None
    return None


def read_json(path: Path):
    """Parse a JSON file. Raises on a missing or malformed file; callers decide."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json_atomic(path: Path, obj) -> None:
    """Write JSON through a temp file and a rename, so a reader never sees half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def now_z() -> str:
    """UTC, second resolution, `Z` suffix: the format `status.json` uses."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_z(value) -> datetime | None:
    """A `...Z` timestamp as an aware datetime, or None when it is not one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def validate_loop(loop) -> dict:
    """Check the shape of `active-loop.json`. Raises ValueError when it is wrong."""
    if not isinstance(loop, dict):
        raise ValueError(f"{ACTIVE_FILENAME} is not a JSON object")
    for key, kind in (("goal", str), ("run", str), ("max_iterations", int),
                      ("iteration", int), ("started", str)):
        value = loop.get(key)
        if not isinstance(value, kind) or isinstance(value, bool):
            raise ValueError(f"{ACTIVE_FILENAME}: {key} is missing or not a {kind.__name__}")
    if loop["max_iterations"] < 1 or loop["iteration"] < 1:
        raise ValueError(f"{ACTIVE_FILENAME}: iteration and max_iterations must be >= 1")
    run = loop["run"]
    if not run or "/" in run or "\\" in run or run in (".", ".."):
        raise ValueError(f"{ACTIVE_FILENAME}: run {run!r} is not a run id")
    return loop


def validate_status(status) -> dict:
    """Check the shape of `runs/<run>/status.json` (schema 1). Raises ValueError."""
    if not isinstance(status, dict):
        raise ValueError(f"{STATUS_FILENAME} is not a JSON object")
    if not isinstance(status.get("done"), bool):
        raise ValueError(f"{STATUS_FILENAME}: done is missing or not a boolean")
    assertions = status.get("assertions")
    if not isinstance(assertions, list) or not all(isinstance(a, dict) for a in assertions):
        raise ValueError(f"{STATUS_FILENAME}: assertions is missing or not a list of objects")
    return status


def failing(status: dict) -> list:
    """The assertions whose status is not PASS, in the order the goal numbers them."""
    return [a for a in status.get("assertions", []) if a.get("status") != "PASS"]


def describe(assertions: list) -> str:
    """`id [FAIL]: detail; id [ERROR]: detail`, each detail bounded."""
    parts = []
    for item in assertions:
        ident = str(item.get("id") or "?")
        state = str(item.get("status") or "?")
        detail = " ".join(str(item.get("detail") or "").split())
        if len(detail) > DETAIL_CHARS:
            detail = detail[: DETAIL_CHARS - 3] + "..."
        parts.append(f"{ident} [{state}]: {detail}" if detail else f"{ident} [{state}]")
    return "; ".join(parts) if parts else "no assertion is marked failing, but done is false"


def bounded(text: str, limit: int = REASON_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."

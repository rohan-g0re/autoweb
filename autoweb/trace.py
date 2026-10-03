"""Decide whether lanes really ran at the same time, from their own timestamps.

An agent cannot tell you this. From inside an orchestrator a staggered dispatch and a
parallel one look identical, and this project has already published one defect that did
not exist by trusting a transcript over a measurement. So the question gets an arithmetic
answer: overlap either exists or it does not.

The input is each lane's four marks, taken inside its own browser. The output is a
verdict plus the interval during which every lane was simultaneously alive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class TraceError(Exception):
    """A trace file that cannot be read, or marks that cannot be believed."""


MARKS = ("T0_start", "T1_loaded", "T2_read", "T3_end")


@dataclass(frozen=True)
class LaneTrace:
    """One lane's marks, in the order it took them."""

    lane: str
    marks: dict[str, datetime]

    @property
    def started(self) -> datetime:
        return self.marks["T0_start"]

    @property
    def loaded(self) -> datetime:
        return self.marks["T1_loaded"]

    @property
    def ended(self) -> datetime:
        return self.marks["T3_end"]

    @property
    def duration_seconds(self) -> float:
        return (self.ended - self.started).total_seconds()


@dataclass(frozen=True)
class Verdict:
    """The answer, and enough of the working to argue with it."""

    concurrent: bool
    lanes: list[LaneTrace]
    overlap_start: datetime | None
    overlap_end: datetime | None
    serial_pairs: list[tuple[str, str]]

    @property
    def overlap_seconds(self) -> float:
        if self.overlap_start is None or self.overlap_end is None:
            return 0.0
        return max(0.0, (self.overlap_end - self.overlap_start).total_seconds())


def _parse_time(raw: str, where: str) -> datetime:
    """Parse an ISO-8601 instant, tolerating the trailing Z that JavaScript emits."""
    if not isinstance(raw, str):
        raise TraceError(f"{where}: expected an ISO timestamp string, got "
                         f"{type(raw).__name__}")
    text = raw.strip()
    if text.endswith(("z", "Z")):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TraceError(
            f"{where}: {raw!r} is not an ISO-8601 instant. The lane was asked for "
            f"`new Date().toISOString()`, which looks like "
            f"'2026-10-03T14:08:18.024Z'."
        ) from exc
    if value.tzinfo is None:
        raise TraceError(
            f"{where}: {raw!r} has no timezone. Comparing lanes across an unknown "
            f"offset would decide concurrency by accident."
        )
    return value


def parse(data: object, *, source: str = "<trace>") -> list[LaneTrace]:
    """Read `{lane: {mark: iso}}`, or a list of `{lane: ..., marks: {...}}`."""
    if isinstance(data, list):
        items = []
        for n, entry in enumerate(data):
            if not isinstance(entry, dict) or "lane" not in entry:
                raise TraceError(f"{source}: item {n} needs a 'lane' key")
            items.append((str(entry["lane"]), entry.get("marks", entry)))
    elif isinstance(data, dict):
        items = [(str(lane), marks) for lane, marks in data.items()]
    else:
        raise TraceError(f"{source}: expected an object or a list, got "
                         f"{type(data).__name__}")

    lanes: list[LaneTrace] = []
    for lane, marks in items:
        if not isinstance(marks, dict):
            raise TraceError(f"{source}: {lane}: expected an object of marks")
        missing = [m for m in MARKS if m not in marks]
        if missing:
            raise TraceError(
                f"{source}: {lane} is missing {', '.join(missing)}. All four marks are "
                f"needed: a lane that only reports its start cannot be shown to have "
                f"overlapped anything."
            )
        parsed = {m: _parse_time(marks[m], f"{source}: {lane}.{m}") for m in MARKS}
        ordered = [parsed[m] for m in MARKS]
        if ordered != sorted(ordered):
            raise TraceError(
                f"{source}: {lane}'s marks are out of order: "
                f"{', '.join(f'{m}={parsed[m].isoformat()}' for m in MARKS)}. "
                f"Either they were not taken when the goal says, or they were written "
                f"from memory afterwards."
            )
        lanes.append(LaneTrace(lane=lane, marks=parsed))

    if len(lanes) < 2:
        raise TraceError(f"{source}: need at least two lanes to talk about overlap, "
                         f"got {len(lanes)}")
    if len({lane.lane for lane in lanes}) != len(lanes):
        raise TraceError(f"{source}: two lanes share a name, so their marks cannot be "
                         f"told apart")
    return lanes


def judge(lanes: list[LaneTrace]) -> Verdict:
    """All lanes overlap when the latest load precedes the earliest end.

    `T1_loaded` rather than `T0_start` on purpose. A lane exists from `T0`, but its
    browser is only demonstrably alive once a page has loaded in it, and the claim being
    tested is about browsers.
    """
    latest_load = max(lane.loaded for lane in lanes)
    earliest_end = min(lane.ended for lane in lanes)

    serial: list[tuple[str, str]] = []
    for first in lanes:
        for second in lanes:
            if first.lane != second.lane and first.ended <= second.started:
                serial.append((first.lane, second.lane))

    concurrent = latest_load < earliest_end
    return Verdict(
        concurrent=concurrent,
        lanes=sorted(lanes, key=lambda lane: lane.started),
        overlap_start=latest_load if concurrent else None,
        overlap_end=earliest_end if concurrent else None,
        serial_pairs=serial,
    )


def load(path: Path) -> list[LaneTrace]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise TraceError(f"{path}: no trace file here") from exc
    except json.JSONDecodeError as exc:
        raise TraceError(f"{path}: not valid JSON: {exc}") from exc
    except OSError as exc:
        raise TraceError(f"{path}: cannot be read: {exc}") from exc
    return parse(raw, source=str(path))


__all__ = ["LaneTrace", "MARKS", "TraceError", "Verdict", "judge", "load", "parse"]

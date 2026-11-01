"""Simultaneity is decided here, by arithmetic, because nobody's transcript can decide it.

This project has already published a defect that did not exist, by reading a stream the
wrong way and believing the conclusion over the measurement. The lesson was not "be more
careful"; it was that a claim about concurrency needs a number and an exit code. These
tests pin the arithmetic and, just as importantly, the refusals: a trace that cannot be
believed must fail loudly rather than quietly return a verdict.
"""

from __future__ import annotations

import json

import pytest

from autoweb.trace import TraceError, judge, load, parse


def marks(t0, t1, t2, t3):
    """Four ISO instants on one arbitrary day, given as seconds past the minute."""
    def at(sec: int) -> str:
        return f"2026-10-03T14:00:{sec:02d}.000Z"
    return {"T0_start": at(t0), "T1_loaded": at(t1), "T2_read": at(t2), "T3_end": at(t3)}


# --- the verdict ------------------------------------------------------------


def test_four_overlapping_lanes_are_concurrent():
    """The shape a passing run has: staggered starts, but all four still alive at once."""
    verdict = judge(parse({
        "lane-1": marks(0, 3, 25, 30),
        "lane-3": marks(1, 5, 26, 31),
        "lane-4": marks(2, 6, 27, 32),
        "lane-5": marks(3, 8, 28, 33),
    }))
    assert verdict.concurrent
    # Latest load is 8s, earliest end is 30s.
    assert verdict.overlap_seconds == pytest.approx(22.0)


def test_lanes_that_take_turns_are_not_concurrent():
    verdict = judge(parse({
        "lane-1": marks(0, 1, 4, 5),
        "lane-3": marks(6, 7, 9, 10),
    }))
    assert not verdict.concurrent
    assert ("lane-1", "lane-3") in verdict.serial_pairs


def test_staggered_dispatch_can_still_be_concurrent():
    """Overlap is the claim, not simultaneous launch. A lane that starts late but while
    the others are still working was running at the same time as them."""
    verdict = judge(parse({
        "lane-1": marks(0, 2, 28, 30),
        "lane-3": marks(10, 12, 26, 29),
    }))
    assert verdict.concurrent


def test_pairwise_overlap_without_a_common_instant_fails():
    """The trap this catches: A overlaps B, B overlaps C, and yet there is no moment when
    all three were alive. A run like that is not what was asked for, and reporting it as
    concurrent would be the same mistake as last time in a new costume."""
    verdict = judge(parse({
        "lane-1": marks(0, 1, 8, 10),
        "lane-3": marks(5, 6, 18, 20),
        "lane-5": marks(15, 16, 28, 30),
    }))
    assert not verdict.concurrent
    assert verdict.overlap_seconds == 0.0


def test_a_short_lane_still_counts_if_it_overlapped():
    """My own first version of this test asserted the opposite, and the test was wrong
    rather than the code.

    A lane alive from 0 to 4 seconds while two others run to 30 really did share two
    seconds with them, and the rule in the goal document says exactly that: latest load
    before earliest end. There is no minimum-overlap threshold, deliberately, because any
    threshold would be a number nobody could justify. If a two second overlap is not good
    enough for a particular run, the fix is to give the lanes more work, not to move this
    goalpost.
    """
    verdict = judge(parse({
        "lane-1": marks(0, 2, 3, 4),
        "lane-3": marks(0, 2, 28, 30),
        "lane-4": marks(0, 2, 28, 31),
    }))
    assert verdict.concurrent
    assert verdict.overlap_seconds == pytest.approx(2.0)


def test_one_lane_running_entirely_after_another_fails():
    """The real serial case, which is what the previous test was reaching for."""
    verdict = judge(parse({
        "lane-1": marks(0, 1, 2, 3),
        "lane-3": marks(5, 6, 28, 30),
        "lane-4": marks(5, 6, 28, 31),
    }))
    assert not verdict.concurrent
    assert ("lane-1", "lane-3") in verdict.serial_pairs


# --- what must not be believed ---------------------------------------------


def test_a_missing_mark_is_refused():
    bad = marks(0, 1, 2, 3)
    del bad["T3_end"]
    with pytest.raises(TraceError, match="T3_end"):
        parse({"lane-1": bad, "lane-3": marks(0, 1, 2, 3)})


def test_marks_out_of_order_are_refused():
    """A lane whose end precedes its start did not read a clock; it wrote from memory
    afterwards, and its numbers cannot be used to judge anything."""
    out_of_order = marks(30, 20, 10, 0)
    with pytest.raises(TraceError, match="out of order"):
        parse({"lane-1": out_of_order, "lane-3": marks(0, 1, 2, 3)})


def test_a_naive_timestamp_is_refused():
    """Comparing lanes across an unknown UTC offset would decide concurrency by luck."""
    naive = {k: v.rstrip("Z") for k, v in marks(0, 1, 2, 3).items()}
    with pytest.raises(TraceError, match="no timezone"):
        parse({"lane-1": naive, "lane-3": marks(0, 1, 2, 3)})


def test_a_non_iso_timestamp_says_what_was_expected():
    bad = dict(marks(0, 1, 2, 3), T1_loaded="about ten past two")
    with pytest.raises(TraceError, match="toISOString"):
        parse({"lane-1": bad, "lane-3": marks(0, 1, 2, 3)})


def test_a_single_lane_cannot_demonstrate_overlap():
    with pytest.raises(TraceError, match="at least two lanes"):
        parse({"lane-1": marks(0, 1, 2, 3)})


def test_an_offset_timestamp_is_compared_correctly():
    """A lane reporting +05:30 and one reporting Z overlap or not on the instant, never
    on the wall clock."""
    verdict = judge(parse({
        "lane-1": {"T0_start": "2026-10-03T14:00:00Z",
                   "T1_loaded": "2026-10-03T14:00:02Z",
                   "T2_read": "2026-10-03T14:00:25Z",
                   "T3_end": "2026-10-03T14:00:30Z"},
        "lane-3": {"T0_start": "2026-10-03T19:30:01+05:30",
                   "T1_loaded": "2026-10-03T19:30:03+05:30",
                   "T2_read": "2026-10-03T19:30:26+05:30",
                   "T3_end": "2026-10-03T19:30:31+05:30"},
    }))
    assert verdict.concurrent


# --- the file and the list form --------------------------------------------


def test_a_list_of_lane_objects_parses_too():
    lanes = parse([
        {"lane": "lane-1", "marks": marks(0, 2, 28, 30)},
        {"lane": "lane-3", "marks": marks(1, 3, 27, 29)},
    ])
    assert [lane.lane for lane in lanes] == ["lane-1", "lane-3"]


def test_two_lanes_with_one_name_are_refused():
    with pytest.raises(TraceError, match="share a name"):
        parse([
            {"lane": "lane-1", "marks": marks(0, 2, 28, 30)},
            {"lane": "lane-1", "marks": marks(1, 3, 27, 29)},
        ])


def test_a_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(TraceError, match="no trace file"):
        load(tmp_path / "nope.json")


def test_a_trace_file_round_trips(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps({
        "lane-1": marks(0, 2, 28, 30),
        "lane-3": marks(1, 3, 27, 29),
    }), encoding="utf-8")
    assert judge(load(path)).concurrent

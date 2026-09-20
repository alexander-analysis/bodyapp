from datetime import date, timedelta

import pytest

from app.engine.progression import e1rm_trend, epley, prescribe_next, session_e1rm, stall_count, summarize
from app.engine.types import Exercise, SetRow, WorkoutRow

BENCH = Exercise(2, "Bench press", "chest", ("triceps",), increment_kg=2.5, rep_min=8, rep_max=12)
D0 = date(2026, 6, 1)


def session(idx: int, weight: float, reps: list[int], rir: list[int | None] | None = None, *, event=None, warmup=True):
    sets = []
    if warmup:
        sets.append(SetRow(BENCH.id, 0, weight * 0.6, 8, None, is_warmup=True))
    for i, r in enumerate(reps):
        sets.append(SetRow(BENCH.id, i + 1, weight, r, None if rir is None else rir[i]))
    return WorkoutRow(id=idx, performed_on=D0 + timedelta(days=3 * idx), sets=tuple(sets), health_event_id=event)


def test_top_of_range_on_all_sets_increments_load_and_resets_reps():
    """Spec 16: top of rep range on all sets increments load and resets reps."""
    p = prescribe_next(BENCH, [session(1, 60.0, [12, 12, 12], [1, 2, 2])])
    assert p.weight_kg == 62.5 and p.target_reps == 8 and p.sets == 3
    assert not p.stalled


def test_below_top_adds_a_rep_at_same_load():
    p = prescribe_next(BENCH, [session(1, 60.0, [10, 9, 9], [2, 2, 1])])
    assert p.weight_kg == 60.0 and p.target_reps == 10


def test_rir_gate_blocks_the_increment():
    p = prescribe_next(BENCH, [session(1, 60.0, [12, 12, 12], [4, 3, 4])])
    assert p.weight_kg == 60.0 and p.target_reps == 12
    assert "RIR" in p.note


def test_missing_rir_passes_the_gate():
    p = prescribe_next(BENCH, [session(1, 60.0, [12, 12, 12])])
    assert p.weight_kg == 62.5 and p.target_reps == 8


def test_one_bad_session_is_not_a_stall():
    p = prescribe_next(BENCH, [session(1, 60.0, [9, 9, 8]), session(2, 60.0, [8, 7, 6])])
    assert not p.stalled and p.stall_sessions == 1
    assert p.weight_kg == 60.0 and p.target_reps == 8  # aim for rep_min again


def test_two_consecutive_below_rep_min_flags_stall():
    p = prescribe_next(BENCH, [session(1, 60.0, [8, 7, 6]), session(2, 60.0, [8, 7, 7])])
    assert p.stalled and p.stall_sessions == 2
    assert p.weight_kg == 60.0 and p.target_reps == 8
    assert p.proposal is None


def test_three_sessions_proposes_deload_or_swap():
    sessions = [session(i, 60.0, [8, 7, 6]) for i in range(1, 4)]
    p = prescribe_next(BENCH, sessions)
    assert p.stalled and p.stall_sessions == 3
    assert p.proposal is not None and "deload to 55.0 kg" in p.proposal and "swap" in p.proposal
    assert p.weight_kg == 60.0  # a proposal, not a prescription


def test_stall_count_resets_on_a_good_session():
    sessions = [session(1, 60.0, [7, 7, 7]), session(2, 60.0, [7, 7, 6]), session(3, 60.0, [9, 9, 8])]
    assert stall_count(BENCH, summarize(BENCH.id, sessions)) == 0
    assert not prescribe_next(BENCH, sessions).stalled


def test_sessions_tagged_with_an_event_are_ignored():
    sessions = [session(1, 60.0, [12, 12, 12], [1, 1, 1]), session(2, 50.0, [8, 8, 8], [4, 4, 4], event=1)]
    p = prescribe_next(BENCH, sessions)
    assert p.weight_kg == 62.5 and p.target_reps == 8


def test_mode_pauses_progression_and_repeats_last_load():
    p = prescribe_next(BENCH, [session(1, 60.0, [12, 12, 12], [1, 1, 1])], allow_progression=False)
    assert p.weight_kg == 60.0 and p.target_reps == 12
    assert "paused" in p.note
    p = prescribe_next(BENCH, [session(1, 60.0, [12, 12, 12], [1, 1, 1])], load_cap=True)
    assert p.weight_kg == 60.0


def test_no_history():
    p = prescribe_next(BENCH, [])
    assert p.weight_kg is None and p.target_reps == 8 and p.sets == 3


def test_epley_and_e1rm_trend():
    assert epley(100, 10) == pytest.approx(133.333, abs=0.01)
    assert session_e1rm([SetRow(2, 0, 100, 5, is_warmup=True), SetRow(2, 1, 80, 10), SetRow(2, 2, 80, 8)]) == pytest.approx(80 * (1 + 10 / 30))
    trend = e1rm_trend(BENCH.id, [session(1, 60.0, [10, 10, 10]), session(2, 62.5, [8, 8, 8])])
    assert [v for _, v in trend] == [pytest.approx(80.0), pytest.approx(79.2)]


def test_warmups_never_count():
    w = WorkoutRow(1, D0, (SetRow(2, 0, 40.0, 15, None, is_warmup=True),))
    assert summarize(BENCH.id, [w]) == []
    assert prescribe_next(BENCH, [w]).weight_kg is None

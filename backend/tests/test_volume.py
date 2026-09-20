from datetime import date, timedelta

from app.engine.volume import flag, volume_flags, weekly_volume
from app.engine.types import Exercise, SetRow, WorkoutRow

EX = {
    1: Exercise(1, "Squat", "quads", ("glutes", "hamstrings")),
    2: Exercise(2, "Bench", "chest", ("triceps",)),
}
D = date(2026, 6, 7)  # as_of


def workout(wid, day, sets, event=None):
    return WorkoutRow(wid, day, tuple(sets), health_event_id=event)


def test_counts_working_sets_with_secondary_at_half():
    w = workout(1, D, [SetRow(1, 0, 60, 8, is_warmup=True)] + [SetRow(1, i, 100, 8, 2) for i in range(1, 4)]
                + [SetRow(2, i, 70, 10, 1) for i in range(1, 5)])
    vol = weekly_volume([w], EX, D)
    assert vol == {"chest": 4.0, "glutes": 1.5, "hamstrings": 1.5, "quads": 3.0, "triceps": 2.0}


def test_high_rir_sets_are_not_working_sets_but_unknown_rir_is():
    w = workout(1, D, [SetRow(2, 1, 70, 10, 4), SetRow(2, 2, 70, 10, 3), SetRow(2, 3, 70, 10, None)])
    assert weekly_volume([w], EX, D)["chest"] == 2.0


def test_seven_day_window_boundaries():
    inside = workout(1, D - timedelta(days=6), [SetRow(2, 1, 70, 10, 2)])
    outside = workout(2, D - timedelta(days=7), [SetRow(2, 1, 70, 10, 2)])
    future = workout(3, D + timedelta(days=1), [SetRow(2, 1, 70, 10, 2)])
    assert weekly_volume([inside, outside, future], EX, D) == {"chest": 1.0, "triceps": 0.5}


def test_event_tagged_sessions_are_excluded():
    sick = workout(1, D, [SetRow(2, i, 50, 8, 2) for i in range(1, 4)], event=1)
    well = workout(2, D - timedelta(days=1), [SetRow(2, i, 70, 8, 2) for i in range(1, 3)])
    assert weekly_volume([sick, well], EX, D)["chest"] == 2.0


def test_flags_and_scaled_bands():
    assert flag(9.5) == "low" and flag(10) == "ok" and flag(20) == "ok" and flag(20.5) == "high"
    assert volume_flags({"chest": 8.0, "back": 12.0, "legs": 22.0}) == {"chest": "low", "back": "ok", "legs": "high"}
    # exam mode: prescribed volume cut by a third, so 8 sets is fine
    assert volume_flags({"chest": 8.0}, cap=2 / 3) == {"chest": "ok"}


def test_synthetic_week_flags(synthetic):
    vol = weekly_volume(synthetic.workouts, synthetic.exercises, synthetic.day(6))
    # bench x2, squat x2; back = row x2 + pull-up + RDL secondary (0.5 x 6); triceps = 0.5 x (bench 6 + ohp 3)
    assert vol["chest"] == 6.0 and vol["quads"] == 6.0 and vol["back"] == 12.0 and vol["triceps"] == 4.5
    flags = volume_flags(vol)
    assert set(flags.values()) <= {"low", "ok", "high"}

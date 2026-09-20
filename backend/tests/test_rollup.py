from datetime import date, timedelta

import pytest

from app.engine.rollup import is_logged_complete, rollup_day, weekly_adherence
from app.engine.types import DayRow, FoodEntryRow, Target

D = date(2026, 6, 10)
TARGET = Target(D, 2200, 170, 80, "cut", "t", "user", steps=9000)


def entry(kcal, meal, conf=0.9, protein=30.0):
    return FoodEntryRow(D, kcal, protein, kcal * 0.4 / 4, kcal * 0.3 / 9, conf, fibre_g=5.0, meal=meal)


def test_rollup_sums_and_entry_weighted_confidence():
    entries = [entry(500, "breakfast", 0.95), entry(800, "lunch", 0.6), entry(900, "dinner", 0.35)]
    row = rollup_day(D, entries, target_kcal=2200, steps=8500)
    assert row.kcal == 2200.0 and row.protein_g == 90.0 and row.fibre_g == 15.0
    assert row.mean_confidence == pytest.approx((0.95 + 0.6 + 0.35) / 3, abs=1e-3)
    assert row.logged_complete and row.steps == 8500 and row.health_event_id is None


def test_logged_complete_heuristic():
    three_meals = [entry(500, "breakfast"), entry(800, "lunch"), entry(900, "dinner")]
    assert is_logged_complete(three_meals, 2200)
    assert not is_logged_complete(three_meals[:2], 2200)  # fewer than 3 entries
    one_meal = [entry(300, "snack"), entry(300, "snack"), entry(300, "snack")]
    assert not is_logged_complete(one_meal, 2200)  # only one distinct meal
    too_little = [entry(200, "breakfast"), entry(200, "lunch"), entry(200, "dinner")]
    assert not is_logged_complete(too_little, 2200)  # < 50% of target
    assert is_logged_complete(too_little, None)  # no target known: structure alone decides
    assert is_logged_complete([], 2200, user_marked=True)
    assert not is_logged_complete(three_meals, 2200, user_marked=False)


def test_empty_day_rolls_up_to_an_unlogged_row():
    row = rollup_day(D, [], steps=4000)
    assert row.is_unlogged and not row.logged_complete and row.mean_confidence is None


def test_rollup_carries_the_event_tag():
    row = rollup_day(D, [entry(500, "lunch")], health_event_id=7)
    assert row.health_event_id == 7


def test_weekly_adherence_counts_clean_complete_days_only():
    days = [
        DayRow(D, kcal=2150.0, protein_g=175.0, steps=9500, logged_complete=True, mean_confidence=0.9),
        DayRow(D + timedelta(days=1), kcal=2600.0, protein_g=150.0, steps=7000, logged_complete=True, mean_confidence=0.6),
        DayRow(D + timedelta(days=2), kcal=2300.0, protein_g=180.0, steps=12000, logged_complete=True, mean_confidence=0.9),
        DayRow(D + timedelta(days=3), kcal=900.0, protein_g=60.0, steps=1000, logged_complete=False),  # incomplete
        DayRow(D + timedelta(days=4), kcal=1500.0, protein_g=100.0, steps=2000, logged_complete=True, health_event_id=1),  # sick
    ]
    a = weekly_adherence(days, TARGET)
    assert a.days_considered == 3
    assert a.kcal_hit_pct == pytest.approx(66.7, abs=0.1)
    assert a.protein_hit_pct == pytest.approx(66.7, abs=0.1)
    assert a.steps_hit_pct == pytest.approx(66.7, abs=0.1)
    assert a.mean_kcal == pytest.approx(2350.0)
    assert a.mean_confidence == pytest.approx(0.8, abs=1e-3)


def test_weekly_adherence_with_nothing():
    a = weekly_adherence([], TARGET)
    assert a.days_considered == 0 and a.kcal_hit_pct is None

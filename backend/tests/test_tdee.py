from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.engine import guards
from app.engine.tdee import (
    ACTIVITY_FACTOR,
    MIN_CONFIDENCE_FOR_BLEND,
    adaptive_tdee,
    blend,
    estimate_tdee,
    formula_tdee,
    logging_confidence_factor,
    mifflin_st_jeor,
)
from app.engine.trend import trend_weight
from app.engine.types import DayRow, UserProfile


def test_mifflin_st_jeor_known_values():
    assert mifflin_st_jeor(80, 180, 24, "m") == pytest.approx(1810.0)
    assert mifflin_st_jeor(60, 165, 30, "f") == pytest.approx(10 * 60 + 6.25 * 165 - 150 - 161)


def test_formula_uses_activity_factor_and_age_on_date():
    profile = UserProfile("m", date(2002, 3, 15), 180.0)
    on = date(2026, 6, 1)  # 24 years old
    assert formula_tdee(profile, 80.0, on) == pytest.approx(1810.0 * ACTIVITY_FACTOR)
    assert profile.age_on(date(2026, 3, 14)) == 23


def test_adaptive_formula_signs():
    # lost 1 kg over 20 days on 2200/day -> maintenance is 2200 + 7700/20
    assert adaptive_tdee(2200, 80.0, 79.0, 20) == pytest.approx(2585.0)
    # gained 1 kg -> lower
    assert adaptive_tdee(2200, 79.0, 80.0, 20) == pytest.approx(1815.0)
    with pytest.raises(ValueError):
        adaptive_tdee(2200, 80, 79, 0)


def test_blend_weights_and_formula_only_below_half():
    assert blend(3000.0, 2000.0, 0.75) == (2750.0, "adaptive")
    assert blend(3000.0, 2000.0, 1.0) == (3000.0, "adaptive")
    assert blend(3000.0, 2000.0, 0.49) == (2000.0, "formula")
    assert blend(None, 2000.0, 1.0) == (2000.0, "formula")


def test_logging_confidence_factor():
    assert logging_confidence_factor(None) == 1.0
    assert logging_confidence_factor(0.9) == 1.0
    assert logging_confidence_factor(0.6) == 1.0
    assert logging_confidence_factor(0.3) == pytest.approx(0.5)


def test_adaptive_tdee_exact_on_known_linear_series(linear):
    """Spec 16: a known synthetic series produces the expected kcal within 1%."""
    series = trend_weight(linear.weights)
    est = estimate_tdee(profile=linear.profile, days=linear.days, trend=series, as_of=linear.day(50))
    assert est.method == "adaptive"
    assert est.confidence == 1.0
    assert est.adaptive_kcal == pytest.approx(linear.true_tdee, rel=0.01)
    assert est.tdee_kcal == pytest.approx(linear.true_tdee, rel=0.01)
    assert est.complete_days == 21 and est.clean_days == 21


def test_adaptive_tdee_on_noisy_fixture_is_close(synthetic):
    series = trend_weight(synthetic.weights)
    est = estimate_tdee(profile=synthetic.profile, days=synthetic.days, trend=series, as_of=synthetic.day(28))
    assert est.method == "adaptive"
    assert est.adaptive_kcal == pytest.approx(synthetic.true_tdee, rel=0.05)


def test_switches_from_formula_to_adaptive_as_data_accumulates(linear):
    series = trend_weight(linear.weights)
    early = estimate_tdee(profile=linear.profile, days=linear.days[:8], trend=series[:8], as_of=linear.day(7))
    assert early.method == "formula"
    assert early.tdee_kcal == early.formula_kcal
    later = estimate_tdee(profile=linear.profile, days=linear.days, trend=series, as_of=linear.day(21))
    assert later.method == "adaptive"
    assert later.is_established


def test_below_half_confidence_uses_formula_alone(linear):
    """Spec 16: below c = 0.5 the formula estimate is used alone."""
    series = trend_weight(linear.weights)
    as_of = linear.day(50)
    window = [d for d in linear.days if as_of - timedelta(days=20) <= d.day <= as_of]
    # only 9 of 21 days logged-complete -> c = 0.43
    days = [replace(d, logged_complete=(i < 9)) for i, d in enumerate(window)]
    est = estimate_tdee(profile=linear.profile, days=days, trend=series, as_of=as_of)
    assert est.complete_days == 9
    assert est.confidence == pytest.approx(9 / 21, abs=1e-3)
    assert est.confidence < MIN_CONFIDENCE_FOR_BLEND
    assert est.method == "formula"
    assert est.tdee_kcal == est.formula_kcal
    assert est.adaptive_kcal is not None  # still computed and reported
    assert any("formula estimate alone" in n for n in est.notes)


def test_low_logging_confidence_downweights_adaptive(linear):
    series = trend_weight(linear.weights)
    as_of = linear.day(50)
    days = [replace(d, mean_confidence=0.3) for d in linear.days]
    est = estimate_tdee(profile=linear.profile, days=days, trend=series, as_of=as_of)
    assert est.confidence == pytest.approx(0.5)
    assert any("estimates" in n for n in est.notes)
    blended, _ = blend(est.adaptive_kcal, est.formula_kcal, 0.5)
    assert est.tdee_kcal == pytest.approx(blended, abs=0.1)


def test_logging_gap_suppresses_adaptive_recompute(linear):
    series = trend_weight(linear.weights)
    as_of = linear.day(50)
    gap = {as_of - timedelta(days=k) for k in range(5, 5 + guards.MAX_UNLOGGED_GAP_DAYS)}
    days = [replace(d, kcal=None, logged_complete=False) if d.day in gap else d for d in linear.days]
    est = estimate_tdee(profile=linear.profile, days=days, trend=series, as_of=as_of)
    assert est.method == "formula"
    assert est.adaptive_kcal is None
    assert est.confidence == 0.0
    assert any(guards.RAIL_LOGGING_GAP in n for n in est.notes)


def test_incomplete_days_do_not_drag_the_intake_mean_down(linear):
    """An incompletely logged day is unknown, not small."""
    series = trend_weight(linear.weights)
    as_of = linear.day(50)
    days = [
        replace(d, kcal=400.0, logged_complete=False) if (as_of - d.day).days in (2, 5, 9) else d
        for d in linear.days
    ]
    est = estimate_tdee(profile=linear.profile, days=days, trend=series, as_of=as_of)
    assert est.complete_days == 18
    assert est.adaptive_kcal == pytest.approx(linear.true_tdee, rel=0.01)


def test_illness_rows_excluded_from_tdee(synthetic):
    """Spec 16: a 4-day event removes those rows from the TDEE computation."""
    series = trend_weight(synthetic.weights)
    as_of = synthetic.day(40)  # window 20..40 contains the illness on 30..33
    with_rows = estimate_tdee(profile=synthetic.profile, days=synthetic.days, trend=series, as_of=as_of,
                              events=synthetic.events)
    without = estimate_tdee(
        profile=synthetic.profile, days=[d for d in synthetic.days if d.health_event_id is None], trend=series,
        as_of=as_of, events=synthetic.events,
    )
    assert with_rows.clean_days == 17
    assert with_rows.adaptive_kcal == without.adaptive_kcal
    assert with_rows.tdee_kcal == without.tdee_kcal
    # and the sick days' 1500 kcal never entered the mean intake
    with_rows_fake = estimate_tdee(
        profile=synthetic.profile,
        days=[replace(d, kcal=100.0) if d.health_event_id else d for d in synthetic.days],
        trend=series, as_of=as_of, events=synthetic.events,
    )
    assert with_rows_fake.adaptive_kcal == with_rows.adaptive_kcal


def test_no_weight_data_gives_no_formula():
    profile = UserProfile("m", date(2002, 3, 15), 180.0)
    est = estimate_tdee(profile=profile, days=[], trend=[], as_of=date(2026, 6, 1))
    assert est.method == "formula" and est.tdee_kcal == 0.0
    assert any("no weight data" in n for n in est.notes)


def test_day_row_unlogged_property():
    assert DayRow(date(2026, 6, 1)).is_unlogged
    assert DayRow(date(2026, 6, 1), kcal=0.0).is_unlogged
    assert not DayRow(date(2026, 6, 1), kcal=1.0).is_unlogged

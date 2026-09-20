from datetime import date, timedelta
from statistics import fmean

import pytest

from app.engine.trend import ALPHA, RETURN_RAMP_READINGS, clean_trend, rate_pct_per_week, trend_weight
from app.engine.types import WeightPoint

D0 = date(2026, 6, 1)


def pts(weights, start=D0, skip=(), event_days=None, event_id=1):
    event_days = set(event_days or ())
    out = []
    for i, w in enumerate(weights):
        if i in skip:
            continue
        out.append(WeightPoint(start + timedelta(days=i), w, event_id if i in event_days else None))
    return out


def test_first_seven_points_display_the_seed_mean():
    weights = [80.0, 81.0, 79.5, 80.5, 80.2, 79.8, 80.6, 79.0, 78.5, 78.0]
    series = trend_weight(pts(weights))
    seed = fmean(weights[:7])
    for p in series[:7]:
        assert p.trend_kg == pytest.approx(seed)
    assert series[7].trend_kg == pytest.approx(ALPHA * weights[7] + (1 - ALPHA) * seed)
    assert series[8].trend_kg == pytest.approx(ALPHA * weights[8] + (1 - ALPHA) * series[7].trend_kg)


def test_fewer_than_seven_points_seed_with_what_exists():
    series = trend_weight(pts([80.0, 82.0, 81.0]))
    assert [p.trend_kg for p in series] == pytest.approx([81.0, 81.0, 81.0])


def test_missing_days_are_skipped_not_interpolated():
    weights = [80.0, 80.0, 80.0, 80.0, 80.0, 80.0, 80.0, 79.0, 78.0, 77.0, 76.0, 75.0]
    full = trend_weight(pts(weights))
    gapped = trend_weight(pts(weights, skip=(8, 9)))  # two missing weigh-ins

    # The gapped series is exactly the recurrence over the points that exist.
    t = 80.0
    expected = []
    for i, w in enumerate(weights):
        if i in (8, 9):
            continue
        if i >= 7:
            t = ALPHA * w + (1 - ALPHA) * t
        expected.append(t)
    assert [p.trend_kg for p in gapped] == pytest.approx(expected)
    assert len(gapped) == len(weights) - 2
    # and it differs from a series that had those days — nothing was filled in.
    assert gapped[-1].trend_kg != pytest.approx(full[-1].trend_kg)


def test_series_is_sorted_by_day_regardless_of_input_order():
    points = pts([80.0, 79.0, 78.0, 77.0, 76.0, 75.0, 74.0, 73.0])
    assert trend_weight(list(reversed(points))) == trend_weight(points)


def test_excluded_points_never_move_the_trend():
    weights = [80.0] * 10 + [77.0, 76.5, 76.0] + [80.0] * 10
    sick = range(10, 13)
    series = trend_weight(pts(weights, event_days=sick))
    clean = clean_trend(series)
    assert len(clean) == 20
    for p in clean:
        assert p.trend_kg == pytest.approx(80.0)
    flagged = [p for p in series if p.excluded]
    assert len(flagged) == 3
    for p in flagged:  # display interpolation between the two 80.0 anchors
        assert p.trend_kg == pytest.approx(80.0)
        assert p.raw_kg < 78.0


def test_excluded_values_do_not_influence_clean_values_at_all():
    weights = [80.0 - 0.05 * i for i in range(30)]
    sick = range(12, 16)
    a = trend_weight(pts(weights, event_days=sick))
    perturbed = [w - 10.0 if i in sick else w for i, w in enumerate(weights)]
    b = trend_weight(pts(perturbed, event_days=sick))
    assert [p.trend_kg for p in clean_trend(a)] == pytest.approx([p.trend_kg for p in clean_trend(b)])


def test_post_event_readings_are_weighted_in_over_five_readings():
    weights = [80.0] * 10 + [77.0] * 3 + [82.0] * 8  # a +2 kg "rebound" after the gap
    series = trend_weight(pts(weights, event_days=range(10, 13)))
    clean = clean_trend(series)
    t = 80.0
    for k in range(1, RETURN_RAMP_READINGS + 1):
        a = ALPHA * k / RETURN_RAMP_READINGS
        t = a * 82.0 + (1 - a) * t
        assert clean[9 + k].trend_kg == pytest.approx(t)
    # after the ramp the normal alpha applies again
    t = ALPHA * 82.0 + (1 - ALPHA) * t
    assert clean[10 + RETURN_RAMP_READINGS].trend_kg == pytest.approx(t)


def test_interpolation_holds_last_anchor_when_event_is_still_open():
    weights = [80.0] * 8 + [77.0, 76.0]
    series = trend_weight(pts(weights, event_days=(8, 9)))
    assert series[-1].excluded and series[-1].trend_kg == pytest.approx(80.0)


def test_all_excluded_returns_raw_as_display_only():
    series = trend_weight(pts([80.0, 79.0], event_days=(0, 1)))
    assert all(p.excluded for p in series)
    assert clean_trend(series) == []


def test_rate_pct_per_week():
    a = trend_weight(pts([80.0] * 7))[0]
    b = type(a)(a.day + timedelta(days=14), 79.0, 79.2)
    # (80 - 79.2) / 79.2 * 100 * 7/14
    assert rate_pct_per_week(a, b) == pytest.approx(0.8 / 79.2 * 100 / 2)
    assert rate_pct_per_week(a, a) is None


def test_synthetic_rebound_is_not_a_spike(synthetic):
    """The rehydration after the illness must not show up as a gain (spec 8.2/8.3)."""
    series = trend_weight(synthetic.weights)
    by_day = {p.day: p for p in series}
    before = by_day[synthetic.day(29)]
    after = by_day[synthetic.day(40)]
    assert not before.excluded and not after.excluded
    assert after.trend_kg < before.trend_kg  # still descending, no rebound bump
    assert before.trend_kg - after.trend_kg < 1.2  # and not a crash either
    for d in range(30, 34):
        assert by_day[synthetic.day(d)].excluded

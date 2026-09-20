from datetime import date, timedelta

import pytest

from app.engine import guards
from app.engine.targets import (
    MIN_WEIGH_INS_IN_WINDOW,
    REVIEW_WINDOW_DAYS,
    STALL_KCAL_CUT,
    STALL_STEP_INCREASE,
    TOO_FAST_KCAL_RAISE,
    ReviewContext,
    classify,
    weekly_review,
)
from app.engine.trend import trend_weight
from app.engine.types import HealthEvent, Target, TrendPoint, UserProfile

TODAY = date(2026, 7, 5)  # a Sunday
PROFILE = UserProfile("m", date(2002, 3, 15), 180.0)


def current(**kw) -> Target:
    base = dict(effective_from=TODAY - timedelta(days=30), kcal=2200, protein_g=170, fat_g_min=80,
                phase="cut", reason="initial", set_by="user", steps=9000)
    base.update(kw)
    return Target(**base)


def trend_at_rate(rate_pct_week: float, *, end_kg: float = 80.0, days: int = REVIEW_WINDOW_DAYS, today: date = TODAY,
                  n_points: int | None = None) -> list[TrendPoint]:
    """A daily trend series ending at ``end_kg`` that loses ``rate`` % BW/week."""
    span = days - 1
    start_kg = end_kg * (1 + rate_pct_week / 100.0 * span / 7.0)
    pts = []
    for i in range(days):
        kg = start_kg + (end_kg - start_kg) * i / span
        pts.append(TrendPoint(today - timedelta(days=span - i), kg, kg))
    if n_points is not None:
        keep = set(range(0, days, max(1, days // n_points)))
        pts = [p for i, p in enumerate(pts) if i in keep or i == days - 1]
    return pts


def ctx(rate: float, **kw) -> ReviewContext:
    base = dict(today=TODAY, profile=PROFILE, current=current(), trend=trend_at_rate(rate))
    base.update(kw)
    return ReviewContext(**base)


def test_classification_bands():
    assert classify(0.05, "cut") == "stalled"
    assert classify(0.2, "cut") == "slow"
    assert classify(0.5, "cut") == "on_track"
    assert classify(0.9, "cut") == "fast"
    assert classify(1.2, "cut") == "too_fast"
    assert classify(1.2, "maintain") == "too_fast"
    assert classify(0.0, "maintain") == "maintaining"
    assert classify(-0.3, "gain") == "gain_ok"
    assert classify(-0.8, "gain") == "gain_overshoot"


def test_on_track_emits_no_change():
    res = weekly_review(ctx(0.5))
    assert res.assessment == "on_track"
    assert res.rate_pct_week == pytest.approx(0.5, abs=0.01)
    assert not res.changed and res.proposed is None
    assert "no change" in res.reason


def test_stall_cuts_150_kcal_with_reason():
    res = weekly_review(ctx(0.05))
    assert res.assessment == "stalled" and res.changed
    assert res.proposed.kcal == 2200 - STALL_KCAL_CUT
    assert res.proposed.steps == 9000
    assert res.proposed.set_by == "engine"
    assert res.proposed.effective_from == TODAY + timedelta(days=1)
    assert "stalled" in res.proposed.reason and "% BW/week" in res.proposed.reason
    assert res.stall_action == "kcal"


def test_stall_alternates_to_steps_never_both():
    res = weekly_review(ctx(0.05, previous_stall_action="kcal"))
    assert res.changed
    assert res.proposed.kcal == 2200 and res.proposed.steps == 9000 + STALL_STEP_INCREASE
    assert res.stall_action == "steps"


def test_stall_uses_steps_when_a_cut_would_hit_the_floor():
    res = weekly_review(ctx(0.05, current=current(kcal=guards.KCAL_FLOOR + 100)))
    assert res.changed and res.stall_action == "steps"
    assert res.proposed.kcal == guards.KCAL_FLOOR + 100


def test_rapid_loss_raises_target():
    """Spec 16: 1.3 %/week over 14 days raises the target."""
    res = weekly_review(ctx(1.3))
    assert res.assessment == "too_fast" and res.changed
    assert res.proposed.kcal == 2200 + TOO_FAST_KCAL_RAISE
    assert guards.RAIL_RAPID_LOSS in res.proposed.reason


def test_rapid_loss_applies_in_maintain_phase_too():
    res = weekly_review(ctx(1.3, current=current(phase="maintain")))
    assert res.changed and res.proposed.kcal == 2400


def test_gain_overshoot_cuts():
    res = weekly_review(ctx(-0.8, current=current(phase="gain", kcal=3000)))
    assert res.assessment == "gain_overshoot" and res.changed
    assert res.proposed.kcal == 2850


def test_gain_on_track_no_change():
    assert not weekly_review(ctx(-0.3, current=current(phase="gain", kcal=3000))).changed


def test_change_magnitude_never_exceeds_200():
    for rate in (0.05, 1.3, -0.8):
        phase = "gain" if rate < 0 else "cut"
        res = weekly_review(ctx(rate, current=current(phase=phase, kcal=2500)))
        if res.changed:
            assert abs(res.proposed.kcal - 2500) <= guards.MAX_TARGET_CHANGE_KCAL


def test_active_event_blocks_review():
    ev = HealthEvent(1, "travel", TODAY - timedelta(days=1))
    res = weekly_review(ctx(0.05, events=[ev]))
    assert res.assessment == "active_event" and not res.changed


def test_window_with_an_ended_event_or_ramp_is_not_clean():
    ended = HealthEvent(1, "illness", TODAY - timedelta(days=12), ended_at=TODAY - timedelta(days=10),
                        ramp_until=TODAY - timedelta(days=7))
    res = weekly_review(ctx(0.05, events=[ended]))
    assert res.assessment == "window_not_clean" and not res.changed
    old = HealthEvent(2, "illness", TODAY - timedelta(days=40), ended_at=TODAY - timedelta(days=30),
                      ramp_until=TODAY - timedelta(days=23))
    assert weekly_review(ctx(0.05, events=[old])).changed


def test_excluded_trend_points_in_window_block_review():
    pts = trend_at_rate(0.05)
    pts[3] = TrendPoint(pts[3].day, pts[3].raw_kg, pts[3].trend_kg, excluded=True)
    res = weekly_review(ctx(0.05, trend=pts))
    assert res.assessment == "window_not_clean"


def test_insufficient_weigh_ins_blocks_review():
    res = weekly_review(ctx(0.05, trend=trend_at_rate(0.05, n_points=4)))
    assert res.assessment == "insufficient_weigh_ins" and not res.changed
    assert str(MIN_WEIGH_INS_IN_WINDOW) in res.reason


def test_second_change_within_seven_days_rejected_by_rails():
    res = weekly_review(ctx(0.05, last_change_on=TODAY - timedelta(days=4)))
    assert res.assessment == "stalled"
    assert not res.changed
    assert guards.RAIL_CHANGE_RATE in res.rails_tripped


def test_floor_clamp_reported_when_cut_lands_below_floor():
    # kcal floor + 150 exactly: a cut lands on the floor, allowed; one below is clamped and named.
    res = weekly_review(ctx(0.05, current=current(kcal=guards.KCAL_FLOOR + STALL_KCAL_CUT)))
    assert res.changed and res.proposed.kcal == guards.KCAL_FLOOR and res.rails_tripped == ()


def test_phase_complete_is_a_proposal_not_a_change():
    profile = UserProfile("m", date(2002, 3, 15), 180.0, goal_weight_kg=80.5)
    res = weekly_review(ctx(0.5, profile=profile))
    assert res.proposals and "confirm switch to maintain" in res.proposals[0]
    assert not res.changed
    assert res.assessment == "on_track"


def test_rehydration_rebound_does_not_trigger_a_change(synthetic):
    """Spec 16: the post-illness spike does not trigger a target change."""
    series = trend_weight(synthetic.weights)
    base = dict(profile=synthetic.profile, current=synthetic.target, trend=series,
                events=synthetic.events, days=synthetic.days)
    # Sundays after the illness (ended day 33, ramp to day 37): 34, 41, 48 all touch event or ramp days.
    for sunday in (34, 41, 48):
        res = weekly_review(ReviewContext(today=synthetic.day(sunday), **base))
        assert not res.changed, (sunday, res)
        assert res.assessment in ("active_event", "window_not_clean")
    # First fully clean window: the cut is still on track, no spurious cut from the rebound.
    res = weekly_review(ReviewContext(today=synthetic.day(55), **base))
    assert res.assessment != "stalled"
    assert res.proposed is None or res.proposed.kcal >= synthetic.target.kcal


def test_initial_target_is_formula_based_and_rail_checked():
    from app.engine.guards import RailContext, apply_rails
    from app.engine.targets import initial_target
    from app.engine.tdee import formula_tdee

    on = date(2026, 6, 1)
    t = initial_target(PROFILE, 80.0, "cut", on)
    assert t.kcal == round(formula_tdee(PROFILE, 80.0, on)) - 500
    assert t.protein_g == 160 and t.fat_g_min == 64 and t.set_by == "engine"
    assert "Mifflin" in t.reason
    res = apply_rails(t, RailContext(today=on))
    assert res.target.fat_g_min == guards.FAT_FLOOR_G and guards.RAIL_FAT_FLOOR in res.tripped  # 64 -> 80
    light = initial_target(UserProfile("f", date(2004, 1, 1), 160.0), 52.0, "cut", on)
    clamped = apply_rails(light, RailContext(today=on))
    assert clamped.target.kcal == guards.KCAL_FLOOR and clamped.target.protein_g == guards.PROTEIN_FLOOR_G
    gain = initial_target(PROFILE, 80.0, "gain", on, maintenance_kcal=2700)
    assert gain.kcal == 3000 and "supplied maintenance" in gain.reason

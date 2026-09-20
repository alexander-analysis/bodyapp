from datetime import date, timedelta

from app.engine import guards
from app.engine.guards import RailContext, apply_rails
from app.engine.types import DayRow, HealthEvent, Target

TODAY = date(2026, 7, 5)


def target(**kw) -> Target:
    base = dict(effective_from=TODAY, kcal=2200, protein_g=170, fat_g_min=80, phase="cut", reason="test", set_by="engine")
    base.update(kw)
    return Target(**base)


def test_constants_are_the_spec_values():
    """Changing a guardrail is a deliberate act: update this test with the code."""
    assert guards.KCAL_FLOOR == 1700
    assert guards.PROTEIN_FLOOR_G == 150
    assert guards.FAT_FLOOR_G == 80
    assert guards.MAX_TARGET_CHANGE_KCAL == 200
    assert guards.MIN_DAYS_BETWEEN_CHANGES == 7
    assert guards.MAX_LOSS_RATE_PCT_WEEK == 1.0
    assert guards.MAX_SICK_DAYS_BEFORE_REFERRAL == 7
    assert guards.MAX_GI_DAYS_BEFORE_REFERRAL == 3
    assert guards.MAX_UNLOGGED_GAP_DAYS == 4


def test_calorie_floor_clamps_and_writes_reason():
    """Spec 16: a computed target of 1500 clamps to 1700 and writes a reason."""
    res = apply_rails(target(kcal=1500), RailContext(today=TODAY))
    assert not res.rejected
    assert res.target.kcal == 1700
    assert guards.RAIL_KCAL_FLOOR in res.tripped
    assert guards.RAIL_KCAL_FLOOR in res.target.reason
    assert "1500" in res.target.reason and "1700" in res.target.reason
    assert res.clamped


def test_protein_and_fat_floors():
    res = apply_rails(target(protein_g=120, fat_g_min=50), RailContext(today=TODAY))
    assert res.target.protein_g == 150 and res.target.fat_g_min == 80
    assert guards.RAIL_PROTEIN_FLOOR in res.tripped and guards.RAIL_FAT_FLOOR in res.tripped


def test_within_bounds_passes_untouched():
    t = target()
    res = apply_rails(t, RailContext(today=TODAY, previous=target(kcal=2350)))
    assert res.target == t and res.tripped == () and not res.clamped


def test_second_engine_change_within_seven_days_is_rejected():
    """Spec 16: a second change within 7 days is rejected."""
    prev = target(kcal=2350, effective_from=TODAY - timedelta(days=3))
    res = apply_rails(target(), RailContext(today=TODAY, previous=prev, last_change_on=prev.effective_from))
    assert res.rejected and res.target is None
    assert res.tripped == (guards.RAIL_CHANGE_RATE,)
    assert guards.RAIL_CHANGE_RATE in res.reason

    ok = apply_rails(
        target(effective_from=TODAY + timedelta(days=4)),
        RailContext(today=TODAY, previous=prev, last_change_on=prev.effective_from),
    )
    assert not ok.rejected


def test_user_override_is_not_rate_limited_but_floors_still_apply():
    prev = target(kcal=2350, effective_from=TODAY - timedelta(days=1))
    res = apply_rails(
        target(kcal=1600, set_by="user"),
        RailContext(today=TODAY, previous=prev, last_change_on=prev.effective_from),
    )
    assert not res.rejected
    assert res.target.kcal == 1700 and guards.RAIL_KCAL_FLOOR in res.tripped


def test_noop_engine_change_is_not_written():
    prev = target(effective_from=TODAY - timedelta(days=30))
    res = apply_rails(target(), RailContext(today=TODAY, previous=prev, last_change_on=prev.effective_from))
    assert res.rejected and "no-op" in res.reason


def test_active_illness_forces_maintenance_and_no_deficit():
    ev = HealthEvent(1, "illness", TODAY - timedelta(days=1), severity="mild")
    res = apply_rails(target(kcal=2000, phase="cut"), RailContext(today=TODAY, events=[ev], maintenance_kcal=2600))
    assert res.target.phase == "maintain"
    assert res.target.kcal == 2600
    assert guards.RAIL_ILLNESS_NO_DEFICIT in res.tripped
    assert guards.RAIL_ILLNESS_NO_DEFICIT in res.target.reason


def test_return_ramp_also_forbids_a_deficit():
    ev = HealthEvent(1, "illness", TODAY - timedelta(days=6), severity="moderate",
                     ended_at=TODAY - timedelta(days=2), ramp_until=TODAY + timedelta(days=2))
    assert ev.in_ramp_on(TODAY)
    res = apply_rails(target(kcal=2000, phase="cut"), RailContext(today=TODAY, events=[ev], maintenance_kcal=2600))
    assert res.target.phase == "maintain" and res.target.kcal == 2600


def test_illness_rule_keeps_higher_kcal_if_already_above_maintenance():
    ev = HealthEvent(1, "illness", TODAY, severity="mild")
    res = apply_rails(target(kcal=2800, phase="gain"), RailContext(today=TODAY, events=[ev], maintenance_kcal=2600))
    assert res.target.phase == "maintain" and res.target.kcal == 2800


def test_non_illness_events_do_not_force_maintenance():
    ev = HealthEvent(1, "travel", TODAY)
    res = apply_rails(target(kcal=2000, phase="cut"), RailContext(today=TODAY, events=[ev], maintenance_kcal=2600))
    assert res.target.phase == "cut" and res.target.kcal == 2000


def test_fever_locks_training_regardless_of_severity():
    mild_fever = HealthEvent(1, "illness", TODAY, severity="mild", fever_flag=True)
    assert guards.training_locked([mild_fever], TODAY)
    assert not guards.training_locked([HealthEvent(2, "illness", TODAY, severity="moderate")], TODAY)
    assert not guards.training_locked([mild_fever], TODAY - timedelta(days=1))  # not active yet


def test_referral_thresholds():
    gi = HealthEvent(1, "illness", TODAY - timedelta(days=3), severity="gi")  # day 4 of GI
    assert guards.referral_due([gi], TODAY) == [gi]
    assert guards.referral_due([gi], TODAY - timedelta(days=1)) == []  # day 3: not yet
    moderate = HealthEvent(2, "illness", TODAY - timedelta(days=7), severity="moderate")  # day 8
    assert guards.referral_due([moderate], TODAY) == [moderate]
    assert guards.referral_due([moderate], TODAY - timedelta(days=1)) == []
    mild_long = HealthEvent(3, "illness", TODAY - timedelta(days=9), severity="mild")
    assert guards.referral_due([mild_long], TODAY) == [mild_long]
    ended = HealthEvent(4, "illness", TODAY - timedelta(days=20), severity="gi", ended_at=TODAY - timedelta(days=10))
    assert guards.referral_due([ended], TODAY) == []


def test_rapid_loss_predicate():
    assert guards.rapid_loss(1.3)
    assert not guards.rapid_loss(1.0)
    assert not guards.rapid_loss(None)


def test_logging_gap_detection():
    start = TODAY - timedelta(days=9)
    logged = [DayRow(start + timedelta(days=i), kcal=2000.0) for i in range(10)]
    assert not guards.has_logging_gap(logged, start, TODAY)

    three_missing = [d for d in logged if (d.day - start).days not in (3, 4, 5)]
    assert not guards.has_logging_gap(three_missing, start, TODAY)

    four_missing = [d for d in logged if (d.day - start).days not in (3, 4, 5, 6)]
    assert guards.has_logging_gap(four_missing, start, TODAY)

    zero_kcal = [DayRow(d.day, kcal=0.0) if (d.day - start).days in (2, 3, 4, 5) else d for d in logged]
    assert guards.has_logging_gap(zero_kcal, start, TODAY)


def test_logging_gap_ignores_event_days():
    start = TODAY - timedelta(days=9)
    ev = HealthEvent(1, "illness", start + timedelta(days=3), ended_at=start + timedelta(days=6))
    without_rows = [DayRow(start + timedelta(days=i), kcal=2000.0) for i in range(10) if i not in (3, 4, 5, 6)]
    assert guards.has_logging_gap(without_rows, start, TODAY)  # no event known: it is a gap
    assert not guards.has_logging_gap(without_rows, start, TODAY, events=[ev])  # event known: transparent
    tagged = [DayRow(start + timedelta(days=i), kcal=None if i in (3, 4, 5, 6) else 2000.0,
                     health_event_id=1 if i in (3, 4, 5, 6) else None) for i in range(10)]
    assert not guards.has_logging_gap(tagged, start, TODAY)


def test_scope_statement_exists_for_the_ui():
    assert "not a clinician" in guards.SCOPE_STATEMENT


def test_logging_gap_ignores_days_before_the_first_logged_day():
    start = TODAY - timedelta(days=20)
    # the user started logging 3 days ago: the 17 empty days before are not a gap
    rows = [DayRow(TODAY - timedelta(days=i), kcal=2000.0) for i in range(3)]
    assert not guards.has_logging_gap(rows, start, TODAY)
    assert not guards.has_logging_gap([], start, TODAY)
    # but a real 4-day hole after they started is
    rows = [DayRow(TODAY - timedelta(days=i), kcal=2000.0) for i in (0, 5, 6)]
    assert guards.has_logging_gap(rows, start, TODAY)

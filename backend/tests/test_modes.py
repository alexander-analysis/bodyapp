from dataclasses import fields
from datetime import date, timedelta

import pytest

from app.engine.modes import (
    EXAM,
    INJURY,
    NORMAL,
    RAMP,
    SICK_GI,
    SICK_MILD,
    SICK_MODERATE,
    TRAVEL,
    ModeRules,
    affected_groups,
    conservative_value,
    effective_severity,
    exercise_is_affected,
    most_conservative,
    ramp_until,
    resolve,
    rules_for,
)
from app.engine.types import Exercise, HealthEvent

TODAY = date(2026, 7, 5)
ALL = [NORMAL, SICK_MILD, SICK_MODERATE, SICK_GI, EXAM, TRAVEL, INJURY, RAMP]


def test_sick_plus_exam_is_the_more_conservative_on_every_field():
    """Spec 16: sick plus exam resolves to the more conservative rule on every field."""
    merged = most_conservative(SICK_MODERATE, EXAM)
    expected = ModeRules(
        kcal_multiplier=1.05, force_maintenance=True, protein_held=True, training="blocked", volume_cap=0.0,
        load_cap_last_session=True, progression_allowed=False, fibre_relaxed=False, hydration_raised=False,
        electrolyte_prompts=False, logging_strict=False, streaks_paused=True, plateau_alerts=False,
        rate_alerts=False, estimation_tolerance="normal", restaurant_suggestions=False, guidance=("rest_screen",),
    )
    assert merged == expected
    for f in fields(ModeRules):
        assert getattr(merged, f.name) == conservative_value(f.name, getattr(SICK_MODERATE, f.name), getattr(EXAM, f.name))


def test_mild_plus_exam_takes_the_stricter_volume_cap_and_the_relaxed_logging():
    merged = most_conservative(SICK_MILD, EXAM)
    assert merged.training == "reduced" and merged.volume_cap == 0.5
    assert merged.force_maintenance and not merged.progression_allowed and merged.load_cap_last_session
    assert not merged.logging_strict and merged.streaks_paused
    assert "above_the_neck" in merged.guidance


@pytest.mark.parametrize("a", ALL)
@pytest.mark.parametrize("b", ALL)
def test_merge_is_commutative_idempotent_and_never_less_strict(a, b):
    ab, ba = most_conservative(a, b), most_conservative(b, a)
    assert ab == ba
    assert most_conservative(ab, a) == ab and most_conservative(ab, b) == ab
    assert most_conservative(a, a) == a
    assert ab.volume_cap <= min(a.volume_cap, b.volume_cap)
    assert ab.kcal_multiplier >= max(a.kcal_multiplier, b.kcal_multiplier)
    assert ab.progression_allowed == (a.progression_allowed and b.progression_allowed)


def test_fever_forces_at_least_moderate():
    assert effective_severity(HealthEvent(1, "illness", TODAY, severity="mild", fever_flag=True)) == "moderate"
    assert effective_severity(HealthEvent(1, "illness", TODAY, severity="gi", fever_flag=True)) == "gi"
    assert effective_severity(HealthEvent(1, "illness", TODAY, severity="none")) == "mild"
    state = resolve([HealthEvent(1, "illness", TODAY, severity="mild", fever_flag=True)], TODAY)
    assert state.training_locked_by_fever and state.rules.training == "blocked"
    assert state.rules.kcal_multiplier == 1.05
    assert state.names == ("illness:moderate",)


def test_rules_for_each_event_type():
    assert rules_for(HealthEvent(1, "illness", TODAY, severity="gi")) == SICK_GI
    assert rules_for(HealthEvent(1, "exam", TODAY)) == EXAM
    assert rules_for(HealthEvent(1, "travel", TODAY)) == TRAVEL
    assert rules_for(HealthEvent(1, "injury", TODAY)) == INJURY


def test_gi_relaxes_fibre_and_raises_hydration_at_maintenance():
    r = resolve([HealthEvent(1, "illness", TODAY, severity="gi")], TODAY).rules
    assert r.fibre_relaxed and r.hydration_raised and r.electrolyte_prompts
    assert r.force_maintenance and r.kcal_multiplier == 1.0 and r.training == "blocked"


def test_ramp_until_is_ended_plus_min_duration_seven():
    four = HealthEvent(1, "illness", TODAY - timedelta(days=3), ended_at=TODAY)
    assert ramp_until(four) == TODAY + timedelta(days=4)
    ten = HealthEvent(2, "illness", TODAY - timedelta(days=9), ended_at=TODAY)
    assert ramp_until(ten) == TODAY + timedelta(days=7)
    assert ramp_until(HealthEvent(3, "illness", TODAY)) is None
    assert ramp_until(HealthEvent(4, "exam", TODAY - timedelta(days=3), ended_at=TODAY)) is None


def test_return_ramp_rules_apply_after_the_event_ends():
    ev = HealthEvent(1, "illness", TODAY - timedelta(days=6), severity="moderate",
                     ended_at=TODAY - timedelta(days=2), ramp_until=TODAY + timedelta(days=3))
    state = resolve([ev], TODAY)
    assert state.active == () and state.ramping == (ev,)
    assert state.rules.force_maintenance and not state.rules.progression_allowed
    assert state.rules.load_cap_last_session and state.rules.training == "reduced"
    assert state.rules.volume_cap == RAMP.volume_cap
    assert state.names == ("ramp:1",)
    after = resolve([ev], TODAY + timedelta(days=4))
    assert after.rules == NORMAL and not after.any_active


def test_travel_suppresses_alerts_but_keeps_the_deficit():
    r = resolve([HealthEvent(1, "travel", TODAY)], TODAY).rules
    assert not r.plateau_alerts and not r.rate_alerts and r.estimation_tolerance == "relaxed"
    assert not r.force_maintenance and r.training == "normal" and r.restaurant_suggestions


def test_injury_is_exercise_level():
    ev = HealthEvent(1, "injury", TODAY, symptoms={"affected_groups": ["shoulders", "chest"]})
    state = resolve([ev], TODAY)
    assert state.rules == NORMAL
    assert state.affected_groups == ("shoulders", "chest")
    assert affected_groups([ev]) == ("shoulders", "chest")
    bench = Exercise(2, "Bench", "chest", ("triceps",))
    squat = Exercise(1, "Squat", "quads", ("glutes",))
    ohp = Exercise(4, "OHP", "shoulders", ("triceps",))
    assert exercise_is_affected(bench, state.affected_groups)
    assert exercise_is_affected(ohp, state.affected_groups)
    assert not exercise_is_affected(squat, state.affected_groups)


def test_prolonged_illness_adds_referral_guidance():
    ev = HealthEvent(1, "illness", TODAY - timedelta(days=4), severity="gi")
    state = resolve([ev], TODAY)
    assert state.referrals == (ev,)
    assert "see_a_doctor" in state.rules.guidance


def test_no_events_is_normal():
    state = resolve([], TODAY)
    assert state.rules == NORMAL and not state.any_active and state.names == ()

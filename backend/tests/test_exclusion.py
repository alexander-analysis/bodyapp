"""Spec 16: a 4-day illness event removes those rows from trend, TDEE, adherence and volume.

Two invariants, checked for every analytic:
  1. dropping the tagged rows entirely changes nothing;
  2. changing the tagged rows' values changes nothing.
"""
from dataclasses import replace
from datetime import timedelta

from app.engine.exclusion import clean_rows, excluded_rows
from app.engine.progression import prescribe_next
from app.engine.rollup import weekly_adherence
from app.engine.tdee import estimate_tdee
from app.engine.trend import clean_trend, trend_weight
from app.engine.volume import weekly_volume
from tests.fixtures.synthetic import ILLNESS_EVENT_ID, make_synthetic


def test_fixture_has_the_illness_where_expected(synthetic):
    tagged_days = sorted(d.day for d in synthetic.days if d.health_event_id == ILLNESS_EVENT_ID)
    assert tagged_days == [synthetic.day(30 + i) for i in range(4)]
    assert len(excluded_rows(synthetic.weights)) == 4
    assert len(excluded_rows(synthetic.workouts)) == 1
    assert len(clean_rows(synthetic.days)) == 56
    assert synthetic.events[0].ramp_until == synthetic.day(37)


def test_trend_ignores_tagged_weights(synthetic):
    base = clean_trend(trend_weight(synthetic.weights))
    dropped = clean_trend(trend_weight(clean_rows(synthetic.weights)))
    assert [p.day for p in base] == [p.day for p in dropped]
    perturbed = [replace(w, weight_kg=w.weight_kg - 15.0) if w.health_event_id else w for w in synthetic.weights]
    assert [p.trend_kg for p in clean_trend(trend_weight(perturbed))] == [p.trend_kg for p in base]


def test_tdee_ignores_tagged_days(synthetic):
    series = trend_weight(synthetic.weights)
    as_of = synthetic.day(42)
    kw = dict(profile=synthetic.profile, trend=series, as_of=as_of, events=synthetic.events)
    base = estimate_tdee(days=synthetic.days, **kw)
    dropped = estimate_tdee(days=clean_rows(synthetic.days), **kw)
    perturbed = estimate_tdee(days=[replace(d, kcal=9000.0, logged_complete=True) if d.health_event_id else d
                                    for d in synthetic.days], **kw)
    assert base.adaptive_kcal == dropped.adaptive_kcal == perturbed.adaptive_kcal
    assert base.confidence == dropped.confidence == perturbed.confidence
    assert base.clean_days == 17


def test_adherence_ignores_tagged_days(synthetic):
    week = [d for d in synthetic.days if synthetic.day(28) <= d.day <= synthetic.day(34)]
    assert any(d.health_event_id for d in week)
    base = weekly_adherence(week, synthetic.target)
    dropped = weekly_adherence(clean_rows(week), synthetic.target)
    perturbed = weekly_adherence([replace(d, kcal=100.0, protein_g=0.0) if d.health_event_id else d for d in week],
                                 synthetic.target)
    assert base == dropped == perturbed
    assert base.days_considered == len([d for d in clean_rows(week) if d.logged_complete])


def test_volume_ignores_tagged_sessions(synthetic):
    as_of = synthetic.day(33)
    base = weekly_volume(synthetic.workouts, synthetic.exercises, as_of)
    dropped = weekly_volume(clean_rows(synthetic.workouts), synthetic.exercises, as_of)
    assert base == dropped
    sick = excluded_rows(synthetic.workouts)[0]
    assert as_of - timedelta(days=6) <= sick.performed_on <= as_of  # it is inside the window and still not counted


def test_progression_ignores_tagged_sessions(synthetic):
    for ex in synthetic.exercises.values():
        base = prescribe_next(ex, synthetic.workouts)
        dropped = prescribe_next(ex, clean_rows(synthetic.workouts))
        assert base == dropped


def test_linear_fixture_has_no_events(linear):
    assert linear.events == [] and excluded_rows(linear.days) == []


def test_exclusion_helpers_preserve_order():
    syn = make_synthetic(seed=3)
    assert [w.day for w in clean_rows(syn.weights)] == sorted(w.day for w in clean_rows(syn.weights))
    assert clean_rows(syn.weights) + excluded_rows(syn.weights) != syn.weights or not excluded_rows(syn.weights)
    assert len(clean_rows(syn.weights)) + len(excluded_rows(syn.weights)) == len(syn.weights)

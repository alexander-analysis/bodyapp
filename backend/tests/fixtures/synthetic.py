"""Synthetic 60-day dataset (spec 15): a cut at a known true TDEE, with a 4-day
moderate illness (fever) starting on day 30, a water-weight dip during it and a
rehydration rebound after it. Deterministic for a given seed.

``make_synthetic(noise=False, illness=False)`` gives the noise-free linear
series used for exactness tests.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.engine.types import (
    DayRow,
    Exercise,
    HealthEvent,
    SetRow,
    Target,
    UserProfile,
    WeightPoint,
    WorkoutRow,
)

START = date(2026, 6, 1)  # a Monday
N_DAYS = 60
TRUE_TDEE = 2700.0
INTAKE = 2200.0
START_WEIGHT = 82.0
KCAL_PER_KG = 7700.0
DAILY_LOSS_KG = (TRUE_TDEE - INTAKE) / KCAL_PER_KG  # ~0.065 kg/day, ~0.55 % BW/week

ILLNESS_START = 30
ILLNESS_DAYS = 4
ILLNESS_DIP = (1.0, 2.0, 2.2, 1.8)  # kg below the true trajectory on each sick day
REBOUND = (1.0, 0.4)  # kg below true on the days after, then back on the line
ILLNESS_INTAKE = 1500.0
ILLNESS_EVENT_ID = 1

MISSING_WEIGH_INS = (12, 20, 47)
UNLOGGED_DAYS = (45,)

EXERCISES: dict[int, Exercise] = {
    1: Exercise(1, "Back squat", "quads", ("glutes", "hamstrings"), tier=1, increment_kg=5.0),
    2: Exercise(2, "Bench press", "chest", ("triceps", "front_delts"), tier=1),
    3: Exercise(3, "Barbell row", "back", ("biceps", "rear_delts"), tier=1),
    4: Exercise(4, "Overhead press", "shoulders", ("triceps",)),
    5: Exercise(5, "Romanian deadlift", "hamstrings", ("glutes", "back")),
    6: Exercise(6, "Pull-up", "back", ("biceps",)),
}
BASE_LOAD = {1: 100.0, 2: 70.0, 3: 60.0, 4: 40.0, 5: 90.0, 6: 0.0}
TEMPLATES = {0: ("upper_a", (2, 3, 4)), 1: ("lower_a", (1, 5)), 3: ("upper_b", (2, 6, 3)), 4: ("lower_b", (1, 5))}


@dataclass
class Synthetic:
    profile: UserProfile
    start: date
    target: Target
    weights: list[WeightPoint] = field(default_factory=list)
    days: list[DayRow] = field(default_factory=list)
    events: list[HealthEvent] = field(default_factory=list)
    workouts: list[WorkoutRow] = field(default_factory=list)
    exercises: dict[int, Exercise] = field(default_factory=dict)
    true_tdee: float = TRUE_TDEE
    intake: float = INTAKE

    def day(self, offset: int) -> date:
        return self.start + timedelta(days=offset)

    @property
    def end(self) -> date:
        return self.day(N_DAYS - 1)


def true_weight(offset: int) -> float:
    return START_WEIGHT - DAILY_LOSS_KG * offset


def make_synthetic(*, seed: int = 7, noise: bool = True, illness: bool = True, n_days: int = N_DAYS) -> Synthetic:
    rng = random.Random(seed)
    profile = UserProfile(sex="m", birth_date=date(2002, 3, 15), height_cm=180.0, goal_weight_kg=76.0)
    target = Target(
        effective_from=START, kcal=int(INTAKE), protein_g=170, fat_g_min=80, phase="cut",
        reason="initial targets", set_by="user",
    )
    syn = Synthetic(profile=profile, start=START, target=target, exercises=dict(EXERCISES))

    sick_days = set(range(ILLNESS_START, ILLNESS_START + ILLNESS_DAYS)) if illness else set()
    if illness:
        ended = START + timedelta(days=ILLNESS_START + ILLNESS_DAYS - 1)
        syn.events.append(
            HealthEvent(
                id=ILLNESS_EVENT_ID, type="illness", started_at=START + timedelta(days=ILLNESS_START),
                severity="moderate", fever_flag=True, ended_at=ended,
                ramp_until=ended + timedelta(days=min(ILLNESS_DAYS, 7)), symptoms={"notes": "flu, fever 38.5"},
            )
        )

    for d in range(n_days):
        day = START + timedelta(days=d)
        w = true_weight(d)
        event_id = ILLNESS_EVENT_ID if d in sick_days else None
        if d in sick_days:
            w -= ILLNESS_DIP[d - ILLNESS_START]
        elif illness and ILLNESS_START + ILLNESS_DAYS <= d < ILLNESS_START + ILLNESS_DAYS + len(REBOUND):
            w -= REBOUND[d - ILLNESS_START - ILLNESS_DAYS]
        if noise:
            w += rng.gauss(0.0, 0.3)
        if not (noise and d in MISSING_WEIGH_INS):
            syn.weights.append(WeightPoint(day, round(w, 2), event_id))

        if d in sick_days:
            syn.days.append(DayRow(day, kcal=ILLNESS_INTAKE, protein_g=120.0, carbs_g=180.0, fat_g=50.0,
                                   fibre_g=15.0, steps=2000, logged_complete=True, mean_confidence=0.9,
                                   health_event_id=event_id))
            continue
        if noise and d in UNLOGGED_DAYS:
            syn.days.append(DayRow(day, steps=8000, logged_complete=False))
            continue
        complete = True if not noise else rng.random() > 0.10
        kcal = INTAKE + (rng.gauss(0.0, 120.0) if noise else 0.0)
        if not complete:
            kcal *= 0.55
        conf = 0.9 if not noise else rng.choice((0.95, 0.9, 0.9, 0.9, 0.6))
        syn.days.append(
            DayRow(
                day, kcal=round(kcal, 1), protein_g=round(kcal * 0.31 / 4, 1), carbs_g=round(kcal * 0.40 / 4, 1),
                fat_g=round(kcal * 0.29 / 9, 1), fibre_g=30.0,
                steps=int(9000 + (rng.gauss(0.0, 1500.0) if noise else 0.0)),
                logged_complete=complete, mean_confidence=conf,
            )
        )

    _add_workouts(syn, n_days, sick_days)
    return syn


def _add_workouts(syn: Synthetic, n_days: int, sick_days: set[int]) -> None:
    counters = {eid: 0 for eid in EXERCISES}
    wid = 0
    for d in range(n_days):
        tmpl = TEMPLATES.get(d % 7)
        if tmpl is None:
            continue
        sick = d in sick_days
        if sick and d != ILLNESS_START + 1:
            continue  # skipped training while ill, except one light session on day 31
        name, exercise_ids = tmpl
        sets: list[SetRow] = []
        for eid in exercise_ids:
            ex = EXERCISES[eid]
            k = counters[eid]
            cycle, step = divmod(k, 5)
            weight = BASE_LOAD[eid] + cycle * ex.increment_kg
            reps = ex.rep_min + step  # 8..12, then load goes up and reps reset
            rir = 3 - step // 2  # 3,3,2,2,1
            if sick:
                weight = max(0.0, weight - 2 * ex.increment_kg)
                reps, rir = ex.rep_min, 4
            else:
                counters[eid] += 1
            if ex.tier == 1:
                sets.append(SetRow(eid, 0, round(weight * 0.6, 1), 8, None, is_warmup=True))
            for i in range(3):
                sets.append(SetRow(eid, i + 1, weight, reps, rir))
        wid += 1
        syn.workouts.append(
            WorkoutRow(
                id=wid, performed_on=syn.day(d), sets=tuple(sets),
                health_event_id=ILLNESS_EVENT_ID if sick else None,
                rpe=6 if sick else 8, template=name,
            )
        )

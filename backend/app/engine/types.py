"""Dataclasses shared by the engine.

The engine is pure: it takes these in, returns these (or plain values) out,
and never touches the database or the network. The DB layer projects rows
into these types; the API layer projects them back out.

Anything carrying a ``health_event_id`` attribute is subject to the analytics
exclusion rule in :mod:`app.engine.exclusion`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Mapping

Sex = Literal["m", "f"]
Phase = Literal["cut", "maintain", "gain"]
EventType = Literal["illness", "injury", "travel", "exam"]
Severity = Literal["mild", "moderate", "gi", "none"]
SetBy = Literal["engine", "user"]
TdeeMethod = Literal["formula", "adaptive"]
InputMethod = Literal["barcode", "photo", "text", "favorite", "manual"]
Meal = Literal["breakfast", "lunch", "dinner", "snack"]


@dataclass(frozen=True)
class UserProfile:
    sex: Sex
    birth_date: date
    height_cm: float
    goal_weight_kg: float | None = None

    def age_on(self, on: date) -> int:
        years = on.year - self.birth_date.year
        if (on.month, on.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years


@dataclass(frozen=True)
class HealthEvent:
    """A row of ``health_events``. Rows tagged with one are excluded from analytics."""

    id: int
    type: EventType
    started_at: date
    severity: Severity = "none"
    fever_flag: bool = False
    ended_at: date | None = None
    ramp_until: date | None = None
    symptoms: Mapping[str, object] = field(default_factory=dict)

    def is_active_on(self, day: date) -> bool:
        if day < self.started_at:
            return False
        return self.ended_at is None or day <= self.ended_at

    def in_ramp_on(self, day: date) -> bool:
        """True during the post-event return ramp (after ``ended_at``, up to ``ramp_until``)."""
        if self.ended_at is None or self.ramp_until is None:
            return False
        return self.ended_at < day <= self.ramp_until

    def duration_days(self, as_of: date | None = None) -> int:
        end = self.ended_at if self.ended_at is not None else as_of
        if end is None:
            raise ValueError("open event needs as_of to compute duration")
        return (end - self.started_at).days + 1


@dataclass(frozen=True)
class WeightPoint:
    day: date
    weight_kg: float
    health_event_id: int | None = None


@dataclass(frozen=True)
class TrendPoint:
    """One point of the EWMA series.

    ``excluded`` points belong to a health event: their ``trend_kg`` is a
    display-only interpolation across the gap and must never feed analytics.
    """

    day: date
    raw_kg: float
    trend_kg: float
    excluded: bool = False


@dataclass(frozen=True)
class DayRow:
    """One row of ``daily_rollup`` as the engine sees it."""

    day: date
    kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fibre_g: float | None = None
    alcohol_g: float | None = None
    steps: int | None = None
    logged_complete: bool = False
    mean_confidence: float | None = None
    health_event_id: int | None = None

    @property
    def is_unlogged(self) -> bool:
        return not self.kcal


@dataclass(frozen=True)
class FoodEntryRow:
    """One row of ``food_entries`` (the fields the rollup needs)."""

    day: date
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    confidence: float
    fibre_g: float = 0.0
    alcohol_g: float = 0.0
    meal: Meal | None = None
    input_method: InputMethod = "manual"
    health_event_id: int | None = None


@dataclass(frozen=True)
class Target:
    """A row of ``targets``. Versioned: never mutated, always re-inserted with a reason."""

    effective_from: date
    kcal: int
    protein_g: int
    fat_g_min: int
    phase: Phase
    reason: str
    set_by: SetBy
    fibre_g: int = 30
    steps: int = 9000


@dataclass(frozen=True)
class Exercise:
    id: int
    name: str
    muscle_group: str
    secondary_groups: tuple[str, ...] = ()
    tier: int = 2
    increment_kg: float = 2.5
    rep_min: int = 8
    rep_max: int = 12


@dataclass(frozen=True)
class SetRow:
    exercise_id: int
    set_index: int
    weight_kg: float
    reps: int
    rir: int | None = None
    is_warmup: bool = False


@dataclass(frozen=True)
class WorkoutRow:
    id: int
    performed_on: date
    sets: tuple[SetRow, ...]
    health_event_id: int | None = None
    rpe: int | None = None
    template: str | None = None

    def sets_for(self, exercise_id: int) -> tuple[SetRow, ...]:
        return tuple(s for s in self.sets if s.exercise_id == exercise_id)

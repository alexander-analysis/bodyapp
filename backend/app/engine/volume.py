"""Weekly volume (spec 5.5): working sets per muscle group over a 7-day window.

A working set is ``is_warmup = 0`` with RIR <= 3 (a set with no RIR logged is
counted). Secondary groups count at 0.5. Groups below LOW_SETS or above
HIGH_SETS are flagged. Sessions tagged with a health event are excluded.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal, Mapping, Sequence

from .exclusion import clean_rows
from .types import Exercise, WorkoutRow

WINDOW_DAYS = 7
LOW_SETS = 10
HIGH_SETS = 20
SECONDARY_WEIGHT = 0.5
WORKING_RIR_MAX = 3

Flag = Literal["low", "ok", "high"]


def weekly_volume(
    workouts: Sequence[WorkoutRow],
    exercises: Mapping[int, Exercise],
    as_of: date,
    *,
    window_days: int = WINDOW_DAYS,
) -> dict[str, float]:
    start = as_of - timedelta(days=window_days - 1)
    volume: dict[str, float] = {}
    for w in clean_rows(workouts):
        if not (start <= w.performed_on <= as_of):
            continue
        for s in w.sets:
            if s.is_warmup or (s.rir is not None and s.rir > WORKING_RIR_MAX):
                continue
            ex = exercises.get(s.exercise_id)
            if ex is None:
                continue
            volume[ex.muscle_group] = volume.get(ex.muscle_group, 0.0) + 1.0
            for g in ex.secondary_groups:
                volume[g] = volume.get(g, 0.0) + SECONDARY_WEIGHT
    return {g: round(v, 1) for g, v in sorted(volume.items())}


def flag(sets: float) -> Flag:
    if sets < LOW_SETS:
        return "low"
    if sets > HIGH_SETS:
        return "high"
    return "ok"


def volume_flags(volume: Mapping[str, float], *, cap: float = 1.0) -> dict[str, Flag]:
    """Flags per group. ``cap`` scales the band for modes that cut prescribed
    volume (exam: 2/3, sick mild: 1/2) so a deliberately reduced week is not
    reported as under-training."""
    return {g: _flag_scaled(v, cap) for g, v in volume.items()}


def _flag_scaled(sets: float, cap: float) -> Flag:
    if sets < LOW_SETS * cap:
        return "low"
    if sets > HIGH_SETS * cap:
        return "high"
    return "ok"

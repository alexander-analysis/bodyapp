"""Daily rollup and weekly adherence — the arithmetic behind ``daily_rollup``.

``logged_complete`` is a judgement the user can override from the UI; the
heuristic here is the default: at least MIN_ENTRIES entries across at least
MIN_MEALS distinct meals, totalling at least MIN_KCAL_FRACTION of the day's
target. ``mean_confidence`` is the entry-weighted mean (each entry counts once).

Adherence counts clean, logged-complete days only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import fmean
from typing import Sequence

from .exclusion import clean_rows
from .types import DayRow, FoodEntryRow, Target

MIN_ENTRIES = 3
MIN_MEALS = 2
MIN_KCAL_FRACTION = 0.5
KCAL_ADHERENCE_BAND = 0.10  # within +/-10% of target counts as a hit


def is_logged_complete(entries: Sequence[FoodEntryRow], target_kcal: int | None, *, user_marked: bool | None = None) -> bool:
    if user_marked is not None:
        return user_marked
    if len(entries) < MIN_ENTRIES:
        return False
    meals = {e.meal for e in entries if e.meal is not None}
    if len(meals) < MIN_MEALS:
        return False
    if target_kcal:
        return sum(e.kcal for e in entries) >= MIN_KCAL_FRACTION * target_kcal
    return True


def rollup_day(
    day: date,
    entries: Sequence[FoodEntryRow],
    *,
    target_kcal: int | None = None,
    steps: int | None = None,
    water_ml: int | None = None,
    sleep_h: float | None = None,
    trend_weight_kg: float | None = None,
    health_event_id: int | None = None,
    user_marked_complete: bool | None = None,
) -> DayRow:
    if entries:
        row = DayRow(
            day=day,
            kcal=round(sum(e.kcal for e in entries), 1),
            protein_g=round(sum(e.protein_g for e in entries), 1),
            carbs_g=round(sum(e.carbs_g for e in entries), 1),
            fat_g=round(sum(e.fat_g for e in entries), 1),
            fibre_g=round(sum(e.fibre_g for e in entries), 1),
            alcohol_g=round(sum(e.alcohol_g for e in entries), 1),
            steps=steps,
            logged_complete=is_logged_complete(entries, target_kcal, user_marked=user_marked_complete),
            mean_confidence=round(fmean(e.confidence for e in entries), 3),
            health_event_id=health_event_id,
        )
    else:
        row = DayRow(day=day, steps=steps, logged_complete=bool(user_marked_complete), health_event_id=health_event_id)
    # water/sleep/trend are carried by the DB layer; DayRow keeps the engine's fields only.
    return row


@dataclass(frozen=True)
class Adherence:
    days_considered: int
    kcal_hit_pct: float | None
    protein_hit_pct: float | None
    steps_hit_pct: float | None
    mean_kcal: float | None
    mean_protein_g: float | None
    mean_confidence: float | None


def weekly_adherence(days: Sequence[DayRow], target: Target) -> Adherence:
    """Over clean, logged-complete rows: how often kcal landed within the band,
    protein met the target, steps met the target."""
    rows = [d for d in clean_rows(days) if d.logged_complete and d.kcal is not None]
    if not rows:
        return Adherence(0, None, None, None, None, None, None)
    kcal_hits = sum(1 for d in rows if abs(d.kcal - target.kcal) <= KCAL_ADHERENCE_BAND * target.kcal)  # type: ignore[operator]
    protein_rows = [d for d in rows if d.protein_g is not None]
    protein_hits = sum(1 for d in protein_rows if d.protein_g >= target.protein_g)  # type: ignore[operator]
    step_rows = [d for d in rows if d.steps is not None]
    step_hits = sum(1 for d in step_rows if d.steps >= target.steps)  # type: ignore[operator]
    confs = [d.mean_confidence for d in rows if d.mean_confidence is not None]
    return Adherence(
        days_considered=len(rows),
        kcal_hit_pct=round(100.0 * kcal_hits / len(rows), 1),
        protein_hit_pct=round(100.0 * protein_hits / len(protein_rows), 1) if protein_rows else None,
        steps_hit_pct=round(100.0 * step_hits / len(step_rows), 1) if step_rows else None,
        mean_kcal=round(fmean(d.kcal for d in rows), 1),  # type: ignore[misc]
        mean_protein_g=round(fmean(d.protein_g for d in protein_rows), 1) if protein_rows else None,  # type: ignore[misc]
        mean_confidence=round(fmean(confs), 3) if confs else None,
    )

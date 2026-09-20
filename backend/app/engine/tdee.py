"""Maintenance calories: formula baseline, adaptive back-calculation, confidence blend.

Formula (starting guess only): Mifflin-St Jeor x ACTIVITY_FACTOR.

Adaptive, over a rolling ``WINDOW_DAYS`` window of clean rows:

    TDEE = mean(kcal_i over logged-complete clean days)
         + (T_start - T_end) * KCAL_PER_KG / n

where ``n`` is the number of day-transitions between the two trend endpoints
(``T_end.day - T_start.day``) less any excluded days in between — i.e. the clean
days over which the mass change accrued. The mean
intake is taken over logged-complete days only: an incompletely logged day's
intake is *unknown*, not small, and treating it as small is exactly the
"engine concludes maintenance is lower than it is" failure spec 8.2 warns about.

Blend:  TDEE_used = c * TDEE_adaptive + (1 - c) * TDEE_formula
with c = (clean logged-complete days / WINDOW_DAYS, capped at 1) scaled by the
window's mean logging confidence (spec 7: below 0.6 the adaptive estimate is
down-weighted). Below c = 0.5 the formula is used alone and the note says why.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean
from typing import Sequence

from . import guards
from .exclusion import clean_rows
from .trend import TrendPoint, trend_on_or_after, trend_on_or_before
from .types import DayRow, HealthEvent, Sex, TdeeMethod, UserProfile

KCAL_PER_KG = 7700
ACTIVITY_FACTOR = 1.35  # sedentary student + 4 lifting sessions + walking
WINDOW_DAYS = 21
MIN_CONFIDENCE_FOR_BLEND = 0.5
ESTABLISHED_COMPLETE_DAYS = 14  # "21 days of data with at least 14 logged-complete"
LOW_LOGGING_CONFIDENCE = 0.6  # below this the adaptive estimate is down-weighted
MIN_TREND_SPAN_DAYS = 10  # trend endpoints must span at least this many days


@dataclass(frozen=True)
class TdeeEstimate:
    computed_on: date
    window_days: int
    formula_kcal: float
    adaptive_kcal: float | None
    confidence: float
    tdee_kcal: float  # the value the rest of the engine uses
    method: TdeeMethod
    clean_days: int
    complete_days: int
    mean_logging_confidence: float | None
    notes: tuple[str, ...]

    @property
    def is_established(self) -> bool:
        return self.method == "adaptive" and self.complete_days >= ESTABLISHED_COMPLETE_DAYS


def mifflin_st_jeor(weight_kg: float, height_cm: float, age_years: int, sex: Sex) -> float:
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years
    return base + 5.0 if sex == "m" else base - 161.0


def formula_tdee(profile: UserProfile, weight_kg: float, on: date, *, activity_factor: float = ACTIVITY_FACTOR) -> float:
    return mifflin_st_jeor(weight_kg, profile.height_cm, profile.age_on(on), profile.sex) * activity_factor


def adaptive_tdee(mean_intake_kcal: float, trend_start_kg: float, trend_end_kg: float, n_days: int) -> float:
    if n_days <= 0:
        raise ValueError("n_days must be positive")
    return mean_intake_kcal + (trend_start_kg - trend_end_kg) * KCAL_PER_KG / n_days


def blend(adaptive_kcal: float | None, formula_kcal: float, confidence: float) -> tuple[float, TdeeMethod]:
    if adaptive_kcal is None or confidence < MIN_CONFIDENCE_FOR_BLEND:
        return formula_kcal, "formula"
    c = min(1.0, max(0.0, confidence))
    return c * adaptive_kcal + (1.0 - c) * formula_kcal, "adaptive"


def logging_confidence_factor(mean_confidence: float | None) -> float:
    """1.0 at or above LOW_LOGGING_CONFIDENCE, falling linearly to 0 below it."""
    if mean_confidence is None:
        return 1.0
    return min(1.0, max(0.0, mean_confidence / LOW_LOGGING_CONFIDENCE))


def excluded_days(days: Sequence[DayRow], events: Sequence[HealthEvent], start: date, end: date) -> set[date]:
    """Calendar days in [start, end] covered by a health event: a tagged row, or
    an event active that day (whether or not anything was logged)."""
    out = {d.day for d in days if d.health_event_id is not None and start <= d.day <= end}
    cur = start
    while cur <= end:
        if any(e.is_active_on(cur) for e in events):
            out.add(cur)
        cur += timedelta(days=1)
    return out


def estimate_tdee(
    *,
    profile: UserProfile,
    days: Sequence[DayRow],
    trend: Sequence[TrendPoint],
    as_of: date,
    events: Sequence[HealthEvent] = (),
    window_days: int = WINDOW_DAYS,
) -> TdeeEstimate:
    """Compute the estimate as of ``as_of`` from rollup rows and the trend series."""
    window_start = as_of - timedelta(days=window_days - 1)
    in_window = [d for d in days if window_start <= d.day <= as_of]
    clean = clean_rows(in_window)
    complete = [d for d in clean if d.logged_complete and d.kcal is not None]

    t_end = trend_on_or_before(trend, as_of)
    t_start = trend_on_or_after(trend, window_start)
    current_weight = t_end.trend_kg if t_end is not None else None
    if current_weight is None:
        raw = [p for p in trend if not p.excluded]
        current_weight = raw[-1].raw_kg if raw else None

    notes: list[str] = []
    formula = formula_tdee(profile, current_weight, as_of) if current_weight else 0.0
    if not current_weight:
        notes.append("no weight data: formula estimate unavailable")

    confs = [d.mean_confidence for d in complete if d.mean_confidence is not None]
    mean_conf = fmean(confs) if confs else None
    conf_factor = logging_confidence_factor(mean_conf)
    confidence = min(1.0, len(complete) / window_days) * conf_factor
    if mean_conf is not None and mean_conf < LOW_LOGGING_CONFIDENCE:
        notes.append(
            f"intake figures are estimates (mean logging confidence {mean_conf:.2f} < {LOW_LOGGING_CONFIDENCE}); "
            "adaptive estimate down-weighted"
        )

    adaptive: float | None = None
    if guards.has_logging_gap(in_window, window_start, as_of, events):
        notes.append(f"{guards.RAIL_LOGGING_GAP}: {guards.MAX_UNLOGGED_GAP_DAYS}+ consecutive unlogged days; window unreliable, adaptive recompute suppressed")
        confidence = 0.0
    elif not complete:
        notes.append("no logged-complete days in window")
    elif t_start is None or t_end is None or (t_end.day - t_start.day).days < MIN_TREND_SPAN_DAYS:
        notes.append(f"trend endpoints span fewer than {MIN_TREND_SPAN_DAYS} days")
    else:
        # n = day-transitions between the two trend measurements, less excluded days.
        span = (t_end.day - t_start.day).days
        n = span - len(excluded_days(in_window, events, t_start.day + timedelta(days=1), t_end.day))
        if n <= 0:
            notes.append("no clean days between trend endpoints")
        else:
            mean_intake = fmean(d.kcal for d in complete)  # type: ignore[arg-type]
            adaptive = adaptive_tdee(mean_intake, t_start.trend_kg, t_end.trend_kg, n)

    used, method = blend(adaptive, formula, confidence)
    if method == "formula" and adaptive is not None:
        notes.append(
            f"confidence {confidence:.2f} below {MIN_CONFIDENCE_FOR_BLEND}: using the formula estimate alone "
            f"({len(complete)} logged-complete clean days of {window_days})"
        )
    elif method == "formula" and adaptive is None and not notes:
        notes.append("adaptive estimate not available yet")

    return TdeeEstimate(
        computed_on=as_of,
        window_days=window_days,
        formula_kcal=round(formula, 1),
        adaptive_kcal=round(adaptive, 1) if adaptive is not None else None,
        confidence=round(confidence, 3),
        tdee_kcal=round(used, 1),
        method=method,
        clean_days=len(clean),
        complete_days=len(complete),
        mean_logging_confidence=round(mean_conf, 3) if mean_conf is not None else None,
        notes=tuple(notes),
    )

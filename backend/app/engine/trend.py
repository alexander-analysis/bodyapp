"""Trend weight: exponentially weighted moving average over clean weigh-ins.

    T_t = alpha * w_t + (1 - alpha) * T_{t-1},   alpha = 0.1

* ``T_0`` is the mean of the first ``SEED_DAYS`` available clean weights; those
  seed points display the seed value.
* Missing days are skipped, never interpolated: the recurrence steps over the
  weigh-ins that exist, whatever the calendar gap between them.
* Rows tagged with a health event are excluded (spec 8.2). The trend resumes
  from the last pre-event anchor and the first ``RETURN_RAMP_READINGS`` clean
  readings after the gap are weighted in with a rising alpha (spec 8.3), so a
  rehydration rebound cannot masquerade as fat gain.
* Excluded points are still returned, flagged ``excluded=True``, with a
  display-only linear interpolation across the gap (spec 8.2: the chart shows a
  shaded band with the line carried across, not broken).
"""
from __future__ import annotations

from datetime import date
from statistics import fmean
from typing import Sequence

from .types import TrendPoint, WeightPoint

ALPHA = 0.1
SEED_DAYS = 7
RETURN_RAMP_READINGS = 5


def trend_weight(points: Sequence[WeightPoint], *, alpha: float = ALPHA) -> list[TrendPoint]:
    ordered = sorted(points, key=lambda p: p.day)
    clean = [p for p in ordered if p.health_event_id is None]
    if not clean:
        return [TrendPoint(p.day, p.weight_kg, p.weight_kg, excluded=True) for p in ordered]

    seed_n = min(SEED_DAYS, len(clean))
    seed = fmean(p.weight_kg for p in clean[:seed_n])

    out: list[TrendPoint] = []
    t = seed
    clean_seen = 0
    gap_pending = False  # an excluded run sits between the previous clean point and the next
    ramp_left = 0

    for p in ordered:
        if p.health_event_id is not None:
            gap_pending = True
            out.append(TrendPoint(p.day, p.weight_kg, t, excluded=True))
            continue

        if clean_seen < seed_n:
            t = seed
            gap_pending = False
        else:
            if gap_pending:
                ramp_left = RETURN_RAMP_READINGS
                gap_pending = False
            a = alpha
            if ramp_left > 0:
                k = RETURN_RAMP_READINGS - ramp_left + 1
                a = alpha * k / RETURN_RAMP_READINGS
                ramp_left -= 1
            t = a * p.weight_kg + (1 - a) * t
        clean_seen += 1
        out.append(TrendPoint(p.day, p.weight_kg, t))

    return _interpolate_excluded(out)


def _interpolate_excluded(series: list[TrendPoint]) -> list[TrendPoint]:
    """Carry the line across excluded runs (display only)."""
    result = list(series)
    i = 0
    n = len(result)
    while i < n:
        if not result[i].excluded:
            i += 1
            continue
        j = i
        while j < n and result[j].excluded:
            j += 1
        before = result[i - 1] if i > 0 else None
        after = result[j] if j < n else None
        for k in range(i, j):
            p = result[k]
            if before is not None and after is not None:
                span = (after.day - before.day).days or 1
                frac = (p.day - before.day).days / span
                value = before.trend_kg + (after.trend_kg - before.trend_kg) * frac
            elif before is not None:
                value = before.trend_kg
            elif after is not None:
                value = after.trend_kg
            else:
                value = p.raw_kg
            result[k] = TrendPoint(p.day, p.raw_kg, value, excluded=True)
        i = j
    return result


def clean_trend(series: Sequence[TrendPoint]) -> list[TrendPoint]:
    """Only the points that may feed analytics."""
    return [p for p in series if not p.excluded]


def trend_on_or_before(series: Sequence[TrendPoint], day: date) -> TrendPoint | None:
    """Latest clean trend point on or before ``day``."""
    best: TrendPoint | None = None
    for p in series:
        if p.excluded or p.day > day:
            continue
        if best is None or p.day > best.day:
            best = p
    return best


def trend_on_or_after(series: Sequence[TrendPoint], day: date) -> TrendPoint | None:
    """Earliest clean trend point on or after ``day``."""
    best: TrendPoint | None = None
    for p in series:
        if p.excluded or p.day < day:
            continue
        if best is None or p.day < best.day:
            best = p
    return best


def rate_pct_per_week(start: TrendPoint, end: TrendPoint) -> float | None:
    """Loss rate in % of body weight per week between two trend points (positive = losing)."""
    span = (end.day - start.day).days
    if span <= 0 or end.trend_kg <= 0:
        return None
    return (start.trend_kg - end.trend_kg) / end.trend_kg * 100.0 * 7.0 / span

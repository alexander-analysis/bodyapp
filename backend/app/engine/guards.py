"""Safety rails. Hard-coded, not configurable from the UI, not reachable by Gemini.

Changing any constant here requires a code change and a commit — deliberately.
``apply_rails`` is the single enforcement point: every ``targets`` row, whether
authored by the weekly review, a mode activation or a user override, passes
through it before it is written.

SCOPE: this is a tracker, not a clinician. It has no view into injury, illness
severity, medication or bloodwork. When a rail trips, it stops giving
instructions rather than giving cautious ones.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Sequence

from .types import DayRow, HealthEvent, Target

KCAL_FLOOR = 1700  # absolute minimum daily target
PROTEIN_FLOOR_G = 150
FAT_FLOOR_G = 80
MAX_TARGET_CHANGE_KCAL = 200
MIN_DAYS_BETWEEN_CHANGES = 7
MAX_LOSS_RATE_PCT_WEEK = 1.0
MAX_SICK_DAYS_BEFORE_REFERRAL = 7
MAX_GI_DAYS_BEFORE_REFERRAL = 3
MAX_UNLOGGED_GAP_DAYS = 4  # 4+ consecutive unlogged days marks a window unreliable

SCOPE_STATEMENT = (
    "This is a tracker, not a clinician. It has no view into injury, illness "
    "severity, medication or bloodwork. When a safety rail trips it stops giving "
    "instructions rather than giving cautious ones."
)

# Rail names, used verbatim in ``targets.reason`` so every clamp is auditable.
RAIL_KCAL_FLOOR = "rail:kcal_floor"
RAIL_PROTEIN_FLOOR = "rail:protein_floor"
RAIL_FAT_FLOOR = "rail:fat_floor"
RAIL_ILLNESS_NO_DEFICIT = "rail:no_deficit_during_illness"
RAIL_CHANGE_RATE = "rail:change_rate"
RAIL_RAPID_LOSS = "rail:rapid_loss"
RAIL_LOGGING_GAP = "rail:logging_gap"
RAIL_PROLONGED_ILLNESS = "rail:prolonged_illness"
RAIL_FEVER = "rail:fever_training_lock"


@dataclass(frozen=True)
class RailContext:
    today: date
    events: Sequence[HealthEvent] = ()
    maintenance_kcal: int | None = None  # current TDEE estimate, used when illness forces maintenance
    previous: Target | None = None
    last_change_on: date | None = None  # effective_from of the most recent targets row


@dataclass(frozen=True)
class RailResult:
    target: Target | None  # None when the write is rejected outright
    tripped: tuple[str, ...]
    reason: str

    @property
    def rejected(self) -> bool:
        return self.target is None

    @property
    def clamped(self) -> bool:
        return self.target is not None and bool(self.tripped)


def illness_active(events: Sequence[HealthEvent], day: date) -> list[HealthEvent]:
    return [e for e in events if e.type == "illness" and e.is_active_on(day)]


def illness_ramp(events: Sequence[HealthEvent], day: date) -> list[HealthEvent]:
    return [e for e in events if e.type == "illness" and e.in_ramp_on(day)]


def illness_forces_maintenance(events: Sequence[HealthEvent], day: date) -> bool:
    """No deficit during any illness, nor during its return ramp (spec 8.1, 8.3, 9)."""
    return bool(illness_active(events, day) or illness_ramp(events, day))


def training_locked(events: Sequence[HealthEvent], day: date) -> bool:
    """Fever locks training regardless of the severity setting."""
    return any(e.fever_flag for e in illness_active(events, day))


def referral_due(events: Sequence[HealthEvent], day: date) -> list[HealthEvent]:
    """Illnesses that have run past the referral thresholds: stop guiding, suggest a doctor."""
    due: list[HealthEvent] = []
    for e in illness_active(events, day):
        days = e.duration_days(as_of=day)
        if e.severity == "gi":
            if days > MAX_GI_DAYS_BEFORE_REFERRAL:
                due.append(e)
        elif days > MAX_SICK_DAYS_BEFORE_REFERRAL:  # mild or moderate: a week is long enough
            due.append(e)
    return due


def rapid_loss(rate_pct_week: float | None) -> bool:
    return rate_pct_week is not None and rate_pct_week > MAX_LOSS_RATE_PCT_WEEK


def has_logging_gap(days: Sequence[DayRow], start: date, end: date, events: Sequence[HealthEvent] = ()) -> bool:
    """True when ``MAX_UNLOGGED_GAP_DAYS`` or more consecutive calendar days in
    [start, end] have no intake logged at all (absent row or zero kcal).

    Days covered by a health event are *excluded*, not unlogged: they are
    transparent here, neither extending nor resetting the run. Days before the
    first logged day are "before the app", not a gap."""
    by_day = {d.day: d for d in days}
    logged_days = [d.day for d in days if not d.is_unlogged]
    if not logged_days:
        return False
    start = max(start, min(logged_days))
    run = 0
    cur = start
    while cur <= end:
        row = by_day.get(cur)
        if (row is not None and row.health_event_id is not None) or any(e.is_active_on(cur) for e in events):
            cur += timedelta(days=1)
            continue
        if row is None or row.is_unlogged:
            run += 1
            if run >= MAX_UNLOGGED_GAP_DAYS:
                return True
        else:
            run = 0
        cur += timedelta(days=1)
    return False


def apply_rails(proposed: Target, ctx: RailContext) -> RailResult:
    """Clamp or reject a proposed ``targets`` row. Never silently adjusts: every
    clamp is named in ``tripped`` and appended to the row's reason."""
    t = proposed
    tripped: list[str] = []
    notes: list[str] = []

    if illness_forces_maintenance(ctx.events, ctx.today):
        forced_kcal = t.kcal
        if ctx.maintenance_kcal is not None:
            forced_kcal = max(t.kcal, ctx.maintenance_kcal)
        if t.phase != "maintain" or forced_kcal != t.kcal:
            t = replace(t, phase="maintain", kcal=forced_kcal)
            tripped.append(RAIL_ILLNESS_NO_DEFICIT)
            notes.append("illness active or in return ramp: phase forced to maintain, no deficit")

    if t.kcal < KCAL_FLOOR:
        notes.append(f"kcal {t.kcal} clamped to floor {KCAL_FLOOR}")
        t = replace(t, kcal=KCAL_FLOOR)
        tripped.append(RAIL_KCAL_FLOOR)
    if t.protein_g < PROTEIN_FLOOR_G:
        notes.append(f"protein {t.protein_g} g clamped to floor {PROTEIN_FLOOR_G} g")
        t = replace(t, protein_g=PROTEIN_FLOOR_G)
        tripped.append(RAIL_PROTEIN_FLOOR)
    if t.fat_g_min < FAT_FLOOR_G:
        notes.append(f"fat {t.fat_g_min} g clamped to floor {FAT_FLOOR_G} g")
        t = replace(t, fat_g_min=FAT_FLOOR_G)
        tripped.append(RAIL_FAT_FLOOR)

    # Change-rate limit applies to engine-authored changes. A user override is a
    # deliberate act and is not blocked by it (floors and the illness rule still apply).
    if (
        t.set_by == "engine"
        and ctx.last_change_on is not None
        and (t.effective_from - ctx.last_change_on).days < MIN_DAYS_BETWEEN_CHANGES
    ):
        reason = (
            f"{RAIL_CHANGE_RATE}: rejected, last change on {ctx.last_change_on.isoformat()} "
            f"is within {MIN_DAYS_BETWEEN_CHANGES} days"
        )
        return RailResult(target=None, tripped=(RAIL_CHANGE_RATE,), reason=reason)

    if ctx.previous is not None and t.set_by == "engine" and t.kcal == ctx.previous.kcal and t.steps == ctx.previous.steps \
            and t.phase == ctx.previous.phase and t.protein_g == ctx.previous.protein_g and t.fat_g_min == ctx.previous.fat_g_min:
        # Nothing actually changes; no row should be written.
        return RailResult(target=None, tripped=tuple(tripped), reason="no-op: proposed target equals the current one")

    reason = t.reason
    if tripped:
        reason = f"{t.reason} | {'; '.join(tripped)}: {'; '.join(notes)}"
        t = replace(t, reason=reason)
    return RailResult(target=t, tripped=tuple(tripped), reason=reason)

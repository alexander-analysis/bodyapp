"""Weekly target adjustment (spec 5.3). Runs Sunday night after the rollup and
emits at most one change, always through :func:`app.engine.guards.apply_rails`.

Rate is the trend-weight loss in % of body weight per week over the last
``REVIEW_WINDOW_DAYS`` days (positive = losing).

    cut:   < STALL  -> stalled  : -150 kcal OR +2000 steps (never both in one week)
           0.3-0.8  -> on track : no change
           > 1.0    -> too fast : +200 kcal   (any phase; this is also the rapid-loss rail)
    gain:  gain > 0.5 -> overshoot : -150 kcal

Bands between the named ones ("slow" 0.15-0.3, "fast" 0.8-1.0) are reported
but not acted on: the spec names the triggers, and the engine acts only on the
triggers it was given.

"14+ clean days" is read strictly: the whole review window must be free of
health-event rows and of any return ramp. That is what makes a rehydration
rebound after illness incapable of moving a target — the window that would
contain it is not eligible for review.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal, Sequence

from . import guards
from .guards import RailContext, RailResult, apply_rails
from .trend import TrendPoint, rate_pct_per_week
from .types import DayRow, HealthEvent, Target, UserProfile

REVIEW_WINDOW_DAYS = 14
MIN_WEIGH_INS_IN_WINDOW = 8  # a rate from fewer points is noise, and noise looks like a stall
MIN_SPAN_DAYS_IN_WINDOW = 7

STALL_RATE_PCT_WEEK = 0.15
ON_TRACK_MIN_PCT_WEEK = 0.3
ON_TRACK_MAX_PCT_WEEK = 0.8
TOO_FAST_PCT_WEEK = guards.MAX_LOSS_RATE_PCT_WEEK
GAIN_OVERSHOOT_PCT_WEEK = 0.5

STALL_KCAL_CUT = 150
STALL_STEP_INCREASE = 2000
TOO_FAST_KCAL_RAISE = 200
GAIN_OVERSHOOT_KCAL_CUT = 150

Assessment = Literal[
    "active_event",
    "window_not_clean",
    "insufficient_weigh_ins",
    "stalled",
    "slow",
    "on_track",
    "fast",
    "too_fast",
    "gain_ok",
    "gain_overshoot",
    "maintaining",
]
StallAction = Literal["kcal", "steps"]


@dataclass(frozen=True)
class ReviewContext:
    today: date
    profile: UserProfile
    current: Target
    trend: Sequence[TrendPoint]
    events: Sequence[HealthEvent] = ()
    days: Sequence[DayRow] = ()
    last_change_on: date | None = None
    previous_stall_action: StallAction | None = None
    maintenance_kcal: int | None = None


@dataclass(frozen=True)
class ReviewResult:
    assessment: Assessment
    rate_pct_week: float | None
    proposed: Target | None  # after rails; None means "no row to write"
    proposals: tuple[str, ...]  # things that need the user's confirmation (phase switch)
    reason: str
    rails_tripped: tuple[str, ...]
    stall_action: StallAction | None = None

    @property
    def changed(self) -> bool:
        return self.proposed is not None


def classify(rate: float, phase: str) -> Assessment:
    if rate > TOO_FAST_PCT_WEEK:
        return "too_fast"
    if phase == "cut":
        if rate < STALL_RATE_PCT_WEEK:
            return "stalled"
        if rate < ON_TRACK_MIN_PCT_WEEK:
            return "slow"
        if rate <= ON_TRACK_MAX_PCT_WEEK:
            return "on_track"
        return "fast"
    if phase == "gain":
        return "gain_overshoot" if -rate > GAIN_OVERSHOOT_PCT_WEEK else "gain_ok"
    return "maintaining"


def window_is_clean(ctx: ReviewContext, start: date, end: date) -> bool:
    """No event rows, no active event days, no ramp days anywhere in [start, end]."""
    for p in ctx.trend:
        if p.excluded and start <= p.day <= end:
            return False
    for d in ctx.days:
        if d.health_event_id is not None and start <= d.day <= end:
            return False
    cur = start
    while cur <= end:
        for e in ctx.events:
            if e.is_active_on(cur) or e.in_ramp_on(cur):
                return False
        cur += timedelta(days=1)
    return True


def _no_change(assessment: Assessment, rate: float | None, reason: str, proposals: Sequence[str] = ()) -> ReviewResult:
    return ReviewResult(assessment, rate, None, tuple(proposals), reason, ())


def weekly_review(ctx: ReviewContext) -> ReviewResult:
    today = ctx.today
    if any(e.is_active_on(today) for e in ctx.events):
        return _no_change("active_event", None, "no change: a health event is active")

    start = today - timedelta(days=REVIEW_WINDOW_DAYS - 1)
    if not window_is_clean(ctx, start, today):
        return _no_change(
            "window_not_clean", None,
            f"no change: the last {REVIEW_WINDOW_DAYS} days include health-event or return-ramp days",
        )

    pts = [p for p in ctx.trend if not p.excluded and start <= p.day <= today]
    if len(pts) < MIN_WEIGH_INS_IN_WINDOW:
        return _no_change(
            "insufficient_weigh_ins", None,
            f"no change: {len(pts)} weigh-ins in the last {REVIEW_WINDOW_DAYS} days, need {MIN_WEIGH_INS_IN_WINDOW}",
        )
    first, last = pts[0], pts[-1]
    if (last.day - first.day).days < MIN_SPAN_DAYS_IN_WINDOW:
        return _no_change("insufficient_weigh_ins", None, "no change: weigh-ins do not span a full week")

    rate = rate_pct_per_week(first, last)
    if rate is None:
        return _no_change("insufficient_weigh_ins", None, "no change: rate could not be computed")
    rate = round(rate, 3)

    cur = ctx.current
    phase = cur.phase
    assessment = classify(rate, phase)
    proposals = list(_phase_proposals(ctx, last.trend_kg))

    effective = today + timedelta(days=1)
    stall_action: StallAction | None = None
    proposed: Target | None = None
    rate_txt = f"trend {rate:+.2f}% BW/week over {(last.day - first.day).days} days ({first.trend_kg:.2f} -> {last.trend_kg:.2f} kg)"

    if assessment == "too_fast":
        proposed = replace(
            cur, effective_from=effective, set_by="engine",
            kcal=cur.kcal + TOO_FAST_KCAL_RAISE,
            reason=f"{guards.RAIL_RAPID_LOSS}: losing faster than {TOO_FAST_PCT_WEEK}% BW/week ({rate_txt}); +{TOO_FAST_KCAL_RAISE} kcal",
        )
    elif assessment == "stalled":
        stall_action = _choose_stall_action(ctx)
        if stall_action == "kcal":
            proposed = replace(
                cur, effective_from=effective, set_by="engine",
                kcal=cur.kcal - STALL_KCAL_CUT,
                reason=f"stalled: {rate_txt}, below {STALL_RATE_PCT_WEEK}% for {REVIEW_WINDOW_DAYS} days; -{STALL_KCAL_CUT} kcal",
            )
        else:
            proposed = replace(
                cur, effective_from=effective, set_by="engine",
                steps=cur.steps + STALL_STEP_INCREASE,
                reason=f"stalled: {rate_txt}, below {STALL_RATE_PCT_WEEK}% for {REVIEW_WINDOW_DAYS} days; +{STALL_STEP_INCREASE} steps",
            )
    elif assessment == "gain_overshoot":
        proposed = replace(
            cur, effective_from=effective, set_by="engine",
            kcal=cur.kcal - GAIN_OVERSHOOT_KCAL_CUT,
            reason=f"gain overshoot: gaining faster than {GAIN_OVERSHOOT_PCT_WEEK}% BW/week ({rate_txt}); -{GAIN_OVERSHOOT_KCAL_CUT} kcal",
        )

    if proposed is None:
        return ReviewResult(assessment, rate, None, tuple(proposals), f"no change: {assessment} ({rate_txt})", ())

    assert abs(proposed.kcal - cur.kcal) <= guards.MAX_TARGET_CHANGE_KCAL
    rails: RailResult = apply_rails(
        proposed,
        RailContext(
            today=today, events=ctx.events, maintenance_kcal=ctx.maintenance_kcal,
            previous=cur, last_change_on=ctx.last_change_on,
        ),
    )
    if rails.rejected:
        return ReviewResult(assessment, rate, None, tuple(proposals), rails.reason, rails.tripped, stall_action)
    return ReviewResult(assessment, rate, rails.target, tuple(proposals), rails.reason, rails.tripped, stall_action)


def _choose_stall_action(ctx: ReviewContext) -> StallAction:
    """Calories or steps, never both in one week. Alternate, and never propose a
    calorie cut that the floor would immediately clamp back."""
    if ctx.previous_stall_action == "kcal":
        return "steps"
    if ctx.current.kcal - STALL_KCAL_CUT < guards.KCAL_FLOOR:
        return "steps"
    return "kcal"


def _phase_proposals(ctx: ReviewContext, trend_kg: float) -> list[str]:
    goal = ctx.profile.goal_weight_kg
    if goal is None:
        return []
    if ctx.current.phase == "cut" and trend_kg <= goal:
        return [f"cut reached goal weight ({trend_kg:.1f} kg <= {goal:.1f} kg): confirm switch to maintain"]
    if ctx.current.phase == "gain" and trend_kg >= goal:
        return [f"gain reached goal weight ({trend_kg:.1f} kg >= {goal:.1f} kg): confirm switch to maintain"]
    return []


# --- initial target -------------------------------------------------------------
# The very first target is a formula too: Mifflin-St Jeor x activity, offset by
# phase. It goes through apply_rails like every other row.

INITIAL_CUT_DEFICIT_KCAL = 500
INITIAL_GAIN_SURPLUS_KCAL = 300
PROTEIN_G_PER_KG = {"cut": 2.0, "maintain": 1.8, "gain": 1.8}
FAT_G_PER_KG = 0.8


def initial_target(profile: UserProfile, weight_kg: float, phase: str, on: date, *, maintenance_kcal: float | None = None) -> Target:
    """Starting targets from the formula estimate (or a supplied maintenance figure).
    The caller must still pass the result through ``apply_rails``."""
    from .tdee import formula_tdee

    tdee = maintenance_kcal if maintenance_kcal is not None else formula_tdee(profile, weight_kg, on)
    offset = {"cut": -INITIAL_CUT_DEFICIT_KCAL, "maintain": 0, "gain": INITIAL_GAIN_SURPLUS_KCAL}[phase]
    kcal = int(round(tdee + offset))
    protein = int(round(PROTEIN_G_PER_KG[phase] * weight_kg))
    fat = int(round(FAT_G_PER_KG * weight_kg))
    basis = "supplied maintenance" if maintenance_kcal is not None else "Mifflin-St Jeor x 1.35"
    reason = (
        f"initial {phase} target: {basis} = {tdee:.0f} kcal {offset:+d}; "
        f"protein {PROTEIN_G_PER_KG[phase]} g/kg, fat {FAT_G_PER_KG} g/kg at {weight_kg:.1f} kg"
    )
    return Target(
        effective_from=on, kcal=kcal, protein_g=protein, fat_g_min=fat, phase=phase,  # type: ignore[arg-type]
        reason=reason, set_by="engine",
    )

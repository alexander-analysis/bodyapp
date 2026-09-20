"""Modes (spec 8): sick (mild / moderate / GI), exam, travel, injury, and the
post-illness return ramp. All share ``health_events``; only the rules differ.

When several apply at once, :func:`most_conservative` takes the safer value of
every field independently (spec 8.7). Fever forces at least moderate.

Modes never author numbers. They yield *rules*; the target engine turns
"maintenance x 1.05" into a kcal figure and the rails check it.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import date, timedelta
from typing import Literal, Sequence

from . import guards
from .types import Exercise, HealthEvent, Severity

Training = Literal["normal", "reduced", "blocked"]
Tolerance = Literal["normal", "relaxed"]

RAMP_MAX_DAYS = 7
RAMP_VOLUME_CAP = 0.75
EXAM_VOLUME_CAP = 2.0 / 3.0
SICK_MILD_VOLUME_CAP = 0.5


@dataclass(frozen=True)
class ModeRules:
    kcal_multiplier: float = 1.0  # relative to maintenance when force_maintenance is set
    force_maintenance: bool = False
    protein_held: bool = True
    training: Training = "normal"
    volume_cap: float = 1.0
    load_cap_last_session: bool = False
    progression_allowed: bool = True
    fibre_relaxed: bool = False
    hydration_raised: bool = False
    electrolyte_prompts: bool = False
    logging_strict: bool = True
    streaks_paused: bool = False
    plateau_alerts: bool = True
    rate_alerts: bool = True
    estimation_tolerance: Tolerance = "normal"
    restaurant_suggestions: bool = False
    guidance: tuple[str, ...] = ()


NORMAL = ModeRules()
SICK_MILD = ModeRules(
    force_maintenance=True, training="reduced", volume_cap=SICK_MILD_VOLUME_CAP,
    load_cap_last_session=True, progression_allowed=False, streaks_paused=True,
    plateau_alerts=False, rate_alerts=False, guidance=("above_the_neck",),
)
SICK_MODERATE = ModeRules(
    kcal_multiplier=1.05, force_maintenance=True, training="blocked", volume_cap=0.0,
    load_cap_last_session=True, progression_allowed=False, streaks_paused=True,
    plateau_alerts=False, rate_alerts=False, guidance=("rest_screen",),
)
SICK_GI = ModeRules(
    force_maintenance=True, training="blocked", volume_cap=0.0, load_cap_last_session=True,
    progression_allowed=False, fibre_relaxed=True, hydration_raised=True, electrolyte_prompts=True,
    streaks_paused=True, plateau_alerts=False, rate_alerts=False, guidance=("electrolytes", "rest_screen"),
)
EXAM = ModeRules(
    force_maintenance=True, training="reduced", volume_cap=EXAM_VOLUME_CAP,
    logging_strict=False, streaks_paused=True,
)
TRAVEL = ModeRules(
    plateau_alerts=False, rate_alerts=False, estimation_tolerance="relaxed",
    restaurant_suggestions=True, logging_strict=False,
)
INJURY = ModeRules()  # exercise-level, see affected_groups(); no blanket block
RAMP = ModeRules(
    force_maintenance=True, training="reduced", volume_cap=RAMP_VOLUME_CAP,
    load_cap_last_session=True, progression_allowed=False, plateau_alerts=False, rate_alerts=False,
    guidance=("return_ramp",),
)

_TRAINING_ORDER = {"normal": 0, "reduced": 1, "blocked": 2}

# Field -> how to pick the conservative value. "max"/"min" numeric, "or"/"and"
# boolean, "training"/"tolerance" ordered enums, "union" for tuples.
_POLICY = {
    "kcal_multiplier": "max",
    "force_maintenance": "or",
    "protein_held": "or",
    "training": "training",
    "volume_cap": "min",
    "load_cap_last_session": "or",
    "progression_allowed": "and",
    "fibre_relaxed": "or",
    "hydration_raised": "or",
    "electrolyte_prompts": "or",
    "logging_strict": "and",
    "streaks_paused": "or",
    "plateau_alerts": "and",
    "rate_alerts": "and",
    "estimation_tolerance": "tolerance",
    "restaurant_suggestions": "or",
    "guidance": "union",
}


def conservative_value(name: str, a: object, b: object) -> object:
    policy = _POLICY[name]
    if policy == "max":
        return max(a, b)  # type: ignore[type-var]
    if policy == "min":
        return min(a, b)  # type: ignore[type-var]
    if policy == "or":
        return bool(a) or bool(b)
    if policy == "and":
        return bool(a) and bool(b)
    if policy == "training":
        return a if _TRAINING_ORDER[a] >= _TRAINING_ORDER[b] else b  # type: ignore[index]
    if policy == "tolerance":
        return "relaxed" if "relaxed" in (a, b) else "normal"
    if policy == "union":
        return tuple(sorted({*a, *b}))  # type: ignore[misc]
    raise KeyError(name)


def most_conservative(a: ModeRules, b: ModeRules) -> ModeRules:
    return ModeRules(**{f.name: conservative_value(f.name, getattr(a, f.name), getattr(b, f.name)) for f in fields(ModeRules)})


def effective_severity(event: HealthEvent) -> Severity:
    """Fever forces at least moderate (spec 8.1)."""
    if event.type != "illness":
        return event.severity
    if event.fever_flag and event.severity in ("none", "mild"):
        return "moderate"
    return event.severity if event.severity != "none" else "mild"


def rules_for(event: HealthEvent) -> ModeRules:
    if event.type == "illness":
        sev = effective_severity(event)
        return {"mild": SICK_MILD, "moderate": SICK_MODERATE, "gi": SICK_GI}[sev]
    return {"exam": EXAM, "travel": TRAVEL, "injury": INJURY}[event.type]


def ramp_until(event: HealthEvent) -> date | None:
    """``ended_at + min(duration, RAMP_MAX_DAYS)`` for illnesses; None otherwise."""
    if event.type != "illness" or event.ended_at is None:
        return None
    return event.ended_at + timedelta(days=min(event.duration_days(), RAMP_MAX_DAYS))


@dataclass(frozen=True)
class ModeState:
    rules: ModeRules
    active: tuple[HealthEvent, ...]
    ramping: tuple[HealthEvent, ...]
    training_locked_by_fever: bool
    referrals: tuple[HealthEvent, ...]
    affected_groups: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        out = [f"{e.type}:{effective_severity(e)}" if e.type == "illness" else e.type for e in self.active]
        out += [f"ramp:{e.id}" for e in self.ramping]
        return tuple(out)

    @property
    def any_active(self) -> bool:
        return bool(self.active or self.ramping)


def resolve(events: Sequence[HealthEvent], today: date) -> ModeState:
    active = tuple(e for e in events if e.is_active_on(today))
    ramping = tuple(e for e in events if e.type == "illness" and e.in_ramp_on(today))

    rules = NORMAL
    for e in active:
        rules = most_conservative(rules, rules_for(e))
    for _ in ramping:
        rules = most_conservative(rules, RAMP)

    fever = guards.training_locked(events, today)
    if fever:
        rules = replace(rules, training="blocked", volume_cap=0.0, progression_allowed=False)
    referrals = tuple(guards.referral_due(events, today))
    if referrals:
        rules = replace(rules, guidance=tuple(sorted({*rules.guidance, "see_a_doctor"})))

    return ModeState(
        rules=rules, active=active, ramping=ramping, training_locked_by_fever=fever,
        referrals=referrals, affected_groups=affected_groups(active),
    )


def affected_groups(events: Sequence[HealthEvent]) -> tuple[str, ...]:
    """Muscle groups an active injury rules out, from ``symptoms_json.affected_groups``."""
    groups: list[str] = []
    for e in events:
        if e.type != "injury":
            continue
        for g in e.symptoms.get("affected_groups", ()):  # type: ignore[union-attr]
            if isinstance(g, str) and g not in groups:
                groups.append(g)
    return tuple(groups)


def exercise_is_affected(exercise: Exercise, groups: Sequence[str]) -> bool:
    return exercise.muscle_group in groups or any(g in groups for g in exercise.secondary_groups)

"""Double progression per exercise (spec 5.4), evaluated on the last clean session.

    if all working sets >= rep_max and mean RIR <= 2:
        next_weight = current + increment_kg ; next_reps = rep_min
    elif any working set < rep_min for two consecutive sessions:
        stalled (after 3 sessions: propose deload or swap)
    else:
        next_weight = current ; next_reps = current_reps + 1

"current_reps" is the lowest working-set rep count of the last session: the
lifter progresses when every set reaches the target. Sets with no RIR logged
are left out of the RIR mean; if no set has RIR, the RIR gate passes.

Sessions tagged with a health event are excluded (spec 8.2). Load caps and
progression locks from the active mode are applied on top by the caller via
``allow_progression`` / ``load_cap``.

Epley e1RM is for trend display only, never for prescription.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Sequence

from .exclusion import clean_rows
from .types import Exercise, SetRow, WorkoutRow

RIR_PROGRESS_MAX = 2
STALL_SESSIONS = 2
DELOAD_PROPOSAL_SESSIONS = 3
DELOAD_FACTOR = 0.9
DEFAULT_SETS = 3


@dataclass(frozen=True)
class Prescription:
    exercise_id: int
    weight_kg: float | None  # None: no history, pick a starting load
    target_reps: int
    sets: int
    stalled: bool = False
    stall_sessions: int = 0
    proposal: str | None = None  # "deload" / "swap" suggestions need the user's confirmation
    note: str = ""


@dataclass(frozen=True)
class SessionSummary:
    workout_id: int
    performed_on: object
    top_weight_kg: float
    min_reps: int
    max_reps: int
    mean_rir: float | None
    e1rm: float | None
    working_sets: int


def epley(weight_kg: float, reps: int) -> float:
    return weight_kg * (1.0 + reps / 30.0)


def working_sets(sets: Sequence[SetRow]) -> list[SetRow]:
    return [s for s in sets if not s.is_warmup]


def session_e1rm(sets: Sequence[SetRow]) -> float | None:
    ws = working_sets(sets)
    return max(epley(s.weight_kg, s.reps) for s in ws) if ws else None


def summarize(exercise_id: int, workouts: Sequence[WorkoutRow]) -> list[SessionSummary]:
    """Per-session summaries for one exercise, oldest first, clean sessions only."""
    out: list[SessionSummary] = []
    for w in sorted(clean_rows(workouts), key=lambda w: (w.performed_on, w.id)):
        ws = working_sets(w.sets_for(exercise_id))
        if not ws:
            continue
        rirs = [s.rir for s in ws if s.rir is not None]
        out.append(
            SessionSummary(
                workout_id=w.id,
                performed_on=w.performed_on,
                top_weight_kg=max(s.weight_kg for s in ws),
                min_reps=min(s.reps for s in ws),
                max_reps=max(s.reps for s in ws),
                mean_rir=fmean(rirs) if rirs else None,
                e1rm=session_e1rm(ws),
                working_sets=len(ws),
            )
        )
    return out


def e1rm_trend(exercise_id: int, workouts: Sequence[WorkoutRow]) -> list[tuple[object, float]]:
    return [(s.performed_on, round(s.e1rm, 1)) for s in summarize(exercise_id, workouts) if s.e1rm is not None]


def stall_count(exercise: Exercise, sessions: Sequence[SessionSummary]) -> int:
    """Consecutive most-recent sessions with any working set below rep_min."""
    n = 0
    for s in reversed(sessions):
        if s.min_reps < exercise.rep_min:
            n += 1
        else:
            break
    return n


def prescribe_next(
    exercise: Exercise,
    workouts: Sequence[WorkoutRow],
    *,
    allow_progression: bool = True,
    load_cap: bool = False,
) -> Prescription:
    sessions = summarize(exercise.id, workouts)
    if not sessions:
        return Prescription(exercise.id, None, exercise.rep_min, DEFAULT_SETS, note="no history: choose a starting load")

    last = sessions[-1]
    stalled_n = stall_count(exercise, sessions)

    if not allow_progression or load_cap:
        return Prescription(
            exercise.id, last.top_weight_kg, min(max(last.min_reps, exercise.rep_min), exercise.rep_max),
            last.working_sets, stalled=stalled_n >= STALL_SESSIONS, stall_sessions=stalled_n,
            note="progression paused by active mode: repeat last session's load",
        )

    rir_ok = last.mean_rir is None or last.mean_rir <= RIR_PROGRESS_MAX
    if last.min_reps >= exercise.rep_max and rir_ok:
        return Prescription(
            exercise.id, last.top_weight_kg + exercise.increment_kg, exercise.rep_min, last.working_sets,
            note=f"all sets at {exercise.rep_max}+ with RIR <= {RIR_PROGRESS_MAX}: +{exercise.increment_kg} kg, reps reset to {exercise.rep_min}",
        )

    if stalled_n >= STALL_SESSIONS:
        proposal = None
        note = f"stalled: below {exercise.rep_min} reps for {stalled_n} sessions; hold load, aim for {exercise.rep_min}"
        if stalled_n >= DELOAD_PROPOSAL_SESSIONS:
            deload = _round_to_increment(last.top_weight_kg * DELOAD_FACTOR, exercise.increment_kg)
            proposal = f"deload to {deload} kg or swap the exercise"
            note = f"stalled for {stalled_n} sessions: {proposal}"
        return Prescription(
            exercise.id, last.top_weight_kg, exercise.rep_min, last.working_sets,
            stalled=True, stall_sessions=stalled_n, proposal=proposal, note=note,
        )

    target = min(max(last.min_reps, exercise.rep_min - 1) + 1, exercise.rep_max)
    note = f"hold {last.top_weight_kg} kg, aim for {target} reps on every set"
    if last.min_reps >= exercise.rep_max and not rir_ok:
        note = f"reps at {exercise.rep_max} but mean RIR {last.mean_rir:.1f} > {RIR_PROGRESS_MAX}: repeat closer to failure before adding load"
    return Prescription(exercise.id, last.top_weight_kg, target, last.working_sets, stall_sessions=stalled_n, note=note)


def _round_to_increment(weight: float, increment: float) -> float:
    if increment <= 0:
        return round(weight, 1)
    return round(round(weight / increment) * increment, 2)

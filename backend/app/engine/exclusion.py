"""The analytics exclusion rule, in one place.

Any row tagged with a ``health_event_id`` (illness, injury, travel, exam) is
stored and displayed but never feeds trend weight, adaptive TDEE, adherence,
volume or progression. Every analytic in the engine goes through
``clean_rows``; the SQL side uses ``CLEAN_PREDICATE`` from ``app.db``. Audit
those two symbols and you have audited the rule.
"""
from __future__ import annotations

from typing import Iterable, Protocol, TypeVar


class HasEventTag(Protocol):
    health_event_id: int | None


T = TypeVar("T", bound=HasEventTag)


def clean_rows(rows: Iterable[T]) -> list[T]:
    """Rows that carry no health-event tag, in their original order."""
    return [r for r in rows if r.health_event_id is None]


def excluded_rows(rows: Iterable[T]) -> list[T]:
    """The complement of :func:`clean_rows` — for display, never for maths."""
    return [r for r in rows if r.health_event_id is not None]

"""Bounds every Gemini food row must pass before it can touch the database (spec 6).

The macro-consistency check (4p + 4c + 9f within 25% of kcal_100g) catches
most hallucinated nutrition rows. A small absolute tolerance keeps genuine
near-zero-kcal foods (water, diet drinks, black coffee) from failing on rounding.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .types import InputMethod

PortionBasis = Literal["reference_object", "plate_fraction", "none"]

BOUNDS = {
    "kcal_100g": (0.0, 900.0),
    "protein_100g": (0.0, 100.0),
    "carbs_100g": (0.0, 100.0),
    "fat_100g": (0.0, 100.0),
    "estimated_grams": (1.0, 2000.0),
}
MACRO_TOLERANCE = 0.25
LOW_KCAL_ABS_TOLERANCE = 15.0

# Confidence per input path (spec 7). Photo confidence comes from the model,
# clamped into its band: identification is good, portion is weak.
INPUT_CONFIDENCE: dict[InputMethod, float] = {"barcode": 0.95, "favorite": 0.90, "text": 0.60, "manual": 1.0}
PHOTO_CONFIDENCE_BAND = (0.35, 0.55)


@dataclass(frozen=True)
class FoodCandidate:
    name: str
    confidence: float
    kcal_100g: float
    protein_100g: float
    carbs_100g: float
    fat_100g: float
    estimated_grams: float | None = None
    portion_basis: PortionBasis = "none"


def macro_kcal(protein_g: float, carbs_g: float, fat_g: float) -> float:
    return 4.0 * protein_g + 4.0 * carbs_g + 9.0 * fat_g


def macros_consistent(kcal_100g: float, protein_100g: float, carbs_100g: float, fat_100g: float) -> bool:
    computed = macro_kcal(protein_100g, carbs_100g, fat_100g)
    tolerance = max(MACRO_TOLERANCE * kcal_100g, LOW_KCAL_ABS_TOLERANCE)
    return abs(computed - kcal_100g) <= tolerance


def validate_candidate(c: FoodCandidate) -> list[str]:
    """Empty list = accept. Otherwise the reasons to drop the item."""
    errors: list[str] = []
    for field_name, (lo, hi) in BOUNDS.items():
        value = getattr(c, field_name)
        if value is None:
            continue
        if not (lo <= value <= hi):
            errors.append(f"{field_name}={value} outside {lo}-{hi}")
    if not (0.0 <= c.confidence <= 1.0):
        errors.append(f"confidence={c.confidence} outside 0-1")
    if not c.name or not c.name.strip():
        errors.append("empty name")
    if not macros_consistent(c.kcal_100g, c.protein_100g, c.carbs_100g, c.fat_100g):
        errors.append(
            f"macro inconsistency: 4p+4c+9f={macro_kcal(c.protein_100g, c.carbs_100g, c.fat_100g):.0f} "
            f"vs kcal_100g={c.kcal_100g:.0f} (tolerance {MACRO_TOLERANCE:.0%})"
        )
    return errors


def input_confidence(method: InputMethod, model_confidence: float | None = None) -> float:
    if method == "photo":
        lo, hi = PHOTO_CONFIDENCE_BAND
        if model_confidence is None:
            return lo
        return min(hi, max(lo, model_confidence))
    return INPUT_CONFIDENCE[method]


def scale_entry(c: FoodCandidate, grams: float) -> dict[str, float]:
    """Per-100g candidate x confirmed grams -> the numbers written to food_entries."""
    f = grams / 100.0
    return {
        "grams": grams,
        "kcal": round(c.kcal_100g * f, 1),
        "protein_g": round(c.protein_100g * f, 1),
        "carbs_g": round(c.carbs_100g * f, 1),
        "fat_g": round(c.fat_100g * f, 1),
    }

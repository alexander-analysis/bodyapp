import pytest

from app.engine.validation import (
    FoodCandidate,
    input_confidence,
    macro_kcal,
    macros_consistent,
    scale_entry,
    validate_candidate,
)


def cand(**kw) -> FoodCandidate:
    base = dict(name="Chicken breast, grilled", confidence=0.8, kcal_100g=165.0, protein_100g=31.0,
                carbs_100g=0.0, fat_100g=3.6, estimated_grams=150.0, portion_basis="plate_fraction")
    base.update(kw)
    return FoodCandidate(**base)


def test_macro_inconsistent_row_is_rejected():
    """Spec 16: a Gemini row failing 4p + 4c + 9f within 25% is rejected."""
    bad = cand(kcal_100g=500.0, protein_100g=5.0, carbs_100g=5.0, fat_100g=5.0)  # macros say 85 kcal
    errors = validate_candidate(bad)
    assert len(errors) == 1 and "macro inconsistency" in errors[0]
    assert not macros_consistent(500.0, 5.0, 5.0, 5.0)


def test_consistent_row_is_accepted():
    assert validate_candidate(cand()) == []
    assert macro_kcal(31.0, 0.0, 3.6) == pytest.approx(156.4)
    # exactly at the 25% edge passes, just beyond fails
    assert macros_consistent(100.0, 0.0, 31.25, 0.0)
    assert not macros_consistent(100.0, 0.0, 31.3, 0.0)


def test_near_zero_kcal_foods_survive_rounding():
    assert validate_candidate(cand(name="Water", kcal_100g=0.0, protein_100g=0.0, carbs_100g=0.0, fat_100g=0.0)) == []
    assert validate_candidate(cand(name="Diet cola", kcal_100g=1.0, protein_100g=0.0, carbs_100g=0.1, fat_100g=0.0)) == []
    assert validate_candidate(cand(name="Black coffee", kcal_100g=2.0, protein_100g=0.1, carbs_100g=0.0, fat_100g=0.0)) == []


def test_bounds():
    assert any("kcal_100g" in e for e in validate_candidate(cand(kcal_100g=950.0, fat_100g=100.0)))
    assert any("estimated_grams" in e for e in validate_candidate(cand(estimated_grams=0.0)))
    assert any("estimated_grams" in e for e in validate_candidate(cand(estimated_grams=2500.0)))
    assert any("protein_100g" in e for e in validate_candidate(cand(protein_100g=120.0, kcal_100g=480.0)))
    assert any("confidence" in e for e in validate_candidate(cand(confidence=1.2)))
    assert any("empty name" in e for e in validate_candidate(cand(name="  ")))
    assert validate_candidate(cand(estimated_grams=None)) == []  # no size reference visible


def test_input_confidence_per_path():
    assert input_confidence("barcode") == 0.95
    assert input_confidence("favorite") == 0.90
    assert input_confidence("text") == 0.60
    assert input_confidence("manual") == 1.0
    assert input_confidence("photo", 0.9) == 0.55
    assert input_confidence("photo", 0.1) == 0.35
    assert input_confidence("photo", 0.45) == 0.45
    assert input_confidence("photo", None) == 0.35


def test_scale_entry():
    assert scale_entry(cand(), 150.0) == {"grams": 150.0, "kcal": 247.5, "protein_g": 46.5, "carbs_g": 0.0, "fat_g": 5.4}

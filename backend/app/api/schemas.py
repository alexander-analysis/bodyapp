"""Request bodies. Responses are plain dicts built in ``services``."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ProfileIn(Body):
    name: str = Field(min_length=1, max_length=80)
    sex: Literal["m", "f"]
    birth_date: date
    height_cm: float = Field(gt=100, lt=250)
    goal_weight_kg: float | None = Field(default=None, gt=30, lt=300)
    timezone: str = "Europe/Madrid"
    start_weight_kg: float | None = Field(default=None, ge=30, le=300)
    phase: Literal["cut", "maintain", "gain"] = "cut"


class TargetOverrideIn(Body):
    reason: str = Field(default="", max_length=300)
    kcal: int | None = Field(default=None, ge=800, le=6000)
    protein_g: int | None = Field(default=None, ge=0, le=500)
    fat_g_min: int | None = Field(default=None, ge=0, le=400)
    fibre_g: int | None = Field(default=None, ge=0, le=120)
    steps: int | None = Field(default=None, ge=0, le=60000)
    phase: Literal["cut", "maintain", "gain"] | None = None


class WeightIn(Body):
    day: date | None = None
    weight_kg: float = Field(ge=30, le=300)
    waist_cm: float | None = Field(default=None, gt=30, lt=250)


class FoodIn(Body):
    name: str = Field(min_length=1, max_length=120)
    brand: str | None = Field(default=None, max_length=80)
    kcal_100g: float = Field(ge=0, le=900)
    protein_100g: float = Field(ge=0, le=100)
    carbs_100g: float = Field(ge=0, le=100)
    fat_100g: float = Field(ge=0, le=100)
    fibre_100g: float = Field(default=0, ge=0, le=100)
    barcode: str | None = Field(default=None, min_length=6, max_length=20, pattern=r"^\d+$")
    save: bool = True


class MacrosIn(Body):
    kcal: float = Field(ge=0, le=6000)
    protein_g: float = Field(ge=0, le=600)
    carbs_g: float = Field(ge=0, le=1500)
    fat_g: float = Field(ge=0, le=600)
    fibre_g: float = Field(default=0, ge=0, le=200)
    alcohol_g: float = Field(default=0, ge=0, le=300)


class EntryIn(Body):
    day: date | None = None
    logged_at: datetime | None = None
    meal: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    grams: float = Field(gt=0, le=5000)
    food_id: int | None = None
    food: FoodIn | None = None
    macros: MacrosIn | None = None
    input_method: Literal["barcode", "photo", "text", "favorite", "manual"] = "manual"
    confidence: float | None = Field(default=None, ge=0, le=1)
    photo_path: str | None = None


class FavoriteItemIn(Body):
    food_id: int | None = None
    macros: MacrosIn | None = None
    label: str | None = None
    grams: float = Field(gt=0, le=5000)


class FavoriteIn(Body):
    label: str = Field(min_length=1, max_length=80)
    items: list[FavoriteItemIn] = Field(min_length=1)


class LogFavoriteIn(Body):
    day: date | None = None
    meal: Literal["breakfast", "lunch", "dinner", "snack"] | None = None
    scale: float = Field(default=1.0, gt=0, le=5)


class SetIn(Body):
    exercise_id: int
    set_index: int | None = None
    weight_kg: float = Field(ge=0, le=1000)
    reps: int = Field(ge=0, le=200)
    rir: int | None = Field(default=None, ge=0, le=10)
    is_warmup: bool = False


class WorkoutIn(Body):
    performed_on: date | None = None
    template: str | None = Field(default=None, max_length=60)
    duration_min: int | None = Field(default=None, ge=0, le=600)
    rpe: int | None = Field(default=None, ge=1, le=10)
    notes: str | None = Field(default=None, max_length=2000)
    sets: list[SetIn] = Field(min_length=1)


class ExerciseIn(Body):
    name: str = Field(min_length=1, max_length=80)
    muscle_group: str = Field(min_length=1, max_length=40)
    secondary_groups: list[str] = []
    tier: int = Field(default=2, ge=1, le=3)
    increment_kg: float = Field(default=2.5, gt=0, le=50)
    rep_min: int = Field(default=8, ge=1, le=50)
    rep_max: int = Field(default=12, ge=1, le=100)


class ExercisePatch(Body):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    muscle_group: str | None = None
    secondary_groups: list[str] | None = None
    tier: int | None = Field(default=None, ge=1, le=3)
    increment_kg: float | None = Field(default=None, gt=0, le=50)
    rep_min: int | None = Field(default=None, ge=1, le=50)
    rep_max: int | None = Field(default=None, ge=1, le=100)


class TemplateItemIn(Body):
    exercise_id: int
    sets: int = Field(default=3, ge=1, le=10)


class TemplateIn(Body):
    name: str = Field(min_length=1, max_length=60)
    slot: int = Field(ge=1, le=14)
    exercises: list[TemplateItemIn] = Field(min_length=1)
    active: bool = True


class DayMetricsIn(Body):
    steps: int | None = Field(default=None, ge=0, le=100000)
    water_ml: int | None = Field(default=None, ge=0, le=20000)
    sleep_h: float | None = Field(default=None, ge=0, le=24)
    logged_complete: bool | None = None


class EventIn(Body):
    type: Literal["illness", "injury", "travel", "exam"]
    severity: Literal["mild", "moderate", "gi", "none"] = "none"
    fever_flag: bool = False
    symptoms: dict | None = None
    started_at: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


class EventPatch(Body):
    severity: Literal["mild", "moderate", "gi", "none"] | None = None
    fever_flag: bool | None = None
    symptoms: dict | None = None
    notes: str | None = Field(default=None, max_length=1000)
    ended_at: date | None = None
    end_now: bool = False

// Every write in the app goes through here: queue first, flush immediately, never block.
import { enqueue, type OpPreview } from "./outbox";
import type { Macros, Meal } from "./types";

export interface EntryBody {
  day?: string;
  meal: Meal | null;
  grams: number;
  food_id?: number;
  food?: { name: string; brand?: string | null; kcal_100g: number; protein_100g: number; carbs_100g: number; fat_100g: number; fibre_100g?: number; save?: boolean; barcode?: string };
  macros?: Macros;
  input_method: "barcode" | "photo" | "text" | "favorite" | "manual";
  confidence?: number;
  photo_path?: string | null;
}

export function previewFor(body: EntryBody, label: string): OpPreview {
  const f = body.grams / 100;
  let kcal = 0, protein_g = 0, carbs_g = 0, fat_g = 0, fibre_g = 0;
  if (body.macros) ({ kcal, protein_g, carbs_g, fat_g } = body.macros), (fibre_g = body.macros.fibre_g ?? 0);
  else if (body.food) {
    kcal = body.food.kcal_100g * f;
    protein_g = body.food.protein_100g * f;
    carbs_g = body.food.carbs_100g * f;
    fat_g = body.food.fat_100g * f;
    fibre_g = (body.food.fibre_100g ?? 0) * f;
  }
  return { kind: "entry", label, day: body.day, meal: body.meal, grams: body.grams, kcal, protein_g, carbs_g, fat_g, fibre_g };
}

export const logEntry = (body: EntryBody, preview: OpPreview) => enqueue({ method: "POST", path: "/api/v1/food/entry", body, preview });

export const logFoodById = (foodId: number, grams: number, meal: Meal, preview: OpPreview) =>
  enqueue({ method: "POST", path: "/api/v1/food/entry", body: { food_id: foodId, grams, meal, input_method: "manual" } satisfies EntryBody, preview });

export const deleteEntry = (id: number) =>
  enqueue({ method: "DELETE", path: `/api/v1/food/entry/${id}`, preview: { kind: "other", label: "Remove entry" } });

export const logFavorite = (favId: number, label: string, meal: Meal, scale: number, kcal: number) =>
  enqueue({ method: "POST", path: `/api/v1/food/favorites/${favId}/log`, body: { meal, scale }, preview: { kind: "entry", label, meal, kcal } });

export const saveFavorite = (label: string, items: { food_id?: number; macros?: Macros; label?: string; grams: number }[]) =>
  enqueue({ method: "POST", path: "/api/v1/food/favorites", body: { label, items }, preview: { kind: "other", label: `Save favorite “${label}”` } });

export const deleteFavorite = (id: number) =>
  enqueue({ method: "DELETE", path: `/api/v1/food/favorites/${id}`, preview: { kind: "other", label: "Remove favorite" } });

export const logWeight = (weight_kg: number, waist_cm: number | null, day?: string) =>
  enqueue({ method: "POST", path: "/api/v1/weight", body: { weight_kg, waist_cm, day }, preview: { kind: "weight", label: `${weight_kg.toFixed(1)} kg`, day } });

export const patchDay = (day: string, body: { steps?: number; water_ml?: number; sleep_h?: number; logged_complete?: boolean }) =>
  enqueue({ method: "PATCH", path: `/api/v1/day/${day}`, body, preview: { kind: "other", label: "Day metrics", day } });

export const logWorkout = (body: {
  performed_on?: string;
  template?: string | null;
  duration_min?: number | null;
  rpe?: number | null;
  notes?: string | null;
  sets: { exercise_id: number; set_index?: number; weight_kg: number; reps: number; rir?: number | null; is_warmup?: boolean }[];
}) => enqueue({ method: "POST", path: "/api/v1/workout", body, preview: { kind: "workout", label: `${body.template ?? "Workout"} · ${body.sets.length} sets` } });

export const deleteWorkout = (id: number) =>
  enqueue({ method: "DELETE", path: `/api/v1/workout/${id}`, preview: { kind: "other", label: "Remove workout" } });

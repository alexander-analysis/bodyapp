// Shapes of the API payloads the screens use. Kept loose where the server returns raw rows.

export type Meal = "breakfast" | "lunch" | "dinner" | "snack";
export type Phase = "cut" | "maintain" | "gain";

export interface Target {
  id: number;
  effective_from: string;
  kcal: number;
  protein_g: number;
  fat_g_min: number;
  fibre_g: number;
  steps: number;
  phase: Phase;
  reason: string;
  set_by: "engine" | "user";
  rails_tripped?: string[];
  held?: boolean;
  held_by?: string[];
  held_reason?: string;
  stored_kcal?: number;
  stored_phase?: Phase;
}

export interface Profile {
  id: number;
  name: string;
  sex: "m" | "f";
  birth_date: string;
  height_cm: number;
  goal_weight_kg: number | null;
  timezone: string;
}

export interface Entry {
  id: number;
  food_id: number | null;
  food_name: string | null;
  food_brand: string | null;
  logged_at: string;
  logged_on: string;
  meal: Meal | null;
  grams: number;
  kcal: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  fibre_g: number | null;
  input_method: string;
  confidence: number;
  health_event_id: number | null;
}

export interface Food {
  id: number;
  name: string;
  brand: string | null;
  kcal_100g: number;
  protein_100g: number;
  carbs_100g: number;
  fat_100g: number;
  fibre_100g: number | null;
  source: string;
  verified: number;
  use_count?: number;
}

export interface Macros {
  kcal: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  fibre_g?: number;
  alcohol_g?: number;
}

export interface Favorite {
  id: number;
  label: string;
  items: { food_id?: number; macros?: Macros; label?: string; grams: number }[];
  use_count: number;
  last_used: string | null;
}

export interface Suggestion {
  food_id: number;
  name: string;
  brand: string | null;
  grams: number;
  times: number;
}

export interface ModeInfo {
  names: string[];
  training: "normal" | "reduced" | "blocked";
  force_maintenance: boolean;
  kcal_multiplier: number;
  volume_cap: number;
  progression_allowed: boolean;
  logging_strict: boolean;
  guidance: string[];
  fever_lock: boolean;
  referral_due: number[];
  affected_groups: string[];
}

export interface Prescription {
  exercise: { id: number; name: string; muscle_group: string; rep_min: number; rep_max: number; increment_kg: number };
  weight_kg: number | null;
  target_reps: number;
  sets: number;
  stalled: boolean;
  stall_sessions: number;
  proposal: string | null;
  note: string;
  omitted: boolean;
  omit_reason: string | null;
}

export interface NextSession {
  template: string | null;
  exercises: Prescription[];
  blocked: boolean;
  note: string | null;
  mode: ModeInfo;
}

export interface TdeeInfo {
  tdee_kcal: number;
  method: "formula" | "adaptive";
  confidence: number;
  adaptive_kcal: number | null;
  formula_kcal: number;
  complete_days: number;
  notes: string[];
}

export interface Today {
  day: string;
  profile: Profile | null;
  target: Target | null;
  consumed: { kcal: number; protein_g: number; carbs_g: number; fat_g: number; fibre_g: number; alcohol_g: number };
  remaining: { kcal: number; protein_g: number; fat_g: number; fibre_g: number } | null;
  entries: Entry[];
  day_metrics: { steps: number | null; water_ml: number | null; sleep_h: number | null; logged_complete: boolean };
  weight: { latest: { logged_on: string; weight_kg: number; waist_cm: number | null }; trend_kg: number | null } | null;
  tdee: TdeeInfo | null;
  mode: ModeInfo;
  next_session: NextSession | null;
  gemini_enabled: boolean;
  llm: { calls_today: number; ok_today: number; cap: number; cap_reached: boolean; error_rate_24h: number; calls_24h: number } | null;
  scope: string;
  version: string;
}

export interface TrendPoint {
  day: string;
  raw_kg: number;
  trend_kg: number;
  excluded: boolean;
}

export interface TrendPayload {
  days: number;
  points: TrendPoint[];
  bands: { event_id: number; type: string; severity: string; start: string; end: string; ramp_until: string | null }[];
  rate_pct_week: number | null;
  latest: { logged_on: string; weight_kg: number; waist_cm: number | null } | null;
  trend_kg: number | null;
}

export interface Exercise {
  id: number;
  name: string;
  muscle_group: string;
  secondary_groups: string | null;
  tier: number;
  increment_kg: number;
  rep_min: number;
  rep_max: number;
}

export interface Template {
  id: number;
  name: string;
  slot: number;
  exercises: { exercise_id: number; sets: number }[];
  active: number;
}

export interface WorkoutSet {
  id?: number;
  exercise_id: number;
  exercise_name?: string;
  set_index: number;
  weight_kg: number;
  reps: number;
  rir: number | null;
  is_warmup: boolean | number;
}

export interface Workout {
  id: number;
  performed_on: string;
  template: string | null;
  duration_min: number | null;
  rpe: number | null;
  notes: string | null;
  health_event_id: number | null;
  sets: WorkoutSet[];
}

export interface History {
  exercise: Exercise & { secondary_groups: string[] };
  sessions: { workout_id: number; performed_on: string; top_weight_kg: number; min_reps: number; max_reps: number; mean_rir: number | null; e1rm: number | null; working_sets: number }[];
  e1rm_trend: { day: string; e1rm: number }[];
  excluded_sessions: number;
}

export interface Volume {
  as_of: string;
  window_days: number;
  band: { low: number; high: number; cap: number };
  groups: { muscle_group: string; sets: number; flag: "low" | "ok" | "high" }[];
}

export interface Summary {
  week: { start: string; end: string };
  target: Target | null;
  adherence: { days_considered: number; kcal_hit_pct: number | null; protein_hit_pct: number | null; steps_hit_pct: number | null; mean_kcal: number | null; mean_protein_g: number | null; mean_confidence: number | null } | null;
  days_logged: number;
  days_excluded: number;
  trend: { start_kg: number | null; end_kg: number | null; change_kg: number | null; rate_pct_week: number | null };
  tdee: { tdee_kcal: number; method: string; confidence: number; notes: string[] } | null;
  volume: Volume;
  events: { id: number; type: string; severity: string; started_at: string; ended_at: string | null }[];
  target_changes: Target[];
  estimates_flagged: boolean;
  narrative: { text: string; source: "gemini" | "template"; generated_on: string; error?: string } | null;
}

export interface Health {
  status: string;
  version: string;
  db: string;
  schema: string | null;
  gemini: string;
  backup_newest_age_h: number | null;
}

export interface Review {
  id: number;
  reviewed_on: string;
  assessment: string;
  rate_pct_week: number | null;
  reason: string;
  proposals: string[];
  rails: string[];
  target_id: number | null;
  triggered_by: "job" | "user";
  target?: Target | null;
}

export interface TdeeRow {
  id: number;
  computed_on: string;
  window_days: number;
  tdee_kcal: number;
  confidence: number;
  method: "formula" | "adaptive";
}

export interface ReviewPayload {
  latest: Review | null;
  history: Review[];
  tdee_history: TdeeRow[];
}

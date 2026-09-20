import { useState } from "react";
import { MEALS, guessMeal } from "../lib/format";
import type { Meal } from "../lib/types";

// Spec 7: portion confirmation is mandatory, and presets beat asking for grams.
const PRESETS: { label: string; hint: string; grams: number }[] = [
  { label: "Palm", hint: "~100 g protein", grams: 100 },
  { label: "Fist", hint: "~150 g carbs", grams: 150 },
  { label: "Thumb", hint: "~15 g fat", grams: 15 },
  { label: "¼ plate", hint: "~125 g", grams: 125 },
  { label: "½ plate", hint: "~250 g", grams: 250 },
  { label: "Full plate", hint: "~400 g", grams: 400 },
];

export function PortionPicker({
  initialGrams,
  per100,
  onConfirm,
  confirmLabel = "Log it",
  busy = false,
}: {
  initialGrams: number;
  per100?: { kcal_100g: number; protein_100g: number } | null;
  onConfirm: (grams: number, meal: Meal) => void;
  confirmLabel?: string;
  busy?: boolean;
}) {
  const [grams, setGrams] = useState<number>(initialGrams);
  const [meal, setMeal] = useState<Meal>(guessMeal());
  const valid = grams > 0 && grams <= 5000;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-2">
        {PRESETS.map((p) => (
          <button key={p.label} type="button" onClick={() => setGrams(p.grams)} className={`btn py-2 flex-col ${grams === p.grams ? "btn-primary" : "btn-ghost"}`}>
            <span>{p.label}</span>
            <span className="text-[11px] opacity-70">{p.hint}</span>
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input type="number" inputMode="decimal" min={1} max={5000} step={5} value={grams || ""} onChange={(e) => setGrams(Number(e.target.value))} aria-label="grams" />
        <span className="text-muted">g</span>
      </div>
      {per100 && valid && (
        <div className="text-sm text-muted tabular">
          ≈ <span className="text-ink">{Math.round((per100.kcal_100g * grams) / 100)}</span> kcal · <span className="text-ink">{((per100.protein_100g * grams) / 100).toFixed(0)}</span> g protein
        </div>
      )}
      <div className="grid grid-cols-4 gap-2">
        {MEALS.map((m) => (
          <button key={m} type="button" onClick={() => setMeal(m)} className={`btn py-2 text-sm capitalize ${meal === m ? "btn-primary" : "btn-ghost"}`}>
            {m}
          </button>
        ))}
      </div>
      <button type="button" className="btn btn-primary w-full" disabled={!valid || busy} onClick={() => onConfirm(grams, meal)}>
        {confirmLabel}
      </button>
    </div>
  );
}

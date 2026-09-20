import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { PortionPicker } from "../components/PortionPicker";
import { Sheet } from "../components/Sheet";
import { deleteFavorite, logEntry, logFavorite, logFoodById, previewFor, saveFavorite, type EntryBody } from "../lib/actions";
import { get } from "../lib/api";
import { MEALS, guessMeal, kcal as fmtKcal } from "../lib/format";
import { useFavorites, useToday } from "../lib/hooks";
import type { Favorite, Food, Meal, Suggestion } from "../lib/types";

// Tab order per spec 11: camera, barcode, favorites, text — then manual as the always-works fallback.
const TABS = ["camera", "barcode", "favorites", "search", "manual"] as const;
type Tab = (typeof TABS)[number];

export function Log() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS as readonly string[]).includes(params.get("tab") ?? "") ? (params.get("tab") as Tab) : "favorites";
  const navigate = useNavigate();
  const today = useToday();
  const gemini = today.data?.gemini_enabled ?? false;

  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });
  const done = () => navigate("/");

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Log food</h1>
      <div className="grid grid-cols-5 gap-1 text-xs">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`rounded-xl py-2 capitalize ${tab === t ? "bg-accent text-black font-medium" : "bg-panel-2 text-muted"}`}>
            {t}
          </button>
        ))}
      </div>
      {tab === "camera" && <ComingSoon what="Photo identification" when="milestone 7" gemini={gemini} onFallback={() => setTab("search")} />}
      {tab === "barcode" && <ComingSoon what="Barcode scanning" when="milestone 6" onFallback={() => setTab("search")} />}
      {tab === "favorites" && <FavoritesTab onDone={done} />}
      {tab === "search" && <SearchTab onDone={done} gemini={gemini} />}
      {tab === "manual" && <ManualTab onDone={done} />}
    </div>
  );
}

function ComingSoon({ what, when, gemini, onFallback }: { what: string; when: string; gemini?: boolean; onFallback: () => void }) {
  return (
    <div className="card text-sm space-y-3">
      <p className="text-muted">{what} arrives in {when}.{gemini === false && what.startsWith("Photo") && " It also needs a Gemini key in the Pi's .env."}</p>
      <button className="btn btn-ghost w-full" onClick={onFallback}>Use search instead</button>
    </div>
  );
}

// --- favorites -----------------------------------------------------------------

function FavoritesTab({ onDone }: { onDone: () => void }) {
  const q = useFavorites();
  const [picked, setPicked] = useState<Favorite | null>(null);
  const [scale, setScale] = useState(1);
  const [meal, setMeal] = useState<Meal>(guessMeal());
  if (q.isLoading) return <p className="text-muted text-sm">Loading…</p>;
  const favs = q.data?.favorites ?? [];
  const suggestions = q.data?.suggestions ?? [];
  const totalKcal = (f: Favorite) => f.items.reduce((s, it) => s + (it.macros?.kcal ?? 0), 0);
  return (
    <div className="space-y-3">
      {favs.length === 0 && <div className="card text-sm text-muted">No favorites yet. After you log the same food three times, it will be offered here. Two taps, no thinking.</div>}
      <ul className="space-y-2">
        {favs.map((f) => (
          <li key={f.id} className="card flex items-center gap-3 py-3">
            <button className="flex-1 text-left" onClick={() => { setPicked(f); setScale(1); setMeal(guessMeal()); }}>
              <div className="font-medium">{f.label}</div>
              <div className="text-xs text-muted">{f.items.length} item{f.items.length === 1 ? "" : "s"} · used {f.use_count}×</div>
            </button>
            <button className="text-muted px-2" aria-label="remove favorite" onClick={() => { if (confirm(`Remove favorite “${f.label}”?`)) void deleteFavorite(f.id); }}>✕</button>
          </li>
        ))}
      </ul>
      {suggestions.length > 0 && <SuggestionList suggestions={suggestions} />}
      <Sheet open={picked != null} onClose={() => setPicked(null)} title={picked?.label}>
        {picked && (
          <div className="space-y-4">
            <ul className="text-sm text-muted">
              {picked.items.map((it, i) => (
                <li key={i} className="flex justify-between py-1"><span>{it.label ?? `food #${it.food_id}`}</span><span className="tabular">{Math.round(it.grams * scale)} g</span></li>
              ))}
            </ul>
            <div className="grid grid-cols-4 gap-2">
              {[0.5, 1, 1.5, 2].map((s) => (
                <button key={s} className={`btn py-2 ${scale === s ? "btn-primary" : "btn-ghost"}`} onClick={() => setScale(s)}>×{s}</button>
              ))}
            </div>
            <div className="grid grid-cols-4 gap-2">
              {MEALS.map((m) => (
                <button key={m} className={`btn py-2 text-sm capitalize ${meal === m ? "btn-primary" : "btn-ghost"}`} onClick={() => setMeal(m)}>{m}</button>
              ))}
            </div>
            <button className="btn btn-primary w-full" onClick={() => { void logFavorite(picked.id, picked.label, meal, scale, totalKcal(picked) * scale); setPicked(null); onDone(); }}>Log it</button>
          </div>
        )}
      </Sheet>
    </div>
  );
}

function SuggestionList({ suggestions }: { suggestions: Suggestion[] }) {
  return (
    <div className="card space-y-2">
      <div className="label">logged 3+ times — save as a favorite?</div>
      {suggestions.map((s) => (
        <div key={s.food_id} className="flex items-center gap-3 text-sm">
          <div className="flex-1"><span>{s.name}</span> <span className="text-muted">· {s.grams} g · {s.times}×</span></div>
          <button className="btn btn-ghost py-1.5 px-3 text-sm" onClick={() => void saveFavorite(s.name, [{ food_id: s.food_id, grams: s.grams, label: s.name }])}>Save</button>
        </div>
      ))}
    </div>
  );
}

// --- search --------------------------------------------------------------------

function SearchTab({ onDone, gemini }: { onDone: () => void; gemini: boolean }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Food[]>([]);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState<Food | null>(null);
  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return; }
    const h = setTimeout(() => {
      setSearching(true);
      get<{ results: Food[] }>(`/api/v1/food/search?q=${encodeURIComponent(q.trim())}`)
        .then((r) => setResults(r.results))
        .catch(() => setResults([]))
        .finally(() => setSearching(false));
    }, 250);
    return () => clearTimeout(h);
  }, [q]);
  return (
    <div className="space-y-3">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search foods you've logged (e.g. chicken, oats)" autoFocus />
      {!gemini && q.trim().length >= 2 && results.length === 0 && !searching && (
        <div className="card text-sm text-muted">Nothing saved under “{q}”. Add it once in <b className="text-ink">Manual</b> (per 100 g) and it will be here next time. Free-text parsing with Gemini arrives in milestone 7.</div>
      )}
      <ul className="space-y-2">
        {results.map((f) => (
          <li key={f.id}>
            <button className="card w-full text-left flex items-center gap-3 py-3" onClick={() => setPicked(f)}>
              <div className="flex-1 min-w-0">
                <div className="truncate">{f.name}{f.brand && <span className="text-muted"> · {f.brand}</span>}</div>
                <div className="text-xs text-muted tabular">{Math.round(f.kcal_100g)} kcal · P {f.protein_100g} · C {f.carbs_100g} · F {f.fat_100g} per 100 g</div>
              </div>
              <span className="text-accent text-sm">pick</span>
            </button>
          </li>
        ))}
      </ul>
      <Sheet open={picked != null} onClose={() => setPicked(null)} title={picked?.name}>
        {picked && (
          <PortionPicker
            initialGrams={100}
            per100={picked}
            onConfirm={(grams, meal) => {
              const body: EntryBody = { food_id: picked.id, grams, meal, input_method: "manual" };
              const preview = previewFor({ ...body, food: { name: picked.name, kcal_100g: picked.kcal_100g, protein_100g: picked.protein_100g, carbs_100g: picked.carbs_100g, fat_100g: picked.fat_100g, fibre_100g: picked.fibre_100g ?? 0 } }, picked.name);
              void logFoodById(picked.id, grams, meal, preview);
              setPicked(null);
              onDone();
            }}
          />
        )}
      </Sheet>
    </div>
  );
}

// --- manual --------------------------------------------------------------------

function ManualTab({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"per100" | "totals">("per100");
  const [name, setName] = useState("");
  const [grams, setGrams] = useState("100");
  const [kcal, setKcal] = useState("");
  const [protein, setProtein] = useState("");
  const [carbs, setCarbs] = useState("");
  const [fat, setFat] = useState("");
  const [fibre, setFibre] = useState("");
  const [meal, setMeal] = useState<Meal>(guessMeal());
  const [save, setSave] = useState(true);
  const n = (s: string) => (s.trim() === "" ? 0 : Number(s));
  const gramsN = n(grams);
  const macroKcal = 4 * n(protein) + 4 * n(carbs) + 9 * n(fat);
  const consistent = mode === "totals" || n(kcal) === 0 ? true : Math.abs(macroKcal - n(kcal)) <= Math.max(0.25 * n(kcal), 15);
  const valid = gramsN > 0 && kcal.trim() !== "" && (mode === "totals" || (name.trim() !== "" && consistent));

  const submit = () => {
    let body: EntryBody;
    if (mode === "per100") {
      body = { meal, grams: gramsN, input_method: "manual", food: { name: name.trim(), kcal_100g: n(kcal), protein_100g: n(protein), carbs_100g: n(carbs), fat_100g: n(fat), fibre_100g: n(fibre), save } };
    } else {
      body = { meal, grams: gramsN, input_method: "manual", macros: { kcal: n(kcal), protein_g: n(protein), carbs_g: n(carbs), fat_g: n(fat), fibre_g: n(fibre) } };
    }
    void logEntry(body, previewFor(body, name.trim() || "Manual entry"));
    onDone();
  };

  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid) submit(); }}>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <button type="button" className={`btn py-2 ${mode === "per100" ? "btn-primary" : "btn-ghost"}`} onClick={() => setMode("per100")}>Per 100 g (reusable)</button>
        <button type="button" className={`btn py-2 ${mode === "totals" ? "btn-primary" : "btn-ghost"}`} onClick={() => setMode("totals")}>Totals (one-off)</button>
      </div>
      {mode === "per100" && <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Food name" />}
      <div className="grid grid-cols-2 gap-2">
        <Field label="grams eaten" value={grams} set={setGrams} />
        <Field label={mode === "per100" ? "kcal / 100 g" : "kcal total"} value={kcal} set={setKcal} />
        <Field label={`protein g${mode === "per100" ? " / 100 g" : ""}`} value={protein} set={setProtein} />
        <Field label={`carbs g${mode === "per100" ? " / 100 g" : ""}`} value={carbs} set={setCarbs} />
        <Field label={`fat g${mode === "per100" ? " / 100 g" : ""}`} value={fat} set={setFat} />
        <Field label={`fibre g${mode === "per100" ? " / 100 g" : ""}`} value={fibre} set={setFibre} />
      </div>
      {!consistent && <p className="text-xs text-danger">4·protein + 4·carbs + 9·fat = {Math.round(macroKcal)} kcal, more than 25% away from {kcal} kcal — check the label.</p>}
      {mode === "per100" && gramsN > 0 && kcal && <p className="text-xs text-muted tabular">≈ {fmtKcal((n(kcal) * gramsN) / 100)} kcal for {gramsN} g</p>}
      <div className="grid grid-cols-4 gap-2">
        {MEALS.map((m) => (
          <button key={m} type="button" className={`btn py-2 text-sm capitalize ${meal === m ? "btn-primary" : "btn-ghost"}`} onClick={() => setMeal(m)}>{m}</button>
        ))}
      </div>
      {mode === "per100" && (
        <label className="flex items-center gap-2 text-sm text-muted"><input type="checkbox" className="w-auto" checked={save} onChange={(e) => setSave(e.target.checked)} /> Save for search next time</label>
      )}
      <button className="btn btn-primary w-full" type="submit" disabled={!valid}>Log it</button>
    </form>
  );
}

function Field({ label, value, set }: { label: string; value: string; set: (v: string) => void }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input type="number" inputMode="decimal" min={0} step="any" value={value} onChange={(e) => set(e.target.value)} className="mt-1" />
    </label>
  );
}

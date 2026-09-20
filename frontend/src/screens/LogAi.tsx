// The two Gemini-backed logging paths (spec 7): photo and free text. Both return
// candidates only; nothing is written until the portion is confirmed.
import { useState } from "react";
import { PhotoCapture } from "../components/PhotoCapture";
import { PortionPicker } from "../components/PortionPicker";
import { Sheet } from "../components/Sheet";
import { logEntry, previewFor, type EntryBody } from "../lib/actions";
import { describeError, getToken, request } from "../lib/api";
import { MEALS, guessMeal } from "../lib/format";
import type { Meal } from "../lib/types";

interface Candidate {
  name: string;
  confidence: number;
  kcal_100g: number;
  protein_100g: number;
  carbs_100g: number;
  fat_100g: number;
  estimated_grams?: number | null;
  grams?: number;
  portion_basis?: string;
  input_method: "photo" | "text";
  entry_confidence: number;
}

interface CandidatesResponse {
  ok: boolean;
  candidates: Candidate[];
  notes?: string;
  dropped?: number;
  cached?: boolean;
  fallback?: string;
  error?: string;
  photo_path?: string | null;
}

export function CameraTab({ enabled, onDone, onFallback }: { enabled: boolean; onDone: () => void; onFallback: () => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CandidatesResponse | null>(null);
  const [picked, setPicked] = useState<Candidate | null>(null);

  if (!enabled) {
    return (
      <div className="card text-sm space-y-3">
        <p className="text-muted">Photo identification needs a Gemini key in the Pi's <code>.env</code>. Until then, barcode, search and manual entry all work.</p>
        <button className="btn btn-ghost w-full" onClick={onFallback}>Use search instead</button>
      </div>
    );
  }

  const upload = async (blob: Blob) => {
    setBusy(true);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", blob, "photo.jpg");
      const res = await fetch("/api/v1/food/photo", { method: "POST", body: fd, headers: { Authorization: `Bearer ${getToken()}` } });
      const data = (await res.json()) as CandidatesResponse;
      if (!res.ok) throw new Error(JSON.stringify(data));
      setResult(data);
    } catch (e) {
      setResult({ ok: false, candidates: [], fallback: "manual", error: describeError(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <PhotoCapture onPhoto={(blob) => void upload(blob)} busy={busy} />
      <p className="text-xs text-muted">Include a hand, cutlery or the plate for a portion estimate. You always confirm the amount — a photo never logs anything by itself.</p>
      {result && !result.ok && (
        <div className="card text-sm space-y-2">
          <p className="text-warn">{result.error}</p>
          <button className="btn btn-ghost w-full" onClick={onFallback}>Use search / manual instead</button>
        </div>
      )}
      {result?.ok && (
        <CandidateList result={result} onPick={setPicked} />
      )}
      <Sheet open={picked != null} onClose={() => setPicked(null)} title={picked?.name}>
        {picked && (
          <PortionPicker
            initialGrams={Math.round(picked.estimated_grams ?? 100)}
            per100={picked}
            confirmLabel={picked.estimated_grams ? `Log it (${picked.portion_basis === "none" ? "amount is a guess" : "estimate from the photo"})` : "Log it"}
            onConfirm={(grams, meal) => {
              const body: EntryBody & { photo_path?: string | null } = {
                grams, meal, input_method: "photo", confidence: picked.entry_confidence, photo_path: result?.photo_path ?? null,
                food: { name: picked.name, kcal_100g: picked.kcal_100g, protein_100g: picked.protein_100g, carbs_100g: picked.carbs_100g, fat_100g: picked.fat_100g, save: true },
              };
              void logEntry(body, previewFor(body, picked.name));
              setPicked(null);
              onDone();
            }}
          />
        )}
      </Sheet>
    </div>
  );
}

function CandidateList({ result, onPick }: { result: CandidatesResponse; onPick: (c: Candidate) => void }) {
  if (result.candidates.length === 0) {
    return <div className="card text-sm text-muted">Nothing recognisable{result.dropped ? ` (${result.dropped} suggestion${result.dropped > 1 ? "s" : ""} failed the nutrition check)` : ""}. Try again closer, or use search.</div>;
  }
  return (
    <div className="space-y-2">
      {result.notes && <p className="text-xs text-muted">{result.notes}</p>}
      {result.cached && <p className="text-xs text-muted">Same photo as before — served from cache, no API call.</p>}
      {result.candidates.map((c, i) => (
        <button key={i} className="card w-full text-left flex items-center gap-3 py-3" onClick={() => onPick(c)}>
          <div className="flex-1 min-w-0">
            <div className="truncate">{c.name} <span className="text-xs text-muted">· {Math.round(c.confidence * 100)}%</span></div>
            <div className="text-xs text-muted tabular">{Math.round(c.kcal_100g)} kcal · P {c.protein_100g} · C {c.carbs_100g} · F {c.fat_100g} per 100 g{c.estimated_grams ? ` · ~${Math.round(c.estimated_grams)} g` : ""}</div>
          </div>
          <span className="text-accent text-sm">pick</span>
        </button>
      ))}
      {result.dropped ? <p className="text-xs text-muted">{result.dropped} suggestion{result.dropped > 1 ? "s" : ""} rejected by the nutrition check.</p> : null}
    </div>
  );
}

export function DescribeMeal({ enabled, onDone }: { enabled: boolean; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CandidatesResponse | null>(null);
  const [items, setItems] = useState<(Candidate & { grams: number; keep: boolean })[]>([]);
  const [meal, setMeal] = useState<Meal>(guessMeal());
  if (!enabled) return null;

  const parse = async () => {
    setBusy(true);
    setResult(null);
    try {
      const data = await request<CandidatesResponse>("POST", "/api/v1/food/text", { text });
      setResult(data);
      setItems(data.candidates.map((c) => ({ ...c, grams: Math.round(c.grams ?? c.estimated_grams ?? 100), keep: true })));
    } catch (e) {
      setResult({ ok: false, candidates: [], fallback: "manual", error: describeError(e) });
    } finally {
      setBusy(false);
    }
  };
  const logAll = () => {
    for (const it of items) {
      if (!it.keep || it.grams <= 0) continue;
      const body: EntryBody = {
        grams: it.grams, meal, input_method: "text", confidence: it.entry_confidence,
        food: { name: it.name, kcal_100g: it.kcal_100g, protein_100g: it.protein_100g, carbs_100g: it.carbs_100g, fat_100g: it.fat_100g, save: true },
      };
      void logEntry(body, previewFor(body, it.name));
    }
    onDone();
  };

  return (
    <div className="card space-y-3">
      <div className="label">or describe the meal</div>
      <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. two eggs, a slice of toast with butter, a banana" />
      <button className="btn btn-ghost w-full" disabled={busy || text.trim().length < 3} onClick={() => void parse()}>{busy ? "Parsing…" : "Parse with Gemini"}</button>
      {result && !result.ok && <p className="text-sm text-warn">{result.error}</p>}
      {result?.ok && items.length > 0 && (
        <div className="space-y-2">
          {items.map((it, i) => (
            <div key={i} className={`text-sm ${it.keep ? "" : "opacity-40"}`}>
              <label className="flex items-center gap-2">
                <input type="checkbox" className="w-auto" checked={it.keep} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, keep: e.target.checked } : x)))} />
                <span className="flex-1 truncate">{it.name}</span>
                <span className="text-xs text-muted tabular">{Math.round((it.kcal_100g * it.grams) / 100)} kcal · {Math.round(it.confidence * 100)}%</span>
              </label>
              <div className="flex items-center gap-2 mt-1 pl-6">
                <input type="number" inputMode="decimal" className="w-24 py-1.5 text-center" value={it.grams} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, grams: Number(e.target.value) } : x)))} />
                <span className="text-xs text-muted">g · P {Math.round((it.protein_100g * it.grams) / 100)} · C {Math.round((it.carbs_100g * it.grams) / 100)} · F {Math.round((it.fat_100g * it.grams) / 100)}</span>
              </div>
            </div>
          ))}
          <div className="grid grid-cols-4 gap-2">
            {MEALS.map((m) => (
              <button key={m} type="button" className={`btn py-2 text-sm capitalize ${meal === m ? "btn-primary" : "btn-ghost"}`} onClick={() => setMeal(m)}>{m}</button>
            ))}
          </div>
          <button className="btn btn-primary w-full" onClick={logAll} disabled={!items.some((x) => x.keep)}>Log {items.filter((x) => x.keep).length} item{items.filter((x) => x.keep).length === 1 ? "" : "s"}</button>
        </div>
      )}
      {result?.ok && items.length === 0 && <p className="text-sm text-muted">Nothing parsed. Try naming the foods.</p>}
    </div>
  );
}

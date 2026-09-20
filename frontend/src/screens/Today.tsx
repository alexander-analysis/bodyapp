import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bar } from "../components/Sheet";
import { deleteEntry, logWeight, patchDay } from "../lib/actions";
import { describeError } from "../lib/api";
import { MEALS, kcal as fmtKcal, g, kg, shortDay } from "../lib/format";
import { useToday } from "../lib/hooks";
import { usePending, type Op } from "../lib/outbox";
import type { Entry, Today as TodayPayload } from "../lib/types";

export function Today() {
  const q = useToday();
  const pending = usePending();
  if (q.isLoading) return <p className="text-muted">Loading…</p>;
  if (q.error) return <p className="text-danger">{describeError(q.error)}</p>;
  const t = q.data!;
  if (!t.profile) return <SetupNudge />;

  const pendingEntries = pending.filter((p) => !p.failed && p.preview?.kind === "entry" && (!p.preview.day || p.preview.day === t.day));
  const pendingKcal = pendingEntries.reduce((s, p) => s + (p.preview?.kcal ?? 0), 0);
  const pendingProtein = pendingEntries.reduce((s, p) => s + (p.preview?.protein_g ?? 0), 0);
  const consumedKcal = t.consumed.kcal + pendingKcal;
  const consumedProtein = t.consumed.protein_g + pendingProtein;
  const target = t.target;
  const remainingKcal = target ? target.kcal - consumedKcal : null;
  const remainingProtein = target ? target.protein_g - consumedProtein : null;

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold">{shortDay(t.day)}</h1>
        {target && <span className="text-xs text-muted capitalize">{target.phase} · {fmtKcal(target.kcal)} kcal</span>}
      </div>

      {t.mode.names.length > 0 && <ModeCard t={t} />}
      {t.llm && (t.llm.cap_reached || (t.llm.calls_24h >= 3 && t.llm.error_rate_24h >= 0.5)) && (
        <div className="card border-warn/40 text-sm text-warn">
          {t.llm.cap_reached ? `Gemini daily cap reached (${t.llm.cap} calls) — photo and text logging are off until tomorrow; barcode, search and manual still work.` : `Gemini has been failing (${Math.round(t.llm.error_rate_24h * 100)}% of calls in 24 h). Manual logging keeps working.`}
        </div>
      )}

      <section className="card">
        <div className="grid grid-cols-2 gap-4">
          <BigNumber label={remainingKcal != null && remainingKcal < 0 ? "kcal over" : "kcal left"} value={remainingKcal == null ? "–" : fmtKcal(Math.abs(remainingKcal))} danger={remainingKcal != null && remainingKcal < 0} />
          <BigNumber label={remainingProtein != null && remainingProtein < 0 ? "protein done" : "protein left"} value={remainingProtein == null ? "–" : `${g(Math.max(0, remainingProtein))} g`} accent />
        </div>
        {target && (
          <div className="mt-4 space-y-2 text-xs text-muted">
            <Row label="kcal" value={`${fmtKcal(consumedKcal)} / ${fmtKcal(target.kcal)}`}><Bar value={consumedKcal} max={target.kcal} /></Row>
            <Row label="protein" value={`${g(consumedProtein)} / ${target.protein_g} g`}><Bar value={consumedProtein} max={target.protein_g} warnOver={false} color="bg-accent-2" /></Row>
            <Row label="fat (min)" value={`${g(t.consumed.fat_g)} / ${target.fat_g_min} g`}><Bar value={t.consumed.fat_g} max={target.fat_g_min} warnOver={false} color="bg-warn" /></Row>
            <Row label="fibre" value={`${g(t.consumed.fibre_g)} / ${target.fibre_g} g`}><Bar value={t.consumed.fibre_g} max={target.fibre_g} warnOver={false} color="bg-muted" /></Row>
          </div>
        )}
        {!target && <p className="mt-3 text-sm text-warn">No target yet — add a start weight in Settings to get one.</p>}
        <Link to="/log" className="btn btn-primary w-full mt-4">Log food</Link>
      </section>

      <div className="grid grid-cols-2 gap-3">
        <StepsCard t={t} />
        <WeightCard t={t} />
      </div>

      {t.next_session && <NextSessionCard t={t} />}

      <EntriesCard entries={t.entries} pending={pendingEntries} />

      {t.tdee && (
        <p className="text-xs text-muted px-1">
          Maintenance estimate {fmtKcal(t.tdee.tdee_kcal)} kcal ({t.tdee.method}{t.tdee.method === "adaptive" ? `, confidence ${(t.tdee.confidence * 100).toFixed(0)}%` : ""}).
          {t.tdee.notes.length > 0 && <> {t.tdee.notes[0]}</>}
        </p>
      )}
    </div>
  );
}

function SetupNudge() {
  const navigate = useNavigate();
  return (
    <div className="card space-y-3">
      <h1 className="text-lg font-semibold">Welcome</h1>
      <p className="text-sm text-muted">Set up your profile once — sex, birth date, height, current weight and phase. The engine derives your first targets from a formula and adjusts them weekly from what it observes.</p>
      <button className="btn btn-primary w-full" onClick={() => navigate("/settings#profile")}>Set up profile</button>
    </div>
  );
}

function BigNumber({ label, value, accent, danger }: { label: string; value: string; accent?: boolean; danger?: boolean }) {
  return (
    <div>
      <div className={`text-4xl font-semibold tabular ${danger ? "text-danger" : accent ? "text-accent-2" : "text-accent"}`}>{value}</div>
      <div className="label mt-1">{label}</div>
    </div>
  );
}

function Row({ label, value, children }: { label: string; value: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex justify-between mb-1"><span>{label}</span><span className="tabular text-ink">{value}</span></div>
      {children}
    </div>
  );
}

function ModeCard({ t }: { t: TodayPayload }) {
  const m = t.mode;
  return (
    <div className="card border-warn/40 text-sm space-y-1">
      <div className="font-medium text-warn">{m.names.join(" · ")}</div>
      {m.force_maintenance && <div className="text-muted">Targets held at maintenance — no deficit while unwell.</div>}
      {m.training === "blocked" && <div className="text-muted">Training is off. Rest.</div>}
      {m.training === "reduced" && <div className="text-muted">Training reduced: volume ×{m.volume_cap.toFixed(2)}, previous loads, no progression.</div>}
      {m.guidance.includes("above_the_neck") && <div className="text-muted">Above-the-neck symptoms only: a light session is fine if you feel up to it.</div>}
      {m.guidance.includes("electrolytes") && <div className="text-muted">Fluids and electrolytes; fibre target relaxed.</div>}
      {m.guidance.includes("see_a_doctor") && <div className="text-danger">This has gone on long enough that the app stops advising. See a doctor.</div>}
    </div>
  );
}

function StepsCard({ t }: { t: TodayPayload }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(t.day_metrics.steps ?? ""));
  const target = t.target?.steps ?? 9000;
  const steps = t.day_metrics.steps ?? 0;
  return (
    <div className="card">
      <div className="label">steps</div>
      {editing ? (
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); void patchDay(t.day, { steps: Number(value) || 0 }); setEditing(false); }}>
          <input type="number" inputMode="numeric" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
          <button className="btn btn-primary py-2 px-3" type="submit">OK</button>
        </form>
      ) : (
        <button className="mt-1 text-left w-full" onClick={() => setEditing(true)}>
          <div className="text-2xl font-semibold tabular">{steps.toLocaleString()}</div>
          <div className="text-xs text-muted">of {target.toLocaleString()}</div>
        </button>
      )}
      <div className="mt-2"><Bar value={steps} max={target} warnOver={false} color="bg-accent-2" /></div>
    </div>
  );
}

function WeightCard({ t }: { t: TodayPayload }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const w = t.weight;
  const loggedToday = w?.latest.logged_on === t.day;
  return (
    <div className="card">
      <div className="label">weight</div>
      {editing ? (
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); const v = Number(value); if (v >= 30 && v <= 300) { void logWeight(v, null, t.day); setEditing(false); } }}>
          <input type="number" inputMode="decimal" step="0.1" value={value} onChange={(e) => setValue(e.target.value)} placeholder="kg" autoFocus />
          <button className="btn btn-primary py-2 px-3" type="submit">OK</button>
        </form>
      ) : (
        <button className="mt-1 text-left w-full" onClick={() => setEditing(true)}>
          <div className="text-2xl font-semibold tabular">{w ? `${kg(w.trend_kg ?? w.latest.weight_kg)}` : "–"}<span className="text-sm text-muted"> kg trend</span></div>
          <div className="text-xs text-muted">{w ? (loggedToday ? `today ${kg(w.latest.weight_kg)} kg` : `last ${kg(w.latest.weight_kg)} · tap to weigh in`) : "tap to weigh in"}</div>
        </button>
      )}
    </div>
  );
}

function NextSessionCard({ t }: { t: TodayPayload }) {
  const s = t.next_session!;
  return (
    <Link to="/train" className="card block">
      <div className="flex justify-between items-baseline">
        <div className="label">next session</div>
        <div className="text-xs text-accent">open →</div>
      </div>
      {s.blocked ? (
        <div className="mt-1 text-sm text-warn">Training blocked today — {s.note}</div>
      ) : (
        <>
          <div className="mt-1 font-medium">{s.template}</div>
          <div className="text-xs text-muted mt-1 line-clamp-2">
            {s.exercises.filter((e) => !e.omitted).slice(0, 4).map((e) => `${e.exercise.name}${e.weight_kg != null ? ` ${e.weight_kg} kg` : ""} × ${e.target_reps}`).join(" · ")}
          </div>
        </>
      )}
    </Link>
  );
}

function EntriesCard({ entries, pending }: { entries: Entry[]; pending: Op[] }) {
  const byMeal = new Map<string, (Entry | Op)[]>();
  for (const e of entries) byMeal.set(e.meal ?? "other", [...(byMeal.get(e.meal ?? "other") ?? []), e]);
  for (const p of pending) byMeal.set(p.preview?.meal ?? "other", [...(byMeal.get(p.preview?.meal ?? "other") ?? []), p]);
  const order = [...MEALS, "other"];
  if (byMeal.size === 0) return <div className="card text-sm text-muted">Nothing logged yet today.</div>;
  return (
    <div className="card space-y-3">
      {order.filter((m) => byMeal.has(m)).map((m) => (
        <div key={m}>
          <div className="label capitalize mb-1">{m}</div>
          <ul className="divide-y divide-line">
            {byMeal.get(m)!.map((item) =>
              "key" in item ? (
                <li key={item.key} className="py-2 flex justify-between text-sm opacity-70">
                  <span>{item.preview?.label} <span className="text-xs text-accent-2">pending</span></span>
                  <span className="tabular">{item.preview?.kcal != null ? fmtKcal(item.preview.kcal) : "–"}</span>
                </li>
              ) : (
                <li key={item.id} className="py-2 flex items-center gap-2 text-sm">
                  <div className="flex-1 min-w-0">
                    <div className="truncate">{item.food_name ?? (item.input_method === "manual" ? "Manual entry" : item.input_method)}</div>
                    <div className="text-xs text-muted tabular">{Math.round(item.grams)} g · P {g(item.protein_g)} · C {g(item.carbs_g)} · F {g(item.fat_g)}{item.confidence < 0.9 && ` · ~${Math.round(item.confidence * 100)}%`}</div>
                  </div>
                  <span className="tabular">{fmtKcal(item.kcal)}</span>
                  <button className="text-muted px-2" aria-label="remove" onClick={() => void deleteEntry(item.id)}>✕</button>
                </li>
              ),
            )}
          </ul>
        </div>
      ))}
    </div>
  );
}

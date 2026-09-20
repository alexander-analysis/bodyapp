import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteWorkout, logWorkout } from "../lib/actions";
import { describeError } from "../lib/api";
import { shortDay, todayISO } from "../lib/format";
import { useExercises, useNextSession, useRecentWorkouts } from "../lib/hooks";
import type { NextSession, Prescription } from "../lib/types";

interface SetState {
  weight: string;
  reps: string;
  rir: string;
  done: boolean;
}

export function Train() {
  const [template, setTemplate] = useState<string | null>(null);
  const next = useNextSession(template);
  const ex = useExercises();
  const recent = useRecentWorkouts();
  const navigate = useNavigate();
  const [sets, setSets] = useState<Record<number, SetState[]>>({});
  const [rest, setRest] = useState<number>(0);
  const [restLen, setRestLen] = useState(120);
  const [rpe, setRpe] = useState("");
  const [started, setStarted] = useState<number | null>(null);

  const session = next.data;
  useEffect(() => {
    if (!session) return;
    const init: Record<number, SetState[]> = {};
    for (const p of session.exercises) {
      if (p.omitted) continue;
      init[p.exercise.id] = Array.from({ length: Math.max(1, p.sets) }, () => ({ weight: p.weight_kg != null ? String(p.weight_kg) : "", reps: String(p.target_reps), rir: "", done: false }));
    }
    setSets(init);
  }, [session?.template, session?.exercises.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (rest <= 0) return;
    const h = setInterval(() => setRest((r) => Math.max(0, r - 1)), 1000);
    return () => clearInterval(h);
  }, [rest > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  const doneCount = useMemo(() => Object.values(sets).flat().filter((s) => s.done).length, [sets]);

  const toggle = (exerciseId: number, i: number) => {
    setSets((prev) => {
      const list = prev[exerciseId].map((s, j) => (j === i ? { ...s, done: !s.done } : s));
      return { ...prev, [exerciseId]: list };
    });
    const s = sets[exerciseId][i];
    if (!s.done) {
      if (started == null) setStarted(Date.now());
      setRest(restLen);
    }
  };
  const edit = (exerciseId: number, i: number, field: keyof SetState, value: string) =>
    setSets((prev) => ({ ...prev, [exerciseId]: prev[exerciseId].map((s, j) => (j === i ? { ...s, [field]: value } : s)) }));
  const addSet = (exerciseId: number) =>
    setSets((prev) => ({ ...prev, [exerciseId]: [...prev[exerciseId], { ...prev[exerciseId][prev[exerciseId].length - 1], done: false }] }));

  const finish = () => {
    if (!session) return;
    const payload: { exercise_id: number; set_index: number; weight_kg: number; reps: number; rir: number | null }[] = [];
    for (const [exerciseId, list] of Object.entries(sets)) {
      list.forEach((s, i) => {
        if (!s.done) return;
        payload.push({ exercise_id: Number(exerciseId), set_index: i + 1, weight_kg: Number(s.weight) || 0, reps: Number(s.reps) || 0, rir: s.rir === "" ? null : Number(s.rir) });
      });
    }
    if (payload.length === 0) return;
    void logWorkout({
      performed_on: todayISO(),
      template: session.template,
      duration_min: started ? Math.round((Date.now() - started) / 60000) : null,
      rpe: rpe ? Number(rpe) : null,
      sets: payload,
    });
    navigate("/");
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Session</h1>
        <select className="w-auto text-sm py-1.5" value={template ?? ""} onChange={(e) => setTemplate(e.target.value || null)}>
          <option value="">next in rotation</option>
          {ex.data?.templates.map((t) => (
            <option key={t.id} value={t.name}>{t.name}</option>
          ))}
        </select>
      </div>

      {next.error && <p className="text-danger text-sm">{describeError(next.error)}</p>}
      {session && <SessionView session={session} sets={sets} toggle={toggle} edit={edit} addSet={addSet} />}

      {session && !session.blocked && (
        <div className="card space-y-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="label">rest</span>
            {[60, 90, 120, 180].map((s) => (
              <button key={s} className={`rounded-lg px-2 py-1 text-xs ${restLen === s ? "bg-accent text-black" : "bg-panel-2 text-muted"}`} onClick={() => setRestLen(s)}>{s}s</button>
            ))}
            <span className="ml-auto tabular text-lg">{rest > 0 ? `${Math.floor(rest / 60)}:${String(rest % 60).padStart(2, "0")}` : "—"}</span>
          </div>
          <label className="flex items-center gap-2 text-sm"><span className="label w-24">session RPE</span><input type="number" inputMode="numeric" min={1} max={10} value={rpe} onChange={(e) => setRpe(e.target.value)} className="w-20" placeholder="1–10" /></label>
          <button className="btn btn-primary w-full" disabled={doneCount === 0} onClick={finish}>Finish · {doneCount} set{doneCount === 1 ? "" : "s"}</button>
        </div>
      )}

      {rest > 0 && (
        <div className="fixed left-0 right-0 bottom-16 z-10 flex justify-center pointer-events-none" style={{ marginBottom: "env(safe-area-inset-bottom)" }}>
          <button className="pointer-events-auto rounded-full bg-accent text-black px-5 py-2 font-semibold tabular shadow-lg" onClick={() => setRest(0)}>
            rest {Math.floor(rest / 60)}:{String(rest % 60).padStart(2, "0")} · skip
          </button>
        </div>
      )}

      <RecentWorkouts workouts={recent.data?.workouts ?? []} />
    </div>
  );
}

function SessionView({ session, sets, toggle, edit, addSet }: { session: NextSession; sets: Record<number, SetState[]>; toggle: (e: number, i: number) => void; edit: (e: number, i: number, f: keyof SetState, v: string) => void; addSet: (e: number) => void }) {
  if (session.blocked) return <div className="card text-sm text-warn">Training is blocked today — {session.note}. Rest, hydrate, come back when the mode clears.</div>;
  if (session.exercises.length === 0) return <div className="card text-sm text-muted">{session.note ?? "Nothing prescribed."}</div>;
  return (
    <div className="space-y-3">
      <div className="text-sm text-muted">{session.template}{session.mode.training === "reduced" && <span className="text-warn"> · reduced (mode)</span>}</div>
      {session.exercises.map((p) => (
        <ExerciseBlock key={p.exercise.id} p={p} sets={sets[p.exercise.id] ?? []} toggle={(i) => toggle(p.exercise.id, i)} edit={(i, f, v) => edit(p.exercise.id, i, f, v)} addSet={() => addSet(p.exercise.id)} />
      ))}
    </div>
  );
}

function ExerciseBlock({ p, sets, toggle, edit, addSet }: { p: Prescription; sets: SetState[]; toggle: (i: number) => void; edit: (i: number, f: keyof SetState, v: string) => void; addSet: () => void }) {
  if (p.omitted) return <div className="card py-3 text-sm text-muted">{p.exercise.name} — omitted ({p.omit_reason})</div>;
  return (
    <div className="card space-y-2">
      <div className="flex justify-between items-baseline gap-2">
        <div className="font-medium">{p.exercise.name}</div>
        <div className="text-xs text-muted tabular">{p.weight_kg != null ? `${p.weight_kg} kg × ` : ""}{p.target_reps} ({p.exercise.rep_min}–{p.exercise.rep_max})</div>
      </div>
      <p className="text-xs text-muted">{p.note}{p.proposal && <span className="text-warn"> · {p.proposal}</span>}</p>
      <div className="grid grid-cols-[2rem_1fr_1fr_1fr_2.5rem] gap-1 items-center text-xs text-muted px-1"><span>#</span><span>kg</span><span>reps</span><span>RIR</span><span /></div>
      {sets.map((s, i) => (
        <div key={i} className={`grid grid-cols-[2rem_1fr_1fr_1fr_2.5rem] gap-1 items-center ${s.done ? "opacity-60" : ""}`}>
          <span className="text-xs text-muted text-center">{i + 1}</span>
          <input type="number" inputMode="decimal" step="0.5" value={s.weight} onChange={(e) => edit(i, "weight", e.target.value)} className="py-2 text-center" aria-label="kg" />
          <input type="number" inputMode="numeric" value={s.reps} onChange={(e) => edit(i, "reps", e.target.value)} className="py-2 text-center" aria-label="reps" />
          <input type="number" inputMode="numeric" value={s.rir} onChange={(e) => edit(i, "rir", e.target.value)} className="py-2 text-center" placeholder="–" aria-label="RIR" />
          <button aria-label={s.done ? "undo set" : "log set"} onClick={() => toggle(i)} className={`h-10 rounded-xl ${s.done ? "bg-accent text-black" : "bg-panel-2 text-muted"}`}>✓</button>
        </div>
      ))}
      <button className="text-xs text-accent" onClick={addSet}>+ set</button>
    </div>
  );
}

function RecentWorkouts({ workouts }: { workouts: import("../lib/types").Workout[] }) {
  if (workouts.length === 0) return null;
  return (
    <div className="card space-y-2">
      <div className="label">recent</div>
      {workouts.map((w) => (
        <div key={w.id} className="flex items-center gap-2 text-sm">
          <div className="flex-1">
            <div>{w.template ?? "Workout"} <span className="text-muted">· {shortDay(w.performed_on)}</span>{w.health_event_id && <span className="text-warn"> · excluded</span>}</div>
            <div className="text-xs text-muted">{w.sets.length} sets{w.rpe ? ` · RPE ${w.rpe}` : ""}{w.duration_min ? ` · ${w.duration_min} min` : ""}</div>
          </div>
          <button className="text-muted px-2" aria-label="remove workout" onClick={() => { if (confirm("Remove this workout?")) void deleteWorkout(w.id); }}>✕</button>
        </div>
      ))}
    </div>
  );
}

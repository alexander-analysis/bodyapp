import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { describeError, get, request } from "../lib/api";
import { dayMonth, todayISO } from "../lib/format";

export interface EventRow {
  id: number;
  type: "illness" | "injury" | "travel" | "exam";
  severity: "mild" | "moderate" | "gi" | "none";
  fever_flag: number;
  started_at: string;
  ended_at: string | null;
  ramp_until: string | null;
  notes: string | null;
  symptoms: { affected_groups?: string[]; notes?: string };
}

interface ActivePayload {
  active: EventRow[];
  ramping: EventRow[];
  mode: { names: string[]; training: string; force_maintenance: boolean; referral_due: number[] };
}

const GROUPS = ["chest", "back", "shoulders", "quads", "hamstrings", "glutes", "biceps", "triceps", "calves", "abs", "side_delts", "rear_delts", "front_delts"];

export const useActiveModes = () => useQuery({ queryKey: ["modes"], queryFn: () => get<ActivePayload>("/api/v1/modes/active") });

export function ModesCard() {
  const q = useActiveModes();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries();
  const end = async (id: number) => {
    try {
      await request("PATCH", `/api/v1/modes/${id}`, { end_now: true });
      setMsg("Ended. Yesterday counts as the last day; a return ramp follows an illness.");
      await refresh();
    } catch (e) {
      setMsg(describeError(e));
    }
  };
  const active = q.data?.active ?? [];
  const ramping = q.data?.ramping ?? [];
  return (
    <div id="modes" className="card space-y-3 text-sm">
      <div className="label">modes (sick · exam · travel · injury)</div>
      {active.length === 0 && ramping.length === 0 && <p className="text-muted">Nothing active. Rows logged during a mode are kept and shown but excluded from every calculation; illness holds targets at maintenance.</p>}
      {active.map((e) => (
        <div key={e.id} className="flex items-center gap-2">
          <div className="flex-1">
            <div className="capitalize">{e.type}{e.type === "illness" && ` · ${e.severity}${e.fever_flag ? " · fever" : ""}`}{e.symptoms?.affected_groups?.length ? ` · ${e.symptoms.affected_groups.join(", ")}` : ""}</div>
            <div className="text-xs text-muted">since {dayMonth(e.started_at)}{e.notes ? ` · ${e.notes}` : ""}</div>
          </div>
          <button className="btn btn-ghost py-1.5 px-3 text-sm" onClick={() => void end(e.id)}>{e.type === "illness" ? "Recovered" : "End"}</button>
        </div>
      ))}
      {ramping.map((e) => (
        <div key={e.id} className="text-muted text-xs">Return ramp after the {e.type} until {e.ramp_until ? dayMonth(e.ramp_until) : "?"}: previous loads, reduced volume, no deficit. Weight that comes back now is rehydration, not fat.</div>
      ))}
      {q.data?.mode.referral_due.length ? <p className="text-danger text-xs">This illness has lasted long enough that the app stops advising. See a doctor.</p> : null}
      {!open && <button className="btn btn-ghost w-full" onClick={() => setOpen(true)}>Start a mode…</button>}
      {open && <StartForm onClose={() => setOpen(false)} onSaved={(m) => { setMsg(m); setOpen(false); void refresh(); }} />}
      {msg && <p className="text-xs text-muted">{msg}</p>}
    </div>
  );
}

function StartForm({ onClose, onSaved }: { onClose: () => void; onSaved: (msg: string) => void }) {
  const [type, setType] = useState<EventRow["type"]>("illness");
  const [severity, setSeverity] = useState<"mild" | "moderate" | "gi">("mild");
  const [fever, setFever] = useState(false);
  const [symptoms, setSymptoms] = useState("");
  const [groups, setGroups] = useState<string[]>([]);
  const [started, setStarted] = useState(todayISO());
  const [suggestion, setSuggestion] = useState<{ severity: string; fever_likely: boolean; rationale: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const suggest = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await request<{ ok: boolean; suggestion: { severity: string; fever_likely: boolean; rationale: string } | null; error?: string }>("POST", "/api/v1/modes/suggest", { text: symptoms });
      if (r.ok && r.suggestion) setSuggestion(r.suggestion);
      else setErr(r.error ?? "No suggestion");
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await request("POST", "/api/v1/modes", {
        type, severity: type === "illness" ? severity : "none", fever_flag: type === "illness" && fever, started_at: started,
        symptoms: type === "injury" ? { affected_groups: groups, notes: symptoms } : symptoms ? { notes: symptoms } : null,
        notes: symptoms || null,
      });
      onSaved(`${type} started. ${type === "illness" ? "Targets are held at maintenance and training is adjusted until you mark yourself recovered." : ""}`);
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <div className="grid grid-cols-4 gap-1">
        {(["illness", "exam", "travel", "injury"] as const).map((t) => (
          <button key={t} type="button" className={`btn py-2 text-xs capitalize ${type === t ? "btn-primary" : "btn-ghost"}`} onClick={() => setType(t)}>{t}</button>
        ))}
      </div>
      <label className="block"><span className="label">started</span><input type="date" value={started} max={todayISO()} onChange={(e) => setStarted(e.target.value)} className="mt-1" /></label>
      {type === "illness" && (
        <>
          <textarea rows={2} value={symptoms} onChange={(e) => setSymptoms(e.target.value)} placeholder="Symptoms (optional) — e.g. sore throat, 38° fever, no appetite" />
          <button type="button" className="btn btn-ghost w-full" disabled={busy || symptoms.trim().length < 3} onClick={() => void suggest()}>Suggest severity (Gemini)</button>
          {suggestion && (
            <div className="text-xs text-muted">Suggested: <b className="text-ink">{suggestion.severity}</b>{suggestion.fever_likely ? " with fever" : ""} — {suggestion.rationale} <button type="button" className="text-accent" onClick={() => { setSeverity(suggestion.severity as "mild" | "moderate" | "gi"); setFever(suggestion.fever_likely); }}>apply</button></div>
          )}
          <div className="grid grid-cols-3 gap-1">
            {(["mild", "moderate", "gi"] as const).map((s) => (
              <button key={s} type="button" className={`btn py-2 text-xs ${severity === s ? "btn-primary" : "btn-ghost"}`} onClick={() => setSeverity(s)}>{s === "gi" ? "GI / stomach" : s}</button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="w-auto" checked={fever} onChange={(e) => setFever(e.target.checked)} /> fever (locks training regardless of severity)</label>
          <p className="text-xs text-muted">Mild: maintenance, light session allowed. Moderate: maintenance +5%, no training. GI: maintenance, no training, fibre relaxed, fluids and electrolytes.</p>
        </>
      )}
      {type === "injury" && (
        <>
          <div className="label">affected muscle groups (those exercises are omitted)</div>
          <div className="flex flex-wrap gap-1">
            {GROUPS.map((g) => (
              <button key={g} type="button" className={`rounded-full px-2 py-1 text-xs ${groups.includes(g) ? "bg-accent text-black" : "bg-panel-2 text-muted"}`} onClick={() => setGroups(groups.includes(g) ? groups.filter((x) => x !== g) : [...groups, g])}>{g}</button>
            ))}
          </div>
          <input value={symptoms} onChange={(e) => setSymptoms(e.target.value)} placeholder="What happened (optional)" />
        </>
      )}
      {(type === "exam" || type === "travel") && <input value={symptoms} onChange={(e) => setSymptoms(e.target.value)} placeholder="Note (optional)" />}
      {type === "exam" && <p className="text-xs text-muted">Exam: maintenance, prescribed volume cut by a third, logging strictness relaxed, streaks paused.</p>}
      {type === "travel" && <p className="text-xs text-muted">Travel: plateau and rate alerts off, relaxed estimates; weigh-ins still recorded but excluded.</p>}
      {err && <p className="text-xs text-danger">{err}</p>}
      <div className="grid grid-cols-2 gap-2">
        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button type="submit" className="btn btn-primary" disabled={busy || (type === "injury" && groups.length === 0)}>Start</button>
      </div>
    </form>
  );
}

/** Daily "still unwell?" prompt (spec 8.3): never auto-expires. */
export function StillUnwellPrompt() {
  const q = useActiveModes();
  const qc = useQueryClient();
  const [answered, setAnswered] = useState<string>(() => {
    try {
      return localStorage.getItem("health.unwell.answered") ?? "";
    } catch {
      return "";
    }
  });
  const illness = q.data?.active.find((e) => e.type === "illness");
  if (!illness || answered === todayISO()) return null;
  const mark = () => {
    try {
      localStorage.setItem("health.unwell.answered", todayISO());
    } catch {
      /* ignore */
    }
    setAnswered(todayISO());
  };
  return (
    <div className="card border-warn/40 text-sm space-y-2">
      <div>Still unwell today?</div>
      <div className="grid grid-cols-2 gap-2">
        <button className="btn btn-ghost py-2" onClick={mark}>Yes, still sick</button>
        <button className="btn btn-primary py-2" onClick={() => void request("PATCH", `/api/v1/modes/${illness.id}`, { end_now: true }).then(() => { mark(); void qc.invalidateQueries(); })}>Recovered</button>
      </div>
      <p className="text-xs text-muted">Recovery starts a short return ramp: previous loads, no deficit, and the weight that comes back is water.</p>
    </div>
  );
}

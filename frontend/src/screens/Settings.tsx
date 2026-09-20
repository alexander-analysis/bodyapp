import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { ApiError, describeError, downloadExport, getToken, request, setToken } from "../lib/api";
import { dayMonth, todayISO } from "../lib/format";
import { useHealth, useReview, useTargets, useToday } from "../lib/hooks";
import { useInstall } from "../lib/install";
import { discardFailed, retryFailed, usePending } from "../lib/outbox";
import type { Phase, Profile, Target } from "../lib/types";

export function Settings() {
  const location = useLocation();
  useEffect(() => {
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: "smooth" });
  }, [location.hash]);
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">More</h1>
      <Link to="/summary" className="card block"><div className="font-medium">Weekly summary →</div><div className="text-xs text-muted">Trend change, adherence, maintenance estimate, target changes.</div></Link>
      <ProfileCard />
      <TargetsCard />
      <EngineCard />
      <QueueCard />
      <DataCard />
      <InstallCard />
      <AboutCard />
    </div>
  );
}

// --- profile -------------------------------------------------------------------

function ProfileCard() {
  const today = useToday();
  const qc = useQueryClient();
  const p = today.data?.profile ?? null;
  const [form, setForm] = useState<{ name: string; sex: "m" | "f"; birth_date: string; height_cm: string; goal_weight_kg: string; start_weight_kg: string; phase: Phase }>({
    name: "", sex: "m", birth_date: "2000-01-01", height_cm: "", goal_weight_kg: "", start_weight_kg: "", phase: "cut",
  });
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (p) setForm((f) => ({ ...f, name: p.name, sex: p.sex, birth_date: p.birth_date, height_cm: String(p.height_cm), goal_weight_kg: p.goal_weight_kg == null ? "" : String(p.goal_weight_kg) }));
  }, [p?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await request<{ profile: Profile; target: Target | null }>("PUT", "/api/v1/profile", {
        name: form.name.trim(), sex: form.sex, birth_date: form.birth_date, height_cm: Number(form.height_cm),
        goal_weight_kg: form.goal_weight_kg ? Number(form.goal_weight_kg) : null,
        start_weight_kg: form.start_weight_kg ? Number(form.start_weight_kg) : null, phase: form.phase,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Madrid",
      });
      setMsg("Saved.");
      await qc.invalidateQueries();
    } catch (e) {
      setMsg(describeError(e));
    } finally {
      setBusy(false);
    }
  };
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <form id="profile" className="card space-y-3" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <div className="label">profile</div>
      <input value={form.name} onChange={set("name")} placeholder="Name" required />
      <div className="grid grid-cols-2 gap-2">
        <label><span className="label">sex</span><select value={form.sex} onChange={set("sex")} className="mt-1"><option value="m">male</option><option value="f">female</option></select></label>
        <label><span className="label">born</span><input type="date" value={form.birth_date} onChange={set("birth_date")} className="mt-1" required /></label>
        <label><span className="label">height, cm</span><input type="number" inputMode="decimal" value={form.height_cm} onChange={set("height_cm")} className="mt-1" required min={100} max={250} /></label>
        <label><span className="label">goal weight, kg</span><input type="number" inputMode="decimal" step="0.5" value={form.goal_weight_kg} onChange={set("goal_weight_kg")} className="mt-1" placeholder="optional" /></label>
        {!p && (
          <>
            <label><span className="label">current weight, kg</span><input type="number" inputMode="decimal" step="0.1" value={form.start_weight_kg} onChange={set("start_weight_kg")} className="mt-1" required min={30} max={300} /></label>
            <label><span className="label">phase</span><select value={form.phase} onChange={set("phase")} className="mt-1"><option value="cut">cut</option><option value="maintain">maintain</option><option value="gain">gain</option></select></label>
          </>
        )}
      </div>
      {!p && <p className="text-xs text-muted">Your first targets come from Mifflin-St Jeor × 1.35 with a phase offset, clamped to the safety floors. After three weeks the engine replaces the formula with what it observes.</p>}
      <button className="btn btn-primary w-full" type="submit" disabled={busy}>{p ? "Save profile" : "Create profile"}</button>
      {msg && <p className="text-sm text-muted">{msg}</p>}
    </form>
  );
}

// --- targets -------------------------------------------------------------------

function TargetsCard() {
  const q = useTargets();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ kcal: "", protein_g: "", fat_g_min: "", steps: "", phase: "", reason: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const cur = q.data?.current ?? null;
  const submit = async () => {
    setMsg(null);
    const body: Record<string, unknown> = { reason: f.reason };
    for (const k of ["kcal", "protein_g", "fat_g_min", "steps"] as const) if (f[k] !== "") body[k] = Number(f[k]);
    if (f.phase) body.phase = f.phase;
    try {
      const row = await request<Target>("POST", "/api/v1/targets/override", body);
      setMsg(row.rails_tripped && row.rails_tripped.length ? `Saved — a safety rail adjusted it: ${row.rails_tripped.join(", ")}` : "Saved.");
      setOpen(false);
      setF({ kcal: "", protein_g: "", fat_g_min: "", steps: "", phase: "", reason: "" });
      await qc.invalidateQueries();
    } catch (e) {
      setMsg(e instanceof ApiError && e.status === 409 ? `Rejected by a safety rail: ${describeError(e)}` : describeError(e));
    }
  };
  return (
    <div id="targets" className="card space-y-3">
      <div className="label">targets</div>
      {cur ? (
        <div className="text-sm">
          <div className="tabular"><b>{cur.kcal}</b> kcal · <b>{cur.protein_g}</b> g protein · fat ≥ <b>{cur.fat_g_min}</b> g · fibre {cur.fibre_g} g · <b>{cur.steps.toLocaleString()}</b> steps · <span className="capitalize">{cur.phase}</span></div>
          <div className="text-xs text-muted mt-1">since {dayMonth(cur.effective_from)} · set by {cur.set_by} — {cur.reason}</div>
        </div>
      ) : (
        <p className="text-sm text-muted">No target yet.</p>
      )}
      {cur && !open && <button className="btn btn-ghost w-full" onClick={() => setOpen(true)}>Override…</button>}
      {open && (
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <div className="grid grid-cols-2 gap-2">
            <input type="number" placeholder={`kcal (${cur?.kcal})`} value={f.kcal} onChange={(e) => setF({ ...f, kcal: e.target.value })} />
            <input type="number" placeholder={`protein g (${cur?.protein_g})`} value={f.protein_g} onChange={(e) => setF({ ...f, protein_g: e.target.value })} />
            <input type="number" placeholder={`fat min g (${cur?.fat_g_min})`} value={f.fat_g_min} onChange={(e) => setF({ ...f, fat_g_min: e.target.value })} />
            <input type="number" placeholder={`steps (${cur?.steps})`} value={f.steps} onChange={(e) => setF({ ...f, steps: e.target.value })} />
          </div>
          <select value={f.phase} onChange={(e) => setF({ ...f, phase: e.target.value })}><option value="">phase: keep {cur?.phase}</option><option value="cut">cut</option><option value="maintain">maintain</option><option value="gain">gain</option></select>
          <input placeholder="Why? (stored with the row)" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />
          <p className="text-xs text-muted">Floors: 1700 kcal, 150 g protein, 80 g fat. During illness the phase is forced to maintain. These cannot be overridden.</p>
          <div className="grid grid-cols-2 gap-2"><button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button><button type="submit" className="btn btn-primary">Save override</button></div>
        </form>
      )}
      {msg && <p className="text-sm text-muted">{msg}</p>}
      {q.data && q.data.history.length > 1 && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">history ({q.data.history.length})</summary>
          <ul className="mt-2 space-y-2">
            {q.data.history.map((t) => (
              <li key={t.id}><span className="tabular text-ink">{dayMonth(t.effective_from)} · {t.kcal} kcal · {t.protein_g} g · {t.steps} steps · {t.phase}</span> · {t.set_by}<br />{t.reason}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// --- engine --------------------------------------------------------------------

function EngineCard() {
  const q = useReview();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const latest = q.data?.latest ?? null;
  const run = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const out = await request<{ review: import("../lib/types").Review }>("POST", "/api/v1/review/run", {});
      const r = out.review;
      setMsg(r.target ? `Changed: ${r.target.kcal} kcal / ${r.target.steps} steps — ${r.reason}` : `No change — ${r.reason}`);
      await qc.invalidateQueries();
    } catch (e) {
      setMsg(describeError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div id="engine" className="card space-y-2 text-sm">
      <div className="label">weekly review (engine)</div>
      {latest ? (
        <div>
          <div><span className="capitalize">{latest.assessment.replace(/_/g, " ")}</span>{latest.rate_pct_week != null && <span className="text-muted"> · trend {latest.rate_pct_week > 0 ? "−" : "+"}{Math.abs(latest.rate_pct_week).toFixed(2)}%/week</span>} <span className="text-muted">· {dayMonth(latest.reviewed_on)}</span></div>
          <div className="text-xs text-muted">{latest.reason}</div>
          {latest.proposals.map((p) => <div key={p} className="text-xs text-warn mt-1">Proposal: {p} (change the phase above to accept)</div>)}
        </div>
      ) : (
        <p className="text-muted">Runs every Sunday night: trend → maintenance estimate → at most one target change, with a reason. Nothing has run yet.</p>
      )}
      <button className="btn btn-ghost w-full" disabled={busy} onClick={() => void run()}>Run the weekly review now</button>
      {msg && <p className="text-xs text-muted">{msg}</p>}
    </div>
  );
}

// --- queue ---------------------------------------------------------------------

function QueueCard() {
  const pending = usePending();
  if (pending.length === 0) return null;
  return (
    <div id="queue" className="card space-y-2">
      <div className="label">offline queue</div>
      {pending.map((op) => (
        <div key={op.id} className="text-sm flex items-center gap-2">
          <div className="flex-1">
            <div>{op.preview?.label ?? `${op.method} ${op.path}`}</div>
            <div className={`text-xs ${op.failed ? "text-danger" : "text-muted"}`}>{op.failed ? `rejected: ${op.lastError}` : op.lastError ? `waiting (${op.attempts} tries): ${op.lastError}` : "waiting to send"}</div>
          </div>
          {op.failed && <button className="btn btn-ghost py-1 px-2 text-xs" onClick={() => void retryFailed(op.id!)}>Retry</button>}
          <button className="btn btn-danger py-1 px-2 text-xs" onClick={() => void discardFailed(op.id!)}>Discard</button>
        </div>
      ))}
    </div>
  );
}

// --- data, install, about ------------------------------------------------------

function DataCard() {
  const [token, setTok] = useState(getToken());
  const [msg, setMsg] = useState<string | null>(null);
  const qc = useQueryClient();
  return (
    <div id="data" className="card space-y-3">
      <div className="label">data & access</div>
      <button className="btn btn-ghost w-full" onClick={() => void downloadExport().catch((e) => setMsg(describeError(e)))}>Export everything (CSV zip)</button>
      <label className="block"><span className="label">API token</span><input value={token} onChange={(e) => setTok(e.target.value)} className="mt-1" spellCheck={false} autoComplete="off" /></label>
      <button className="btn btn-ghost w-full" onClick={() => { setToken(token); void qc.invalidateQueries(); setMsg("Token saved on this device."); }}>Save token</button>
      {msg && <p className="text-sm text-muted">{msg}</p>}
    </div>
  );
}

function InstallCard() {
  const install = useInstall();
  return (
    <div className="card space-y-2 text-sm">
      <div className="label">install</div>
      {install.standalone ? (
        <p className="text-muted">Installed — running full-screen.</p>
      ) : install.ios ? (
        <p className="text-muted">iPhone: open this page in Safari, tap <b className="text-ink">Share</b>, then <b className="text-ink">Add to Home Screen</b>.</p>
      ) : install.canPrompt ? (
        <button className="btn btn-primary w-full" onClick={() => void install.prompt()}>Install app</button>
      ) : (
        <p className="text-muted">Use your browser's “Install app” / “Add to Home screen” menu item.</p>
      )}
    </div>
  );
}

function AboutCard() {
  const health = useHealth();
  const today = useToday();
  return (
    <div className="card space-y-2 text-xs text-muted">
      <div className="label">about</div>
      <p>{today.data?.scope}</p>
      <p>
        version {health.data?.version ?? "…"} · schema {health.data?.schema ?? "…"} · Gemini {health.data?.gemini ?? "…"} · newest backup {health.data?.backup_newest_age_h == null ? "none yet" : `${health.data.backup_newest_age_h} h ago`} · {todayISO()}
      </p>
    </div>
  );
}

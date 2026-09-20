import { describeError } from "../lib/api";
import { dayMonth, kcal as fmtKcal, kg, pct, signed } from "../lib/format";
import { useSummary } from "../lib/hooks";

export function Summary() {
  const q = useSummary();
  if (q.isLoading) return <p className="text-muted">Loading…</p>;
  if (q.error) return <p className="text-danger">{describeError(q.error)}</p>;
  const s = q.data!;
  const a = s.adherence;
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Week of {dayMonth(s.week.start)} – {dayMonth(s.week.end)}</h1>

      {s.narrative ? (
        <div className="card text-sm leading-relaxed">{s.narrative}</div>
      ) : (
        <div className="card text-sm text-muted">The written weekly summary (Gemini) arrives in milestone 8. The numbers it will be describing are already below — it never computes them.</div>
      )}

      {s.estimates_flagged && <div className="card text-sm text-warn">Intake figures this week are estimates (mean logging confidence below 0.6); the maintenance estimate is down-weighted accordingly.</div>}

      <div className="grid grid-cols-2 gap-3">
        <Stat label="trend change" value={s.trend.change_kg != null ? `${signed(s.trend.change_kg, 2)} kg` : "–"} sub={s.trend.rate_pct_week != null ? `${signed(-s.trend.rate_pct_week, 2)}%/week` : undefined} />
        <Stat label="trend now" value={s.trend.end_kg != null ? `${kg(s.trend.end_kg)} kg` : "–"} sub={s.trend.start_kg != null ? `from ${kg(s.trend.start_kg)}` : undefined} />
        <Stat label="days logged" value={`${s.days_logged} / 7`} sub={s.days_excluded ? `${s.days_excluded} excluded (event)` : undefined} />
        <Stat label="maintenance" value={s.tdee ? `${fmtKcal(s.tdee.tdee_kcal)}` : "–"} sub={s.tdee ? `${s.tdee.method}${s.tdee.method === "adaptive" ? ` · ${Math.round(s.tdee.confidence * 100)}%` : ""}` : undefined} />
      </div>

      {a && a.days_considered > 0 ? (
        <div className="card text-sm space-y-1">
          <div className="label mb-1">adherence · {a.days_considered} complete day{a.days_considered === 1 ? "" : "s"}</div>
          <Row k="kcal within ±10%" v={pct(a.kcal_hit_pct)} />
          <Row k="protein target met" v={pct(a.protein_hit_pct)} />
          <Row k="steps target met" v={pct(a.steps_hit_pct)} />
          <Row k="mean intake" v={`${fmtKcal(a.mean_kcal)} kcal · ${a.mean_protein_g ?? "–"} g protein`} />
        </div>
      ) : (
        <div className="card text-sm text-muted">No logged-complete days yet this week (3+ entries across 2+ meals, at least half the target).</div>
      )}

      {s.target_changes.length > 0 && (
        <div className="card text-sm space-y-2">
          <div className="label">target changes this week</div>
          {s.target_changes.map((t) => (
            <div key={t.id}>
              <div className="tabular">{dayMonth(t.effective_from)} · {t.kcal} kcal · {t.protein_g} g protein · {t.steps} steps · <span className="text-muted">{t.set_by}</span></div>
              <div className="text-xs text-muted">{t.reason}</div>
            </div>
          ))}
        </div>
      )}

      {s.volume.groups.length > 0 && (
        <div className="card text-sm">
          <div className="label mb-1">volume flags</div>
          <div className="flex flex-wrap gap-1">
            {s.volume.groups.map((g) => (
              <span key={g.muscle_group} className={`rounded-full px-2 py-0.5 text-xs ${g.flag === "ok" ? "bg-accent/15 text-accent" : g.flag === "low" ? "bg-warn/15 text-warn" : "bg-danger/15 text-danger"}`}>{g.muscle_group} {g.sets}</span>
            ))}
          </div>
        </div>
      )}

      {s.events.length > 0 && (
        <div className="card text-sm">
          <div className="label mb-1">health events</div>
          {s.events.map((e) => (
            <div key={e.id}>{e.type}{e.severity !== "none" ? ` (${e.severity})` : ""} · {dayMonth(e.started_at)}{e.ended_at ? ` – ${dayMonth(e.ended_at)}` : " – ongoing"}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card py-3">
      <div className="label">{label}</div>
      <div className="text-xl font-semibold tabular mt-0.5">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="flex justify-between"><span className="text-muted">{k}</span><span className="tabular">{v}</span></div>;
}

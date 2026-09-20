import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { TrendChart } from "../components/TrendChart";
import { dayMonth } from "../lib/format";
import { useExercises, useHistory, useReview, useTrend, useVolume } from "../lib/hooks";

const TOOLTIP = { background: "#171a21", border: "1px solid #2a2f3a", borderRadius: 12, fontSize: 12 };

export function Progress() {
  const trend = useTrend(90);
  const ex = useExercises();
  const [exerciseId, setExerciseId] = useState<number | null>(null);
  const history = useHistory(exerciseId);
  const volume = useVolume();
  const review = useReview();
  const tdee = [...(review.data?.tdee_history ?? [])].reverse();

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Progress</h1>

      <section className="card">
        <div className="label mb-2">trend weight · 90 days</div>
        {trend.data ? <TrendChart data={trend.data} /> : <p className="text-sm text-muted">Loading…</p>}
      </section>

      <section className="card">
        <div className="flex items-center justify-between mb-2">
          <div className="label">estimated 1RM</div>
          <select className="w-auto text-sm py-1.5" value={exerciseId ?? ""} onChange={(e) => setExerciseId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">choose exercise</option>
            {ex.data?.exercises.map((x) => (
              <option key={x.id} value={x.id}>{x.name}</option>
            ))}
          </select>
        </div>
        {exerciseId == null && <p className="text-sm text-muted">Epley e1RM per session — a trend indicator, never a prescription.</p>}
        {history.data && history.data.e1rm_trend.length === 0 && <p className="text-sm text-muted">No clean sessions yet for this exercise.</p>}
        {history.data && history.data.e1rm_trend.length > 0 && (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={history.data.e1rm_trend} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid stroke="#2a2f3a" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="day" tickFormatter={dayMonth} stroke="#9aa3b2" fontSize={11} minTickGap={24} />
              <YAxis stroke="#9aa3b2" fontSize={11} width={44} domain={["auto", "auto"]} />
              <Tooltip contentStyle={TOOLTIP} formatter={(v) => [`${Number(v).toFixed(1)} kg`, "e1RM"]} />
              <Line type="monotone" dataKey="e1rm" stroke="#60a5fa" strokeWidth={2.5} dot={{ r: 3 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
        {history.data && history.data.excluded_sessions > 0 && <p className="text-xs text-muted mt-1">{history.data.excluded_sessions} session(s) during health events are shown nowhere here.</p>}
      </section>

      <section className="card">
        <div className="label mb-2">maintenance estimate · weekly</div>
        {tdee.length === 0 && <p className="text-sm text-muted">Recorded every Sunday night; adaptive once 21 days of data exist.</p>}
        {tdee.length > 0 && (
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={tdee} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid stroke="#2a2f3a" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="computed_on" tickFormatter={dayMonth} stroke="#9aa3b2" fontSize={11} minTickGap={24} />
              <YAxis stroke="#9aa3b2" fontSize={11} width={44} domain={["auto", "auto"]} />
              <Tooltip contentStyle={TOOLTIP} formatter={(v, _n, item) => [`${Math.round(Number(v))} kcal (${(item.payload as { method: string }).method})`, "maintenance"]} />
              <Line type="monotone" dataKey="tdee_kcal" stroke="#fbbf24" strokeWidth={2.5} dot={{ r: 3 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </section>

      <section className="card">
        <div className="label mb-2">working sets · last 7 days</div>
        {volume.data && volume.data.groups.length === 0 && <p className="text-sm text-muted">No sets this week.</p>}
        {volume.data && volume.data.groups.length > 0 && (
          <ResponsiveContainer width="100%" height={Math.max(160, volume.data.groups.length * 26)}>
            <BarChart data={volume.data.groups} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
              <XAxis type="number" stroke="#9aa3b2" fontSize={11} />
              <YAxis type="category" dataKey="muscle_group" stroke="#9aa3b2" fontSize={11} width={80} />
              <Tooltip contentStyle={TOOLTIP} formatter={(v) => [`${v} sets`, ""]} />
              <ReferenceLine x={volume.data.band.low * volume.data.band.cap} stroke="#fbbf24" strokeDasharray="4 4" />
              <ReferenceLine x={volume.data.band.high * volume.data.band.cap} stroke="#f87171" strokeDasharray="4 4" />
              <Bar dataKey="sets" fill="#34d399" radius={[0, 6, 6, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        )}
        {volume.data && (
          <p className="text-xs text-muted mt-1">
            Band {Math.round(volume.data.band.low * volume.data.band.cap)}–{Math.round(volume.data.band.high * volume.data.band.cap)} sets per group per week{volume.data.band.cap < 1 ? ` (scaled ×${volume.data.band.cap.toFixed(2)} by the active mode)` : ""}. Secondary muscles count half.
          </p>
        )}
      </section>
    </div>
  );
}

import { useState } from "react";
import { TrendChart } from "../components/TrendChart";
import { logWeight } from "../lib/actions";
import { describeError } from "../lib/api";
import { kg, shortDay, signed, todayISO } from "../lib/format";
import { useToday, useTrend } from "../lib/hooks";
import { usePending } from "../lib/outbox";

const RANGES = [30, 90, 365] as const;

export function Weight() {
  const [days, setDays] = useState<(typeof RANGES)[number]>(90);
  const trend = useTrend(days);
  const today = useToday();
  const pending = usePending().filter((p) => !p.failed && p.preview?.kind === "weight");
  const [value, setValue] = useState("");
  const [waist, setWaist] = useState("");
  const [day, setDay] = useState(todayISO());
  const v = Number(value);
  const valid = v >= 30 && v <= 300;
  const phase = today.data?.target?.phase;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Weigh-in</h1>
      <form
        className="card space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!valid) return;
          void logWeight(v, waist ? Number(waist) : null, day);
          setValue("");
          setWaist("");
        }}
      >
        <div className="flex gap-2 items-end">
          <label className="flex-1">
            <span className="label">weight, kg</span>
            <input type="number" inputMode="decimal" step="0.1" min={30} max={300} value={value} onChange={(e) => setValue(e.target.value)} className="mt-1 text-2xl" placeholder="0.0" autoFocus />
          </label>
          <label className="w-28">
            <span className="label">waist, cm</span>
            <input type="number" inputMode="decimal" step="0.5" value={waist} onChange={(e) => setWaist(e.target.value)} className="mt-1" placeholder="opt." />
          </label>
        </div>
        <div className="flex gap-2 items-center">
          <input type="date" value={day} max={todayISO()} onChange={(e) => setDay(e.target.value)} className="flex-1" />
          <button className="btn btn-primary" type="submit" disabled={!valid}>Save</button>
        </div>
        {pending.length > 0 && <p className="text-xs text-accent-2">{pending.length} weigh-in{pending.length > 1 ? "s" : ""} queued.</p>}
      </form>

      <div className="card">
        <div className="flex items-center justify-between mb-2">
          <div>
            <div className="text-2xl font-semibold tabular">{kg(trend.data?.trend_kg)} <span className="text-sm text-muted">kg trend</span></div>
            <div className="text-xs text-muted">
              {trend.data?.latest ? `last weigh-in ${kg(trend.data.latest.weight_kg)} kg on ${shortDay(trend.data.latest.logged_on)}` : "no weigh-ins yet"}
            </div>
          </div>
          <div className="flex gap-1 text-xs">
            {RANGES.map((r) => (
              <button key={r} onClick={() => setDays(r)} className={`rounded-lg px-2 py-1 ${days === r ? "bg-accent text-black" : "bg-panel-2 text-muted"}`}>{r}d</button>
            ))}
          </div>
        </div>
        {trend.error && <p className="text-danger text-sm">{describeError(trend.error)}</p>}
        {trend.data && <TrendChart data={trend.data} />}
        {trend.data?.rate_pct_week != null && (
          <p className="text-xs text-muted mt-2">
            Trend {signed(-trend.data.rate_pct_week)}% of body weight per week over the last two weeks
            {phase === "cut" && (trend.data.rate_pct_week >= 0.3 && trend.data.rate_pct_week <= 0.8 ? " — on track." : trend.data.rate_pct_week > 1.0 ? " — faster than the safe rate; the engine will raise the target." : trend.data.rate_pct_week < 0.15 ? " — stalled; the engine adjusts after 14 clean days." : ".")}
            {phase !== "cut" && "."}
          </p>
        )}
        <p className="text-xs text-muted mt-1">The line is the trend (EWMA); dots are raw weigh-ins. Shaded bands are health events, excluded from every calculation.</p>
      </div>
    </div>
  );
}

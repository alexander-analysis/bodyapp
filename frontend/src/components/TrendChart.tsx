import { CartesianGrid, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dayMonth } from "../lib/format";
import type { TrendPayload } from "../lib/types";

/** Trend line with raw points behind it at low opacity, shaded bands for health events (spec 11). */
export function TrendChart({ data, height = 220 }: { data: TrendPayload; height?: number }) {
  const points = data.points.map((p) => ({ day: p.day, raw: p.raw_kg, trend: p.trend_kg, excluded: p.excluded }));
  if (points.length === 0) return <div className="text-sm text-muted py-8 text-center">No weigh-ins yet.</div>;
  const values = points.flatMap((p) => [p.raw, p.trend]);
  const lo = Math.floor(Math.min(...values) - 0.5);
  const hi = Math.ceil(Math.max(...values) + 0.5);
  const first = points[0].day;
  const last = points[points.length - 1].day;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={points} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid stroke="#2a2f3a" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="day" tickFormatter={dayMonth} stroke="#9aa3b2" fontSize={11} minTickGap={24} />
        <YAxis domain={[lo, hi]} stroke="#9aa3b2" fontSize={11} width={44} tickFormatter={(v: number) => v.toFixed(1)} />
        <Tooltip
          contentStyle={{ background: "#171a21", border: "1px solid #2a2f3a", borderRadius: 12, fontSize: 12 }}
          labelFormatter={(l) => String(l)}
          formatter={(v, name) => [`${Number(v).toFixed(2)} kg`, name === "trend" ? "trend" : "weigh-in"]}
        />
        {data.bands.map((b) => (
          <ReferenceArea
            key={b.event_id}
            x1={b.start < first ? first : b.start}
            x2={b.end > last ? last : b.end}
            fill="#fbbf24"
            fillOpacity={0.12}
            strokeOpacity={0}
          />
        ))}
        <Line
          type="monotone"
          dataKey="raw"
          stroke="#9aa3b2"
          strokeOpacity={0}
          dot={{ r: 2.5, fill: "#9aa3b2", fillOpacity: 0.45, strokeWidth: 0 }}
          activeDot={{ r: 4 }}
          isAnimationActive={false}
        />
        <Line type="monotone" dataKey="trend" stroke="#34d399" strokeWidth={2.5} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

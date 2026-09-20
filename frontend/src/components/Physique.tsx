import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { describeError, get, getToken, request } from "../lib/api";
import { dayMonth } from "../lib/format";
import { PhotoCapture } from "./PhotoCapture";

interface Analysis {
  body_fat_estimate_pct: number;
  muscularity_score: number;
  progress_to_reference_pct: number;
  actor_match_name: string;
  actor_match_why: string;
  visible_changes: string;
  coaching_notes: string;
  confidence: number;
}

interface Photo {
  id: number;
  taken_on: string;
  path: string;
  analysis: Analysis | null;
  body_fat_pct: number | null;
  muscularity: number | null;
  progress_pct: number | null;
  actor_match: string | null;
  confidence: number | null;
  reference_name: string | null;
  error: string | null;
  ok?: boolean;
}

interface Payload {
  photos: Photo[];
  series: { taken_on: string; body_fat_pct: number; muscularity: number; progress_pct: number; actor_match: string; confidence: number }[];
  reference: { name: string; description: string; is_default: boolean };
}

const TOOLTIP = { background: "#171a21", border: "1px solid #2a2f3a", borderRadius: 12, fontSize: 12 };

export const usePhysique = () => useQuery({ queryKey: ["physique"], queryFn: () => get<Payload>("/api/v1/progress/photos") });

/** Photos are behind the bearer token, so <img src> cannot load them directly. */
function useAuthedImage(path: string | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let revoked = false;
    let objectUrl: string | null = null;
    fetch(`/api/v1/photos/${path}`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => {
        if (revoked) return;
        objectUrl = URL.createObjectURL(b);
        setUrl(objectUrl);
      })
      .catch(() => setUrl(null));
    return () => {
      revoked = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  return url;
}

export function PhysiqueSection({ enabled }: { enabled: boolean }) {
  const q = usePhysique();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const photos = q.data?.photos ?? [];
  const latest = photos.find((p) => p.analysis) ?? photos[0] ?? null;
  const ref = q.data?.reference;

  const upload = async (blob: Blob) => {
    setBusy(true);
    setMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", blob, "progress.jpg");
      const res = await fetch("/api/v1/progress/photo", { method: "POST", body: fd, headers: { Authorization: `Bearer ${getToken()}` } });
      const data = (await res.json()) as Photo & { detail?: string };
      if (!res.ok) throw new Error(data.detail ?? `HTTP ${res.status}`);
      setMsg(data.ok ? "Analysed." : data.error ?? "Saved without analysis.");
      await qc.invalidateQueries({ queryKey: ["physique"] });
    } catch (e) {
      setMsg(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card space-y-3">
      <div className="flex items-baseline justify-between">
        <div className="label">physique · daily photo</div>
        {ref && <span className="text-xs text-muted">vs {ref.name}{ref.is_default ? " (describe it in Settings)" : ""}</span>}
      </div>
      {!enabled && <p className="text-sm text-muted">Needs a Gemini key on the Pi. Photos are still stored and can be analysed later.</p>}
      <PhotoCapture onPhoto={(blob) => void upload(blob)} busy={busy} label={photos.length ? "Today's photo" : "First photo"} />
      <p className="text-xs text-muted">Same spot, same light, same pose each day. The photo goes to Gemini for the assessment and stays on your Pi. Numbers are the model's estimates, for the chart only — they never change your targets.</p>
      {msg && <p className="text-xs text-muted">{msg}</p>}
      {latest && <LatestCard photo={latest} referenceName={ref?.name ?? "reference"} onRetry={() => void request("POST", `/api/v1/progress/photos/${latest.id}/reanalyze`, {}).then(() => qc.invalidateQueries({ queryKey: ["physique"] }))} />}
      {q.data && q.data.series.length > 1 && <ProgressChart series={q.data.series} />}
      {photos.length > 0 && <Strip photos={photos} onDelete={(id) => { if (confirm("Delete this photo and its assessment?")) void request("DELETE", `/api/v1/progress/photos/${id}`).then(() => qc.invalidateQueries({ queryKey: ["physique"] })); }} />}
    </section>
  );
}

function LatestCard({ photo, referenceName, onRetry }: { photo: Photo; referenceName: string; onRetry: () => void }) {
  const url = useAuthedImage(photo.path);
  const a = photo.analysis;
  return (
    <div className="flex gap-3">
      {url ? <img src={url} alt="" className="w-24 h-32 object-cover rounded-xl bg-panel-2" /> : <div className="w-24 h-32 rounded-xl bg-panel-2" />}
      <div className="flex-1 text-sm space-y-1 min-w-0">
        <div className="text-xs text-muted">{dayMonth(photo.taken_on)}</div>
        {a ? (
          <>
            <div className="text-2xl font-semibold tabular text-accent">{Math.round(a.progress_to_reference_pct)}%<span className="text-xs text-muted font-normal"> toward {referenceName}</span></div>
            <div><span className="text-muted">looks most like</span> <b>{a.actor_match_name}</b> <span className="text-xs text-muted">— {a.actor_match_why}</span></div>
            <div className="text-xs text-muted tabular">~{a.body_fat_estimate_pct.toFixed(0)}% body fat · muscularity {a.muscularity_score.toFixed(1)}/10 · confidence {Math.round(a.confidence * 100)}%</div>
            {a.visible_changes && <div className="text-xs"><span className="text-muted">changes:</span> {a.visible_changes}</div>}
            {a.coaching_notes && <div className="text-xs"><span className="text-muted">notes:</span> {a.coaching_notes}</div>}
          </>
        ) : (
          <>
            <div className="text-warn text-xs">{photo.error ?? "Not analysed yet."}</div>
            <button className="btn btn-ghost py-1.5 px-3 text-xs" onClick={onRetry}>Analyse now</button>
          </>
        )}
      </div>
    </div>
  );
}

function ProgressChart({ series }: { series: Payload["series"] }) {
  return (
    <div>
      <div className="label mb-1">progress toward reference · body-fat estimate</div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={series} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid stroke="#2a2f3a" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="taken_on" tickFormatter={dayMonth} stroke="#9aa3b2" fontSize={11} minTickGap={24} />
          <YAxis yAxisId="pct" domain={[0, 100]} stroke="#9aa3b2" fontSize={11} width={44} />
          <YAxis yAxisId="bf" orientation="right" domain={["auto", "auto"]} stroke="#9aa3b2" fontSize={11} width={40} />
          <Tooltip contentStyle={TOOLTIP} formatter={(v, name) => [name === "progress_pct" ? `${Math.round(Number(v))}%` : `${Number(v).toFixed(1)}%`, name === "progress_pct" ? "progress" : "body fat"]} />
          <Line yAxisId="pct" type="monotone" dataKey="progress_pct" stroke="#34d399" strokeWidth={2.5} dot={{ r: 3 }} isAnimationActive={false} />
          <Line yAxisId="bf" type="monotone" dataKey="body_fat_pct" stroke="#fbbf24" strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function Strip({ photos, onDelete }: { photos: Photo[]; onDelete: (id: number) => void }) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {photos.slice(0, 14).map((p) => (
        <Thumb key={p.id} photo={p} onDelete={() => onDelete(p.id)} />
      ))}
    </div>
  );
}

function Thumb({ photo, onDelete }: { photo: Photo; onDelete: () => void }) {
  const url = useAuthedImage(photo.path);
  return (
    <div className="relative shrink-0 w-16">
      {url ? <img src={url} alt="" className="w-16 h-20 object-cover rounded-lg bg-panel-2" /> : <div className="w-16 h-20 rounded-lg bg-panel-2" />}
      <div className="text-[10px] text-muted text-center tabular">{dayMonth(photo.taken_on)}{photo.progress_pct != null ? ` · ${Math.round(photo.progress_pct)}%` : ""}</div>
      <button className="absolute top-0.5 right-0.5 text-[10px] bg-black/60 rounded px-1 text-muted" aria-label="delete photo" onClick={onDelete}>✕</button>
    </div>
  );
}

export function PhysiqueReferenceCard() {
  const q = usePhysique();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (q.data) {
      setName(q.data.reference.name);
      setDesc(q.data.reference.description);
    }
  }, [q.data?.reference.name, q.data?.reference.description]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <form className="card space-y-2 text-sm" onSubmit={(e) => { e.preventDefault(); void request("PUT", "/api/v1/settings/physique", { name, description: desc }).then(() => { setMsg("Saved."); void qc.invalidateQueries({ queryKey: ["physique"] }); }).catch((er) => setMsg(describeError(er))); }}>
      <div className="label">reference physique (for progress photos)</div>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. Richardson" />
      <textarea rows={4} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Describe the physique in words: leanness, shoulder width, waist, arms, chest, roughly what body-fat %. Gemini scores each photo against this." />
      <p className="text-xs text-muted">The model needs words, not a name: describe what “{name || "the reference"}” looks like.</p>
      <button className="btn btn-ghost w-full" type="submit" disabled={!name.trim() || !desc.trim()}>Save reference</button>
      {msg && <p className="text-xs text-muted">{msg}</p>}
    </form>
  );
}

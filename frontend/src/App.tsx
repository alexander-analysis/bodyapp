import { useEffect, useState } from "react";

type Health = {
  status: string;
  version: string;
  db: string;
  schema: string | null;
  backup_newest_age_h: number | null;
};

// Milestone 2 shell: proves the build → image → Pi → Tailscale path end to end.
// The real PWA (Today / Log / Weight / Train / Progress / Summary / Settings) is milestone 3.
export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/v1/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", background: "#0f1115", color: "#e6e6e6", minHeight: "100vh", padding: 24 }}>
      <h1 style={{ fontSize: 22, margin: 0 }}>Health</h1>
      <p style={{ opacity: 0.7 }}>Deployment shell — the app arrives in milestone 3.</p>
      {error && <p style={{ color: "#ff6b6b" }}>API unreachable: {error}</p>}
      {health && (
        <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 16px" }}>
          <dt>status</dt><dd>{health.status}</dd>
          <dt>version</dt><dd>{health.version}</dd>
          <dt>db</dt><dd>{health.db} (schema {health.schema ?? "none"})</dd>
          <dt>newest backup</dt><dd>{health.backup_newest_age_h == null ? "none yet" : `${health.backup_newest_age_h} h ago`}</dd>
        </dl>
      )}
    </main>
  );
}

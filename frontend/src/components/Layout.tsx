import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { AuthError, getToken, setToken } from "../lib/api";
import { useToday } from "../lib/hooks";
import { usePending } from "../lib/outbox";
import { InstallBanner } from "./InstallBanner";

const TABS: { to: string; label: string; icon: string }[] = [
  { to: "/", label: "Today", icon: "◎" },
  { to: "/log", label: "Log", icon: "＋" },
  { to: "/weight", label: "Weight", icon: "⚖" },
  { to: "/train", label: "Train", icon: "🏋" },
  { to: "/progress", label: "Progress", icon: "↗" },
  { to: "/settings", label: "More", icon: "≡" },
];

export function Layout() {
  const pending = usePending();
  const today = useToday();
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);

  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  const needsToken = !getToken() || today.error instanceof AuthError;
  const queued = pending.filter((p) => !p.failed).length;
  const failed = pending.filter((p) => p.failed).length;
  const mode = today.data?.mode;

  if (needsToken && location.pathname !== "/settings") {
    return <TokenGate onSaved={() => { void qc.invalidateQueries(); navigate("/"); }} />;
  }

  return (
    <div className="min-h-dvh flex flex-col">
      <header className="sticky top-0 z-20 bg-bg/90 backdrop-blur border-b border-line">
        <div className="mx-auto max-w-lg px-4 h-12 flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm">
            <span className="font-semibold">Health</span>
            {mode && mode.names.length > 0 && (
              <span className="rounded-full bg-warn/15 text-warn px-2 py-0.5 text-xs">{mode.names.join(" · ")}</span>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs text-muted">
            {!online && <span className="rounded-full bg-panel-2 px-2 py-0.5">offline</span>}
            {queued > 0 && (
              <span className="rounded-full bg-accent-2/15 text-accent-2 px-2 py-0.5" title="queued writes">{queued} pending</span>
            )}
            {failed > 0 && (
              <NavLink to="/settings#queue" className="rounded-full bg-danger/15 text-danger px-2 py-0.5">{failed} failed</NavLink>
            )}
          </div>
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-lg px-4 pb-28 pt-4">
        <InstallBanner />
        <Outlet />
      </main>

      <nav className="fixed bottom-0 inset-x-0 z-20 bg-panel/95 backdrop-blur border-t border-line" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
        <div className="mx-auto max-w-lg grid grid-cols-6">
          {TABS.map((t) => (
            <NavLink
              key={t.to}
              to={t.to}
              end={t.to === "/"}
              className={({ isActive }) =>
                `flex flex-col items-center justify-center h-16 text-[11px] gap-0.5 ${isActive ? "text-accent" : "text-muted"}`
              }
            >
              <span className="text-lg leading-none" aria-hidden>{t.icon}</span>
              {t.label}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}

function TokenGate({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="min-h-dvh flex items-center justify-center px-6">
      <form
        className="card w-full max-w-sm space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim().length < 32) return;
          setToken(value);
          onSaved();
        }}
      >
        <div>
          <h1 className="text-xl font-semibold">Health</h1>
          <p className="text-sm text-muted mt-1">Paste the API token from <code>/opt/health/.env</code> on the Pi. It is stored only on this device.</p>
        </div>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="API_BEARER_TOKEN" autoComplete="off" spellCheck={false} />
        <button className="btn btn-primary w-full" type="submit" disabled={value.trim().length < 32}>Continue</button>
      </form>
    </div>
  );
}

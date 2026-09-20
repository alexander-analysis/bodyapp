import { type ReactNode, useEffect } from "react";

/** Bottom sheet: one-handed, thumb-reachable actions. */
export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title?: string; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-30 flex items-end justify-center" role="dialog" aria-modal>
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div
        className="relative w-full max-w-lg bg-panel rounded-t-3xl border-t border-line p-4 max-h-[88dvh] overflow-y-auto"
        style={{ paddingBottom: "calc(1rem + env(safe-area-inset-bottom))" }}
      >
        <div className="mx-auto h-1 w-10 rounded-full bg-line mb-3" />
        {title && <h2 className="text-lg font-semibold mb-3">{title}</h2>}
        {children}
      </div>
    </div>
  );
}

export function Bar({ value, max, color = "bg-accent", warnOver = true }: { value: number; max: number; color?: string; warnOver?: boolean }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const over = warnOver && max > 0 && value > max;
  return (
    <div className="h-2 rounded-full bg-panel-2 overflow-hidden">
      <div className={`h-full rounded-full ${over ? "bg-danger" : color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

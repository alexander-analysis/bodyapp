export const kcal = (n: number | null | undefined) => (n == null ? "–" : Math.round(n).toLocaleString());
export const g = (n: number | null | undefined, d = 0) => (n == null ? "–" : n.toFixed(d));
export const kg = (n: number | null | undefined) => (n == null ? "–" : n.toFixed(1));
export const pct = (n: number | null | undefined) => (n == null ? "–" : `${n.toFixed(1)}%`);
export const signed = (n: number | null | undefined, d = 2) => (n == null ? "–" : `${n > 0 ? "+" : ""}${n.toFixed(d)}`);

export function todayISO(): string {
  const d = new Date();
  const off = d.getTimezoneOffset();
  return new Date(d.getTime() - off * 60_000).toISOString().slice(0, 10);
}

export function shortDay(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

export function dayMonth(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export const MEALS = ["breakfast", "lunch", "dinner", "snack"] as const;

export function guessMeal(): (typeof MEALS)[number] {
  const h = new Date().getHours();
  if (h < 11) return "breakfast";
  if (h < 16) return "lunch";
  if (h < 21) return "dinner";
  return "snack";
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

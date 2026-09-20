// Offline queue (spec 11). Writes go to a Dexie table first, then flush to the API.
// Each op carries a client-generated UUID used as the Idempotency-Key, so a retry
// after a lost response writes exactly once. Flushed on app focus, network regain
// and every 30 s while online; never blocks the UI.

import Dexie, { type Table } from "dexie";
import { useLiveQuery } from "dexie-react-hooks";
import { ApiError, AuthError, OfflineError, request, type Method } from "./api";

export interface Op {
  id?: number;
  key: string; // Idempotency-Key
  method: Exclude<Method, "GET">;
  path: string;
  body?: unknown;
  createdAt: number;
  attempts: number;
  lastError?: string;
  failed?: boolean; // rejected by the server (4xx): kept for the user to see, not retried
  preview?: OpPreview; // what the UI shows while the op is pending
}

export interface OpPreview {
  kind: "entry" | "weight" | "workout" | "other";
  label: string;
  day?: string;
  meal?: string | null;
  kcal?: number;
  protein_g?: number;
  carbs_g?: number;
  fat_g?: number;
  fibre_g?: number;
  grams?: number;
}

class HealthDB extends Dexie {
  outbox!: Table<Op, number>;
  constructor() {
    super("health");
    this.version(1).stores({ outbox: "++id, key, createdAt" });
  }
}

export const db = new HealthDB();

type Listener = () => void;
const listeners = new Set<Listener>();
export function onFlushed(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export async function enqueue(op: Omit<Op, "id" | "key" | "createdAt" | "attempts">): Promise<Op> {
  const full: Op = { ...op, key: uuid(), createdAt: Date.now(), attempts: 0 };
  full.id = await db.outbox.add(full);
  void flush();
  return full;
}

let flushing = false;
let lastResult: "ok" | "offline" | "auth" | "idle" = "idle";
export function flushState() {
  return lastResult;
}

/** Send queued ops in order. Stops at the first network failure so order is kept. */
export async function flush(): Promise<void> {
  if (flushing) return;
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    lastResult = "offline";
    return;
  }
  flushing = true;
  let sentAny = false;
  try {
    // Load everything in order; `failed` is absent on fresh ops, so an index query would skip them.
    const ops = await db.outbox.orderBy("createdAt").toArray();
    for (const op of ops) {
      if (op.failed) continue;
      try {
        await request(op.method, op.path, op.body, { "Idempotency-Key": op.key });
        await db.outbox.delete(op.id!);
        sentAny = true;
        lastResult = "ok";
      } catch (e) {
        if (e instanceof OfflineError) {
          await db.outbox.update(op.id!, { attempts: op.attempts + 1, lastError: e.message });
          lastResult = "offline";
          break;
        }
        if (e instanceof AuthError) {
          lastResult = "auth";
          break;
        }
        if (e instanceof ApiError && e.status >= 500) {
          await db.outbox.update(op.id!, { attempts: op.attempts + 1, lastError: `server ${e.status}` });
          break;
        }
        // 4xx: the server will never accept it; keep it visible, do not retry
        const detail = e instanceof ApiError ? JSON.stringify(e.detail) : (e as Error).message;
        await db.outbox.update(op.id!, { failed: true, lastError: detail });
      }
    }
  } finally {
    flushing = false;
    if (sentAny) listeners.forEach((fn) => fn());
  }
}

export async function discardFailed(id: number): Promise<void> {
  await db.outbox.delete(id);
}

export async function retryFailed(id: number): Promise<void> {
  await db.outbox.update(id, { failed: false, lastError: undefined });
  void flush();
}

export function usePending(): Op[] {
  return useLiveQuery(() => db.outbox.orderBy("createdAt").toArray(), [], [] as Op[]);
}

let installed = false;
export function installFlushTriggers(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  window.addEventListener("online", () => void flush());
  window.addEventListener("focus", () => void flush());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void flush();
  });
  window.setInterval(() => {
    if (navigator.onLine) void flush();
  }, 30_000);
  void flush();
}

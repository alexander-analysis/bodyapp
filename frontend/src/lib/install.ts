// Install-to-home-screen prompt (spec 11): show on first visit, again after three
// sessions if dismissed. iOS Safari never fires beforeinstallprompt, so it gets
// manual instructions instead.

import { useEffect, useState } from "react";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const DISMISSED_AT_SESSION = "health.install.dismissedAtSession";
const SESSIONS = "health.install.sessions";
const REPROMPT_AFTER = 3;

let deferred: BeforeInstallPromptEvent | null = null;
const subs = new Set<() => void>();

if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e as BeforeInstallPromptEvent;
    subs.forEach((s) => s());
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    subs.forEach((s) => s());
  });
  try {
    if (!sessionStorage.getItem("health.session.counted")) {
      sessionStorage.setItem("health.session.counted", "1");
      localStorage.setItem(SESSIONS, String((Number(localStorage.getItem(SESSIONS)) || 0) + 1));
    }
  } catch {
    /* storage unavailable */
  }
}

export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
}

export function isIOS(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  return /iPhone|iPad|iPod/.test(ua) || (ua.includes("Mac") && "ontouchend" in document);
}

function sessions(): number {
  try {
    return Number(localStorage.getItem(SESSIONS)) || 0;
  } catch {
    return 0;
  }
}

export function shouldShowBanner(): boolean {
  if (isStandalone()) return false;
  let dismissedAt: number | null = null;
  try {
    const v = localStorage.getItem(DISMISSED_AT_SESSION);
    dismissedAt = v === null ? null : Number(v);
  } catch {
    /* ignore */
  }
  if (dismissedAt === null) return true;
  return sessions() - dismissedAt >= REPROMPT_AFTER;
}

export function dismissBanner(): void {
  try {
    localStorage.setItem(DISMISSED_AT_SESSION, String(sessions()));
  } catch {
    /* ignore */
  }
}

export function useInstall() {
  const [, tick] = useState(0);
  useEffect(() => {
    const s = () => tick((n) => n + 1);
    subs.add(s);
    return () => {
      subs.delete(s);
    };
  }, []);
  return {
    canPrompt: deferred !== null,
    ios: isIOS() && !isStandalone(),
    standalone: isStandalone(),
    async prompt(): Promise<"accepted" | "dismissed" | "unavailable"> {
      if (!deferred) return "unavailable";
      const ev = deferred;
      deferred = null;
      await ev.prompt();
      const choice = await ev.userChoice;
      subs.forEach((s) => s());
      return choice.outcome;
    },
  };
}

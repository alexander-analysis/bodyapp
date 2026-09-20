import { useEffect, useRef, useState } from "react";

// Spec 11: live overlay with the native BarcodeDetector where available; @zxing/library
// as the fallback for iOS Safari. getUserMedia needs a secure context (Tailscale HTTPS).

type Detector = { detect: (source: ImageBitmapSource) => Promise<{ rawValue: string }[]> };
declare global {
  interface Window {
    BarcodeDetector?: new (opts?: { formats?: string[] }) => Detector;
  }
}

const FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128"];

export function BarcodeScanner({ onCode, active }: { onCode: (code: string) => void; active: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [engine, setEngine] = useState<"native" | "zxing" | null>(null);
  const lastRef = useRef<{ code: string; at: number }>({ code: "", at: 0 });

  useEffect(() => {
    if (!active) return;
    let stream: MediaStream | null = null;
    let stopped = false;
    let timer: number | undefined;
    let zxingControls: { stop: () => void } | null = null;

    const emit = (code: string) => {
      const now = Date.now();
      if (code === lastRef.current.code && now - lastRef.current.at < 2500) return;
      lastRef.current = { code, at: now };
      onCode(code);
    };

    (async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setError("Camera not available in this browser. Type the barcode below.");
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      } catch (e) {
        setError(`Camera permission denied or unavailable (${(e as Error).message}). Type the barcode below.`);
        return;
      }
      if (stopped || !videoRef.current) return;
      const video = videoRef.current;
      video.srcObject = stream;
      await video.play().catch(() => undefined);

      if (window.BarcodeDetector) {
        setEngine("native");
        const det = new window.BarcodeDetector({ formats: FORMATS });
        const tick = async () => {
          if (stopped) return;
          try {
            if (video.readyState >= 2) {
              const found = await det.detect(video);
              if (found.length > 0) emit(found[0].rawValue);
            }
          } catch {
            /* frame not ready */
          }
          timer = window.setTimeout(() => void tick(), 300);
        };
        void tick();
      } else {
        setEngine("zxing");
        try {
          const { BrowserMultiFormatReader } = await import("@zxing/library");
          const reader = new BrowserMultiFormatReader();
          await reader.decodeFromStream(stream, video, (result) => {
            if (result) emit(result.getText());
          });
          zxingControls = { stop: () => reader.reset() };
        } catch (e) {
          setError(`Scanner failed to start (${(e as Error).message}). Type the barcode below.`);
        }
      }
    })();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      zxingControls?.stop();
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [active, onCode]);

  return (
    <div className="space-y-2">
      <div className="relative rounded-2xl overflow-hidden bg-black aspect-[4/3]">
        <video ref={videoRef} className="w-full h-full object-cover" muted playsInline />
        <div className="absolute inset-x-8 top-1/2 -translate-y-1/2 h-24 border-2 border-accent/70 rounded-xl pointer-events-none" />
        {engine && <span className="absolute bottom-2 right-2 text-[10px] text-muted bg-black/50 rounded px-1">{engine}</span>}
      </div>
      {error && <p className="text-xs text-warn">{error}</p>}
    </div>
  );
}

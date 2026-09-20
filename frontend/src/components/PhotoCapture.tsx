import { useRef, useState } from "react";

// Spec 11: downscale on-canvas to 1024 px before upload. On phones the file input with
// capture="environment" opens the camera directly; on desktop it opens a file picker.

const MAX_EDGE = 1024;

export async function downscale(file: File, maxEdge = MAX_EDGE, quality = 0.8): Promise<Blob> {
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) return file;
  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) return file;
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return await new Promise<Blob>((resolve) => canvas.toBlob((b) => resolve(b ?? file), "image/jpeg", quality));
}

export function PhotoCapture({ onPhoto, busy, label = "Take a photo" }: { onPhoto: (blob: Blob, previewUrl: string) => void; busy?: boolean; label?: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  return (
    <div className="space-y-2">
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          const blob = await downscale(f);
          const url = URL.createObjectURL(blob);
          setPreview(url);
          onPhoto(blob, url);
          e.target.value = "";
        }}
      />
      {preview && <img src={preview} alt="" className="w-full rounded-2xl object-cover max-h-72" />}
      <button type="button" className="btn btn-primary w-full" disabled={busy} onClick={() => input.current?.click()}>
        {busy ? "Analysing…" : preview ? "Retake" : label}
      </button>
    </div>
  );
}

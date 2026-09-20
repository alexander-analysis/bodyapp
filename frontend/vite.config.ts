import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: the API runs on :8000; proxy /api so the browser sees one origin.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", sourcemap: false },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the FastAPI backend; production build is served by FastAPI itself.
export default defineConfig({
  // Relative base: the same build works at / (direct) and at /dcc/ (behind Barcode Battles on :8787).
  base: "./",
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8777", "/media": "http://127.0.0.1:8777" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 900 },
});

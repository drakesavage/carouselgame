import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the FastAPI backend; production build is served by FastAPI itself.
export default defineConfig({
  // Relative base: the same build works at / (direct) and behind a reverse proxy under a path prefix.
  base: "./",
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8797", "/media": "http://127.0.0.1:8797", "/tiles": "http://127.0.0.1:8797" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 900 },
});

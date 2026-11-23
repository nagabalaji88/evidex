/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: Vite proxies /api/* to FastAPI on port 8000, so the browser sees one origin.
// Production: `npm run build`, then FastAPI serves dist/ at http://localhost:8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // EVIDEX_API_URL lets a second checkout point at its own API on another port.
    proxy: { "/api": { target: process.env.EVIDEX_API_URL ?? "http://localhost:8000", changeOrigin: false } },
  },
  test: { environment: "jsdom" },
});

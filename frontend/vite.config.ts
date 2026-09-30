import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// Production: `npm run build` writes frontend/dist, which the local Flask server
// serves at http://<server>:5000/app/ (same origin as the API, so no CORS).
// Development: `npm run dev` serves http://127.0.0.1:5173/app/ and proxies /api to
// the Flask server on 127.0.0.1:5000 (live data).
// Prototype: `npm run dev:mock` runs every workspace on mock data with the Role Switcher.
// Mock mode is never part of `npm run build`.
export default defineConfig(({ mode }) => ({
  base: "/app/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@data-source": fileURLToPath(new URL(`./src/services/source.${mode === "mock" ? "mock" : "live"}.ts`, import.meta.url)),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:5000", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    // html2pdf.js (~1 MB) is a separate chunk loaded only when someone clicks "Download PDF".
    chunkSizeWarningLimit: 1000,
  },

}));

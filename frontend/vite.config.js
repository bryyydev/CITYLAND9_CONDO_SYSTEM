import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Production: `npm run build` writes frontend/dist, which the local Flask server
// serves at http://<server>:5000/app/ (same origin as the API, so no CORS).
// Development: `npm run dev` serves http://127.0.0.1:5173/app/ and proxies /api to
// the Flask server on 127.0.0.1:5000. Cookies are per host (not per port), so the
// login session is shared with the legacy pages on :5000.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
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
  },
});

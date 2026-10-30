import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import vuetify from "vite-plugin-vuetify";

// The backend (FastAPI, :8000) is reached through /api; set EHDS_API_URL to point elsewhere.
const api = process.env.EHDS_API_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  server: {
    port: 5173,
    proxy: { "/api": { target: api, changeOrigin: true } },
  },
  preview: {
    port: 5173,
    proxy: { "/api": { target: api, changeOrigin: true } },
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// UI routes (/alerts, /models, ...) share prefixes with the API. Browser page loads
// (Accept: text/html) get the SPA; fetch() calls go to FastAPI.
const UI_ROUTES = /^\/(alerts(\/[^/]+)?|link\/[^/]+|rules|models|patterns|ops)?\/?$/;
const proxy = {
  target: "http://127.0.0.1:8000",
  changeOrigin: true,
  bypass: (req) => (req.headers.accept?.includes("text/html") && UI_ROUTES.test(req.url.split("?")[0]) ? "/index.html" : undefined),
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/events": proxy,
      "/alerts": proxy,
      "/houses": proxy,
      "/patterns": proxy,
      "/devices": proxy,
      "/ai": proxy,
      "/config": proxy,
      "/health": proxy,
      "/auth": proxy,
      "/sim": proxy,
      "/audit": proxy,
      "/live": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});

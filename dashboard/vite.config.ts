/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies the API (and its WebSocket) so the browser only talks
// to one origin. In Docker Compose the API is at http://api:8000.
const apiTarget = process.env.API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, ws: true, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true, // lets Testing Library clean up after each test
  },
});

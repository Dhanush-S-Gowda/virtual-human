import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    host: "0.0.0.0",
    proxy: {
      "/api": {
        target: "http://localhost:8000", ws: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/cv": { target: "ws://localhost:8765", ws: true },
    },
  },
  preview: {
    port: 4173,
    host: "0.0.0.0",
  },
});

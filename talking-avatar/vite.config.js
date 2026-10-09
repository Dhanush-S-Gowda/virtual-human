import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    host: "0.0.0.0",
    proxy: {
      "/api/tts": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/tts/, ""),
      },
      "/api/stt": {
        target: "http://localhost:8001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/stt/, ""),
      },
    },
  },
  preview: {
    port: 4173,
    host: "0.0.0.0",
  },
});


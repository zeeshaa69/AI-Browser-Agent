import { defineConfig } from "vite";

export default defineConfig({
  build: { outDir: "../src/browser_agent/ui_dist", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});

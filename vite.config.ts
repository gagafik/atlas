import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { pwa } from "./tools/pwa.mjs";
export default defineConfig(async ({ mode }) => {
  if (mode === "demo") {
    // Imported lazily: the Docker frontend stage copies only tools/pwa.mjs.
    const { demoPages } = await import("./tools/demo/vite-plugin.mjs");
    return {
      base: "/atlas/",
      plugins: [react(), demoPages()],
      build: { outDir: "dist-demo", emptyOutDir: true },
    };
  }
  return {
    plugins: [react(), pwa()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": "http://127.0.0.1:8000",
        "/published": "http://127.0.0.1:8000",
        "/hall-api": "http://127.0.0.1:8000",
      },
    },
  };
});

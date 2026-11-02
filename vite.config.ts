import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { pwa } from "./tools/pwa.mjs";
export default defineConfig(async ({ mode }) => {
  if (mode === "demo") {
    // The Docker frontend stage copies only tools/pwa.mjs, so the demo plugin
    // must not be visible to the bundler of this config (esbuild resolves even
    // a literal dynamic import). A file URL built at run time is resolved only
    // here, in the demo build, relative to this file.
    const pluginUrl = new URL("./tools/demo/vite-plugin.mjs", import.meta.url).href;
    const { demoPages } = await import(/* @vite-ignore */ pluginUrl);
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

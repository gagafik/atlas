import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { pwa } from "./tools/pwa.mjs";
export default defineConfig({
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
});

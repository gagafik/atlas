import { readdirSync, readFileSync } from "node:fs";
import { servedText } from "./served.mjs";

const SNAPSHOT_DIR = new URL("./snapshot/", import.meta.url);

/**
 * Vite plugin for the GitHub Pages demo build only (`vite build --mode demo`).
 * It points index.html at the demo entry and publishes the committed per-role
 * snapshots as static assets (served.mjs withdraws the parts of the journal
 * they hold no answers for). The normal build never loads this file.
 */
export function demoPages() {
  return {
    name: "atlas-demo-pages",
    apply: "build",
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        return html
          .replace("/src/main.tsx", "/src/demo/main.ts")
          .replace(/\s*<link rel="manifest"[^>]*>/, "")
          .replace(/<title>[^<]*<\/title>/, "<title>Атлас · демо</title>");
      },
    },
    generateBundle() {
      for (const name of readdirSync(SNAPSHOT_DIR)) {
        if (!name.endsWith(".json")) continue;
        this.emitFile({
          type: "asset",
          fileName: `demo-data/${name}`,
          source: servedText(readFileSync(new URL(name, SNAPSHOT_DIR), "utf8")),
        });
      }
    },
  };
}

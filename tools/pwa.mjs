import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

export function pwa() {
  return {
    name: "atlas-public-shell",
    apply: "build",
    generateBundle() {
      const files = [
        "/offline.html",
        "/manifest.webmanifest",
        "/icons/atlas-192.png",
        "/icons/atlas-512.png",
      ];
      const source = readFileSync(
        new URL("../src/service-worker.js", import.meta.url),
        "utf8",
      );
      const hash = createHash("sha256").update(source);
      for (const file of files)
        hash.update(readFileSync(new URL(`../public${file}`, import.meta.url)));
      this.emitFile({
        type: "asset",
        fileName: "sw.js",
        source: source.replace(
          "__SHELL_VERSION__",
          hash.digest("hex").slice(0, 16),
        ),
      });
    },
  };
}

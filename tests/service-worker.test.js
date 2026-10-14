import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

function worker() {
  const handlers = {};
  const fallback = new Response("public offline shell");
  const cache = {
    addAll: vi.fn(async () => {}),
    match: vi.fn(async () => fallback),
  };
  const caches = {
    open: vi.fn(async () => cache),
    keys: vi.fn(async () => ["atlas-public-shell-old", "unrelated-cache"]),
    delete: vi.fn(async () => true),
  };
  const fetch = vi.fn(async () => new Response("online app"));
  const self = {
    location: { origin: "https://school.example" },
    clients: { claim: vi.fn(async () => {}) },
    addEventListener: (name, fn) => {
      handlers[name] = fn;
    },
  };
  runInNewContext(
    readFileSync(new URL("../src/service-worker.js", import.meta.url), "utf8"),
    { self, caches, fetch, URL, Response },
  );
  return { handlers, cache, caches, fetch, fallback, self };
}
function request(path, options = {}) {
  return {
    url: "https://school.example" + path,
    method: "GET",
    mode: "cors",
    ...options,
  };
}

describe("public offline shell", () => {
  it("caches only explicit public resources and removes only its own older cache", async () => {
    const w = worker();
    let pending;
    w.handlers.install({
      waitUntil: (value) => {
        pending = value;
      },
    });
    await pending;
    expect(w.cache.addAll.mock.calls[0][0]).toEqual([
      "/offline.html",
      "/manifest.webmanifest",
      "/icons/atlas-192.png",
      "/icons/atlas-512.png",
    ]);
    w.handlers.activate({
      waitUntil: (value) => {
        pending = value;
      },
    });
    await pending;
    expect(w.caches.delete.mock.calls).toEqual([["atlas-public-shell-old"]]);
    expect(w.self.clients.claim).toHaveBeenCalledOnce();
  });
  it("never intercepts private API, public bearer documents, mutations or cross-origin requests", () => {
    const w = worker();
    const respondWith = vi.fn();
    for (const r of [
      request("/api/state"),
      request("/api/portal/schedule?entityId=s1"),
      request("/api/notifications/inbox"),
      request("/published/secret"),
      request("/", { method: "POST", mode: "navigate" }),
      request("/assets/app.js"),
      request("/offline.html?private=1"),
      request("/offline.html", { url: "https://other.example/offline.html" }),
    ]) {
      w.handlers.fetch({ request: r, respondWith });
    }
    expect(respondWith).not.toHaveBeenCalled();
    expect(w.caches.open).not.toHaveBeenCalled();
  });
  it("uses the network for app navigation and falls back without storing the response", async () => {
    const w = worker();
    let pending;
    const event = {
      request: request("/", { mode: "navigate" }),
      respondWith: (value) => {
        pending = value;
      },
    };
    w.handlers.fetch(event);
    expect(await (await pending).text()).toBe("online app");
    expect(w.cache.match).not.toHaveBeenCalled();
    w.fetch.mockRejectedValueOnce(new Error("offline"));
    w.handlers.fetch(event);
    expect(await pending).toBe(w.fallback);
    w.fetch.mockResolvedValueOnce(new Response("error", { status: 503 }));
    w.handlers.fetch(event);
    expect(await pending).toBe(w.fallback);
  });
});

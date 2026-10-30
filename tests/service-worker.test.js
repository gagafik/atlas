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
    registration: { showNotification: vi.fn(async () => {}) },
    clients: {
      claim: vi.fn(async () => {}),
      matchAll: vi.fn(async () => []),
      openWindow: vi.fn(async () => {}),
    },
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

describe("private background notifications", () => {
  async function shown(data) {
    const w = worker();
    let pending;
    w.handlers.push({
      data,
      waitUntil: (value) => {
        pending = value;
      },
    });
    await pending;
    const [title, options] = w.self.registration.showNotification.mock.calls[0];
    expect(w.fetch).not.toHaveBeenCalled();
    expect(w.caches.open).not.toHaveBeenCalled();
    return { title, options };
  }
  const generic =
    "В Атласе есть обновления. Откройте приложение, чтобы посмотреть.";
  it("shows only the fixed label of a known category even if a payload contains personal data or a link", async () => {
    const json = vi.fn(() => ({
      category: "grades",
      body: "Private pupil and grade",
      url: "https://attacker.example",
      title: "Secret",
      studentName: "Private",
    }));
    const { title, options } = await shown({ json });
    expect(title).toBe("Атлас");
    expect(options).toEqual({
      body: "Новая оценка. Откройте приложение, чтобы посмотреть.",
      icon: "/icons/atlas-192.png",
      tag: "atlas-updates",
    });
    expect(JSON.stringify(options)).not.toContain("Private");
  });
  it("falls back to generic text for an unknown category, a prototype name, bad JSON or no payload", async () => {
    for (const data of [
      { json: () => ({ category: "constructor" }) },
      { json: () => ({ category: "__proto__" }) },
      { json: () => ({ category: "Secret grade" }) },
      { json: () => ({ category: 7 }) },
      { json: () => null },
      {
        json: () => {
          throw new SyntaxError("bad payload");
        },
      },
      undefined,
    ]) {
      const { options } = await shown(data);
      expect(options.body).toBe(generic);
    }
  });
  it("opens the inbox without following links in the notification", async () => {
    const w = worker();
    let pending;
    const close = vi.fn();
    const event = {
      notification: { close, data: { url: "https://attacker.example" } },
      waitUntil: (value) => {
        pending = value;
      },
    };
    const wrong = { url: "https://other.example/", focus: vi.fn() };
    const publication = {
      url: "https://school.example/published/private",
      focus: vi.fn(),
    };
    w.self.clients.matchAll.mockResolvedValue([wrong, publication]);
    w.handlers.notificationclick(event);
    await pending;
    expect(w.self.clients.openWindow).toHaveBeenCalledWith("/?notifications=1");
    const app = {
      url: "https://school.example/",
      focus: vi.fn(async () => {}),
      postMessage: vi.fn(),
    };
    w.self.clients.matchAll.mockResolvedValue([wrong, publication, app]);
    w.handlers.notificationclick(event);
    await pending;
    expect(app.focus).toHaveBeenCalledOnce();
    expect(app.postMessage).toHaveBeenCalledWith({
      type: "atlas-open-notifications",
    });
    expect(wrong.focus).not.toHaveBeenCalled();
    expect(publication.focus).not.toHaveBeenCalled();
  });
  it("requires the user to re-enrol after a provider replaces the subscription", async () => {
    const w = worker();
    let pending;
    const app = { postMessage: vi.fn() };
    w.self.clients.matchAll.mockResolvedValue([app]);
    w.handlers.pushsubscriptionchange({
      waitUntil: (value) => {
        pending = value;
      },
    });
    await pending;
    expect(app.postMessage).toHaveBeenCalledWith({
      type: "atlas-push-changed",
    });
    expect(w.fetch).not.toHaveBeenCalled();
  });
});

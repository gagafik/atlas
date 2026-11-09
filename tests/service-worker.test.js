import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { PUSH_LABELS } from "../src/notifications";

function worker() {
  const handlers = {};
  const fallback = new Response("public offline shell");
  // The caches of the browser, by name, holding the paths stored in each.
  const store = new Map([
    ["atlas-public-shell-old", new Set()],
    ["unrelated-cache", new Set()],
  ]);
  let opened = "";
  const cache = {
    addAll: vi.fn(async (paths) => {
      for (const path of paths) store.get(opened).add(path);
    }),
    match: vi.fn(async () => fallback),
  };
  const caches = {
    open: vi.fn(async (name) => {
      opened = name;
      if (!store.has(name)) store.set(name, new Set());
      return cache;
    }),
    keys: vi.fn(async () => [...store.keys()]),
    delete: vi.fn(async (name) => store.delete(name)),
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
  return { handlers, cache, caches, store, fetch, fallback, self };
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
    const current = [...w.store.keys()].filter(
      (name) => !["atlas-public-shell-old", "unrelated-cache"].includes(name),
    );
    expect(current).toHaveLength(1);
    const cached = [...w.store.get(current[0])];
    expect(cached).toContain("/offline.html");
    for (const path of cached)
      expect(path).not.toMatch(/^\/(api|published)\//);
    w.handlers.activate({
      waitUntil: (value) => {
        pending = value;
      },
    });
    await pending;
    expect([...w.store.keys()].sort()).toEqual(
      [current[0], "unrelated-cache"].sort(),
    );
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
  // A push with no payload at all: the text the worker shows when it knows nothing of the notice.
  const bodyOf = async (data) => (await shown(data)).options.body;
  it("shows only the fixed label of a known category even if a payload contains personal data or a link", async () => {
    const json = vi.fn(() => ({
      category: "grades",
      body: "Private pupil and grade",
      url: "https://attacker.example",
      title: "Secret",
      studentName: "Private",
    }));
    const { title, options } = await shown({ json });
    const everything = JSON.stringify({ title, options });
    for (const leak of ["Private", "Secret", "attacker.example"])
      expect(everything).not.toContain(leak);
    // The same text as for the bare category, and the one the app itself names for it (two copies of the table).
    expect(options.body).toBe(
      await bodyOf({ json: () => ({ category: "grades" }) }),
    );
    expect(options.body.startsWith(PUSH_LABELS.grades)).toBe(true);
  });
  it("falls back to generic text for an unknown category, a prototype name, bad JSON or no payload", async () => {
    const generic = await bodyOf(undefined);
    expect(generic).not.toBe(
      await bodyOf({ json: () => ({ category: "grades" }) }),
    );
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
    ])
      expect(await bodyOf(data)).toBe(generic);
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

import { describe, expect, it, vi } from "vitest";
import { installClock } from "./clock";
import { createDemoFetch, type DemoSnapshot } from "./runtime";

const ORIGIN = "http://demo.test";
const snapshot: DemoSnapshot = {
  capturedAt: "2026-11-02T11:30:00+05:00",
  readPosts: [
    { pattern: "^/api/report$" },
    { pattern: "^/api/presence$", anyBody: true },
  ],
  silent: [{ method: "PUT", pattern: "^/api/threads/[^/]+/read$" }],
  entries: {
    "GET /api/day?day=2026-11-02": { s: 200, b: { lessons: [1, 2] } },
    "POST /api/report#{\"a\":1,\"b\":{\"c\":2}}": { s: 200, b: { rows: 3 } },
    "POST /api/presence": { s: 200, b: { peers: [] } },
    "GET /api/gate": { s: 403, b: { detail: "closed" } },
  },
};

function setup() {
  const realFetch = vi.fn(async () => new Response("real"));
  const log = vi.fn();
  const demoFetch = createDemoFetch(
    snapshot,
    realFetch as unknown as typeof fetch,
    log,
    ORIGIN,
  );
  return { realFetch, log, demoFetch };
}

describe("demo fetch", () => {
  it("answers a captured GET regardless of workspace and parameter order", async () => {
    const { demoFetch, realFetch } = setup();
    const response = await demoFetch(
      `${ORIGIN}/api/day?workspace=school&day=2026-11-02`,
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ lessons: [1, 2] });
    expect(realFetch).not.toHaveBeenCalled();
  });

  it("replays a captured error status", async () => {
    const { demoFetch } = setup();
    expect((await demoFetch(`${ORIGIN}/api/gate`)).status).toBe(403);
  });

  it("answers a missing GET with JSON 404", async () => {
    const { demoFetch } = setup();
    const response = await demoFetch(`${ORIGIN}/api/day?day=2026-11-03`);
    expect(response.status).toBe(404);
    expect(typeof (await response.json()).detail).toBe("string");
  });

  it.each(["PUT", "POST", "DELETE", "PATCH"])(
    "refuses %s writes without reaching the network",
    async (method) => {
      const { demoFetch, realFetch } = setup();
      const response = await demoFetch(`${ORIGIN}/api/entry`, {
        method,
        body: "{}",
      });
      expect(response.status).toBe(403);
      expect(typeof (await response.json()).detail).toBe("string");
      expect(realFetch).not.toHaveBeenCalled();
    },
  );

  it("matches a read POST by content, not by key order or token", async () => {
    const { demoFetch } = setup();
    const response = await demoFetch(`${ORIGIN}/api/report?workspace=school`, {
      method: "POST",
      body: JSON.stringify({ token: "x", b: { c: 2 }, a: 1 }),
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ rows: 3 });
  });

  it("refuses a read POST with a body that was not captured", async () => {
    const { demoFetch } = setup();
    const response = await demoFetch(`${ORIGIN}/api/report`, {
      method: "POST",
      body: JSON.stringify({ a: 2 }),
    });
    expect(response.status).toBe(404);
  });

  it("answers a body-agnostic read POST for any body", async () => {
    const { demoFetch } = setup();
    const response = await demoFetch(`${ORIGIN}/api/presence`, {
      method: "POST",
      body: JSON.stringify({ anything: Math.random() }),
    });
    expect(await response.json()).toEqual({ peers: [] });
  });

  it("lets quiet background writes succeed", async () => {
    const { demoFetch } = setup();
    const response = await demoFetch(`${ORIGIN}/api/threads/abc/read`, {
      method: "PUT",
    });
    expect(response.status).toBe(200);
  });

  it("sends assets and other origins to the real fetch", async () => {
    const { demoFetch, realFetch } = setup();
    await demoFetch(`${ORIGIN}/atlas/assets/index.js`);
    await demoFetch("https://example.org/api/day");
    expect(realFetch).toHaveBeenCalledTimes(2);
  });

  it("reads the method and URL from a Request object", async () => {
    const { demoFetch, realFetch } = setup();
    const response = await demoFetch(
      new Request(`${ORIGIN}/api/entry`, { method: "PUT", body: "{}" }),
    );
    expect(response.status).toBe(403);
    expect(realFetch).not.toHaveBeenCalled();
  });
});

describe("demo clock", () => {
  it("freezes the current time but leaves explicit dates alone", () => {
    const target = { Date } as { Date: DateConstructor };
    const restore = installClock(snapshot.capturedAt, target);
    try {
      const Frozen = target.Date;
      const frozen = Date.parse(snapshot.capturedAt);
      expect(new Frozen().getTime()).toBe(frozen);
      expect(Frozen.now()).toBe(frozen);
      expect(Frozen()).toBe(new Date(frozen).toString());
      expect(new Frozen(2026, 0, 1).getFullYear()).toBe(2026);
      expect(new Frozen(2026, 0, 1).getTime()).not.toBe(frozen);
      expect(new Frozen("2020-05-05T00:00:00Z").getTime()).toBe(
        Date.parse("2020-05-05T00:00:00Z"),
      );
      expect(new Frozen() instanceof Frozen).toBe(true);
      expect(new Frozen() instanceof Date).toBe(true);
      expect(Frozen.UTC(2020, 0, 1)).toBe(Date.UTC(2020, 0, 1));
    } finally {
      restore();
    }
    expect(target.Date).toBe(Date);
  });
});

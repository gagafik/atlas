import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { canonicalJson, getKey, postKey } from "../src/demo/requestKey";
import { createDemoFetch, type DemoSnapshot } from "../src/demo/runtime";
import * as twin from "../tools/demo/request-key.mjs";
import { scrubSnapshot } from "../tools/demo/scrub.mjs";

// The browser runtime and the capture tool must derive identical request keys.
const urls = [
  "http://x/api/journal/day?day=2026-11-02&workspace=school",
  "http://x/api/messages/feed",
  "http://x/api/portal/schedule?view=student&entityId=s0&day=2026-11-02",
  "http://x/api/forms/items/abc/responses?limit=50&after=&workspace=school",
  "http://x/api/a?b=2&b=1&a=%D0%B0",
];
const bodies = [
  { start: "2026-10-05", end: "2026-11-02", status: "all", token: "t" },
  { b: [{ z: 1, a: [null, true] }], a: "яз" },
  { empty: {}, list: [], nothing: null },
];

describe("demo request keys", () => {
  it.each(urls)("GET key matches the Node twin for %s", (address) => {
    const url = new URL(address);
    expect(getKey(url)).toBe(twin.getKey(url));
  });

  it.each(bodies)("POST key matches the Node twin for %j", (body) => {
    const url = new URL("http://x/api/journal/coverage/report?workspace=school");
    const text = JSON.stringify(body);
    expect(postKey(url, text)).toBe(twin.postKey(url, text));
    expect(canonicalJson(body)).toBe(twin.canonicalJson(body));
  });

  it("ignores key order, workspace and the token", () => {
    const url = new URL("http://x/api/r?workspace=school");
    expect(postKey(url, '{"a":1,"b":2,"token":"1"}')).toBe(
      postKey(new URL("http://x/api/r"), '{"token":"2","b":2,"a":1}'),
    );
  });
});

// The committed snapshot, not a stub: what a viewer of the published demo gets.
describe("committed administrator snapshot", () => {
  const origin = "http://demo.test";
  const snapshot = JSON.parse(
    readFileSync(new URL("../tools/demo/snapshot/admin.json", import.meta.url), "utf8"),
  ) as DemoSnapshot;
  const realFetch = vi.fn(async () => new Response("real"));
  const demoFetch = createDemoFetch(snapshot, realFetch as unknown as typeof fetch, () => {}, origin);

  it("keeps the signed-in administrator and refuses writes", async () => {
    const status = await demoFetch(`${origin}/api/auth/status?workspace=school`);
    expect(status.status).toBe(200);
    expect((await status.json()).user.role).toBe("admin");
    const write = await demoFetch(`${origin}/api/journal/entry?workspace=school`, {
      method: "PUT",
      body: "{}",
    });
    expect(write.status).toBe(403);
    expect(realFetch).not.toHaveBeenCalled();
  });
});

describe("snapshot scrubbing", () => {
  const snapshot = (body: unknown) => ({
    capturedAt: "2026-11-02T11:30:00+05:00",
    readPosts: [],
    silent: [],
    entries: { "GET /api/x": { s: 200, b: body } },
  });

  it("replaces secret-like values and keeps the shape", () => {
    const clean = scrubSnapshot(snapshot({ token: "abc", nested: { endpointHash: "x", note: "ok" }, count: 3 }), "someone");
    expect(clean.entries["GET /api/x"].b).toEqual({ token: "demo", nested: { endpointHash: "demo", note: "ok" }, count: 3 });
  });

  it.each([
    ["a Windows path", { file: "C:\Users\someone\data\school.db" }],
    ["a Unix home path", { file: "/home/someone/.env" }],
    ["a loopback address", { server: "http://127.0.0.1:8000" }],
    ["an e-mail address", { contact: "teacher@school.example" }],
    ["a password hash", { note: "scrypt$16384$8$1$abcd" }],
    ["the operating system user", { note: "report by someone" }],
  ])("refuses to write a snapshot with %s", (_label, body) => {
    expect(() => scrubSnapshot(snapshot(body), "someone")).toThrow(/leak/);
  });
});

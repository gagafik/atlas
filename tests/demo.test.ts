import { describe, expect, it } from "vitest";
import { canonicalJson, getKey, postKey } from "../src/demo/requestKey";
import * as twin from "../tools/demo/request-key.mjs";

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

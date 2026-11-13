import { afterEach, describe, expect, it, vi } from "vitest";
import {
  allPages,
  can,
  changedFields,
  exportHref,
  readBase64,
  staySince,
  MAX_UPLOAD,
  type Options,
} from "./registerTypes";

const options = (capabilities: Options["capabilities"]): Options => ({
  admin: false,
  capabilities,
  today: "2026-10-24",
  now: "2026-10-24T10:00:00+00:00",
  familySummaries: false,
  family: false,
});

describe("register links", () => {
  const parsed = (href: string) => new URL(href, "http://x");
  it("skips empty filters", () => {
    const url = parsed(
      exportHref("visitors/export", "demo", { day: "2026-10-01", q: "", roomId: undefined }),
    );
    expect(url.pathname).toBe("/api/registers/visitors/export");
    expect(url.searchParams.get("workspace")).toBe("demo");
    expect(url.searchParams.get("day")).toBe("2026-10-01");
    expect(url.searchParams.has("q")).toBe(false);
    expect(url.searchParams.has("roomId")).toBe(false);
  });
  it("hands Cyrillic values and separators over unchanged", () => {
    const value = "Стол 1 & стул=2";
    const url = parsed(exportHref("inventory/export", "school", { q: value }));
    expect(url.pathname).toBe("/api/registers/inventory/export");
    expect(url.searchParams.get("workspace")).toBe("school");
    expect(url.searchParams.get("q")).toBe(value);
    expect([...url.searchParams.keys()].sort()).toEqual(["q", "workspace"]);
  });
  it("needs no filters at all", () => {
    const url = parsed(exportHref("inventory/export", "demo"));
    expect([...url.searchParams.entries()]).toEqual([["workspace", "demo"]]);
  });
});

describe("time in the building", () => {
  const now = Date.parse("2026-10-24T10:00:00Z");
  it("shows minutes and padded hours", () => {
    expect(staySince("2026-10-24T08:55:00Z", now)).toBe("1 ч 05 мин");
    expect(staySince("2026-10-24T09:48:00Z", now)).toBe("12 мин");
    expect(staySince("2026-10-24T09:59:40Z", now)).toBe("0 мин");
  });
  it("never goes negative and counts days for forgotten departures", () => {
    expect(staySince("2026-10-24T10:30:00Z", now)).toBe("0 мин");
    expect(staySince("2026-10-22T07:00:00Z", now)).toBe("2 сут 3 ч");
  });
});

describe("capabilities", () => {
  it("opens only the registers the server granted", () => {
    expect(can(options(["visitors"]), "visitors")).toBe(true);
    expect(can(options(["visitors"]), "incidents")).toBe(false);
    expect(can(null, "inventory")).toBe(false);
  });
});

describe("history of edits", () => {
  it("names a changed field once and not the ones that stayed", () => {
    expect(
      changedFields(
        { name: "А", note: "", badge: "1" },
        { name: "Б", note: "", badge: "1" },
      ),
    ).toHaveLength(1);
  });
  it("names two different fields differently, and one field kept under two keys once", () => {
    const [first, second] = changedFields(
      { name: "А", badge: "1" },
      { name: "Б", badge: "2" },
    );
    expect(first).not.toBe(second);
    expect(
      changedFields({ hostId: "1", hostName: "А" }, { hostId: "2", hostName: "Б" }),
    ).toHaveLength(1);
  });
  it("sees a change deep inside a list", () => {
    expect(
      changedFields(
        { parentNotices: [] },
        { parentNotices: [{ by: "Кл. руководитель" }] },
      ),
    ).toHaveLength(1);
  });
  it("names nothing when nothing changed or one side is missing", () => {
    expect(changedFields({ name: "А" }, { name: "А" })).toEqual([]);
    expect(changedFields(null, { name: "Б" })).toEqual([]);
  });
});

describe("file reading", () => {
  // Node has no FileReader: a stand-in that reads any file, so only the size limit can refuse one.
  afterEach(() => vi.unstubAllGlobals());
  const readsAnything = () =>
    vi.stubGlobal(
      "FileReader",
      class {
        result = "data:;base64,AAAA";
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        readAsDataURL() {
          queueMicrotask(() => this.onload?.());
        }
      },
    );
  it("refuses files over the upload limit without reading them", async () => {
    readsAnything();
    const big = new File([new Uint8Array(MAX_UPLOAD + 1)], "big.csv");
    await expect(readBase64(big)).rejects.toThrow();
  });
  it("reads a file of exactly the upload limit", async () => {
    readsAnything();
    const edge = new File([new Uint8Array(MAX_UPLOAD)], "edge.csv");
    await expect(readBase64(edge)).resolves.toBe("AAAA");
  });
});

describe("printing a paged list", () => {
  const page = (items: number[], nextBefore: number | null) => ({
    items,
    nextBefore,
  });
  it("follows every page and keeps the order they arrived in", async () => {
    const pages: Record<number, ReturnType<typeof page>> = {
      30: page([3, 2], 10),
      10: page([1], null),
    };
    const asked: number[] = [];
    const all = await allPages(page([5, 4], 30), async (before) => {
      asked.push(before);
      return pages[before];
    });
    expect(all.items).toEqual([5, 4, 3, 2, 1]);
    expect(all.nextBefore).toBeNull();
    expect(asked).toEqual([30, 10]);
  });
  it("asks for nothing when the list is already complete", async () => {
    const first = page([1, 2], null);
    let calls = 0;
    const all = await allPages(first, async () => {
      calls++;
      return page([], null);
    });
    expect(all).toEqual(first);
    expect(calls).toBe(0);
  });
  it("refuses to print a list that never ends", async () => {
    await expect(
      allPages(page([1], 9), async (before) => page([before], before - 1), 3),
    ).rejects.toThrow();
  });
});

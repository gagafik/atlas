import { describe, expect, it } from "vitest";
import {
  allPages,
  can,
  capabilityNames,
  changedFields,
  conditions,
  documentTypes,
  exportHref,
  historyActions,
  readBase64,
  staySince,
  statuses,
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
  it("skips empty filters and encodes Cyrillic values", () => {
    expect(
      exportHref("visitors/export", "demo", { day: "2026-10-01", q: "" }),
    ).toBe("/api/registers/visitors/export?workspace=demo&day=2026-10-01");
    expect(
      exportHref("inventory/export", "school", {
        q: "Стол 1",
        roomId: undefined,
      }),
    ).toBe(
      "/api/registers/inventory/export?workspace=school&q=%D0%A1%D1%82%D0%BE%D0%BB%201",
    );
    expect(exportHref("inventory/export", "demo")).toBe(
      "/api/registers/inventory/export?workspace=demo",
    );
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
  it("names every register in Russian", () => {
    expect(Object.keys(capabilityNames).sort()).toEqual([
      "incidents",
      "inventory",
      "visitors",
    ]);
    expect(
      Object.values(capabilityNames).every((name) => /[А-Яа-я]/.test(name)),
    ).toBe(true);
  });
});

describe("labels", () => {
  it("covers every state and action the server can return", () => {
    for (const key of [
      "inside",
      "left",
      "voided",
      "open",
      "closed",
      "active",
      "written_off",
    ])
      expect(statuses[key]).toBeTruthy();
    for (const key of [
      "arrived",
      "departed",
      "voided",
      "edited",
      "created",
      "close",
      "reopen",
      "publish_summary",
      "withdraw_summary",
      "moved",
      "written_off",
      "restored",
      "imported",
      "configured",
    ])
      expect(historyActions[key]).toBeTruthy();
    for (const key of ["new", "good", "fair", "poor", "broken"])
      expect(conditions[key]).toBeTruthy();
    for (const key of [
      "passport",
      "id_card",
      "driver_license",
      "service_id",
      "other",
    ])
      expect(documentTypes[key]).toBeTruthy();
  });
});

describe("history of edits", () => {
  it("names only the fields that changed", () => {
    expect(
      changedFields(
        { name: "А", note: "", badge: "1" },
        { name: "Б", note: "", badge: "1" },
      ),
    ).toEqual(["Посетитель"]);
    expect(
      changedFields(
        { parentNotices: [] },
        { parentNotices: [{ by: "Кл. руководитель" }] },
      ),
    ).toEqual(["Сообщения родителям"]);
    expect(changedFields(null, { name: "Б" })).toEqual([]);
  });
});

describe("file reading", () => {
  it("refuses files over the 2 MB limit without reading them", async () => {
    const big = new File([new Uint8Array(MAX_UPLOAD + 1)], "big.csv");
    await expect(readBase64(big)).rejects.toThrow("Файл больше 2 МБ.");
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
    expect(all).toBe(first);
    expect(calls).toBe(0);
  });
  it("refuses to print a list that never ends", async () => {
    await expect(
      allPages(page([1], 9), async (before) => page([before], before - 1), 3),
    ).rejects.toThrow("Список слишком длинный для печати.");
  });
});

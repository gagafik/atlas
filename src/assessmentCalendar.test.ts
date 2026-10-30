import { describe, expect, it } from "vitest";
import {
  actionLabels,
  addDays,
  coordinatorRoles,
  dayLabel,
  keepChoices,
  loadClass,
  menuRoles,
  mondayOf,
  monthRange,
  monthWeeks,
  shiftMonth,
  weekday,
} from "./assessmentCalendar";

describe("assessment calendar dates", () => {
  it("builds Monday-first weeks that cover the whole month", () => {
    const weeks = monthWeeks("2026-08");
    expect(weeks[0][0]).toBe("2026-07-27");
    expect(weeks[0].indexOf("2026-08-01")).toBe(5);
    expect(weeks.every((week) => week.length === 7)).toBe(true);
    expect(weeks.at(-1)!.at(-1)).toBe("2026-09-06");
    const february = monthWeeks("2027-02").flat();
    expect(february[0]).toBe("2027-02-01");
    expect(february).toContain("2027-02-28");
    expect(february.at(-1)).toBe("2027-02-28");
  });
  it("finds Mondays and shifts days across month and year ends", () => {
    expect(mondayOf("2026-08-23")).toBe("2026-08-17");
    expect(mondayOf("2026-08-17")).toBe("2026-08-17");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
  });
  it("gives the first and last day of a month", () => {
    expect(monthRange("2026-09")).toEqual({
      start: "2026-09-01",
      end: "2026-09-30",
    });
    expect(monthRange("2028-02").end).toBe("2028-02-29");
  });
  it("labels days without a time zone shift", () => {
    expect(dayLabel("2026-08-17")).toBe("17.08.2026");
    expect(weekday("2026-08-17")).toBe("Пн");
    expect(weekday("2026-08-23")).toBe("Вс");
  });
  it("marks a load only when it exceeds a limit", () => {
    expect(loadClass(2, 1)).toBe("over");
    expect(loadClass(1, 1)).toBe("");
    expect(loadClass(5, null)).toBe("");
  });
});

const pair = (groupId: string, subjectId: string) =>
  ({ groupId, subjectId }) as never;
const type = (id: string, active = true) => ({ id, active }) as never;
describe("booking form choices", () => {
  const options = {
    pairs: [pair("g-a", "sub2"), pair("g-d", "sub2")],
    types: [type("sor"), type("old", false), type("soch")],
  };
  it("keeps a pair and a type that still exist", () => {
    expect(
      keepChoices(
        { mode: "create", pair: "g-d|sub2", typeId: "soch" },
        options,
      ),
    ).toEqual({ pair: "g-d|sub2", typeId: "soch" });
  });
  it("drops what belongs to another workspace or is switched off", () => {
    expect(
      keepChoices({ mode: "create", pair: "g-x|sub9", typeId: "old" }, options),
    ).toEqual({ pair: "", typeId: "sor" });
    expect(
      keepChoices({ mode: "create", pair: "", typeId: "quiz" }, options).typeId,
    ).toBe("sor");
  });
  it("picks the only pair at once", () => {
    const single = { ...options, pairs: [pair("g-a", "sub2")] };
    expect(
      keepChoices({ mode: "create", pair: "g-x|sub9", typeId: "sor" }, single)
        .pair,
    ).toBe("g-a|sub2");
  });
  it("leaves a move as it was opened", () => {
    expect(
      keepChoices({ mode: "move", pair: "g-x|sub9", typeId: "old" }, options),
    ).toEqual({ pair: "g-x|sub9", typeId: "old" });
  });
});

describe("module access in the client", () => {
  it("shows the menu to every role that can be named coordinator", () => {
    for (const role of coordinatorRoles) expect(menuRoles).toContain(role);
    expect(menuRoles).not.toContain("viewer");
  });
});

describe("booking history labels", () => {
  it("names every action the server writes for a booking in Russian", () => {
    for (const action of [
      "created",
      "published",
      "edited",
      "moved",
      "cancelled",
      "journal_linked",
      "journal_unlinked",
    ])
      expect(actionLabels[action]).toMatch(/[А-Яа-я]/);
    expect(actionLabels.journal_linked).toBe("Связана с работой журнала");
    expect(actionLabels.journal_unlinked).toBe("Связь с журналом снята");
  });
});

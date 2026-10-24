import { describe, expect, it } from "vitest";
import {
  addDays,
  dayLabel,
  loadClass,
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

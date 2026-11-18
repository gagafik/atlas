import { describe, expect, it } from "vitest";
import { formatSchoolDate, schoolYearText, weekShape } from "./settingsSummary";

describe("settings summary", () => {
  it("writes dates day first", () => {
    expect(formatSchoolDate("2026-10-05")).toBe("05.10.2026");
    expect(formatSchoolDate("2027-05-28")).toBe("28.05.2027");
    expect(formatSchoolDate("2028-02-29")).toBe("29.02.2028");
    expect(schoolYearText("2026-10-05", "2027-05-28")).toBe(
      "05.10.2026 — 28.05.2027",
    );
  });
  it("returns what is not a date as it came", () => {
    expect(formatSchoolDate("")).toBe("");
    expect(formatSchoolDate("скоро")).toBe("скоро");
    expect(formatSchoolDate("2027-02-30")).toBe("2027-02-30");
  });
  it("follows the numbers in word forms", () => {
    expect(weekShape(1, 1)).toBe("1 день · 1 смена");
    expect(weekShape(2, 2)).toBe("2 дня · 2 смены");
    expect(weekShape(5, 5)).toBe("5 дней · 5 смен");
    expect(weekShape(11, 11)).toBe("11 дней · 11 смен");
    expect(weekShape(21, 21)).toBe("21 день · 21 смена");
  });
});

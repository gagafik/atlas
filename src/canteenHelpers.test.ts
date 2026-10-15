import { afterEach, expect, it, vi } from "vitest";
import { addDays, schoolDay, schoolInput, schoolISO } from "./canteenHelpers";
afterEach(() => vi.useRealTimers());
it("uses the school date across UTC midnight and calendar boundaries", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-12-31T19:00:00Z"));
  expect(schoolDay()).toBe("2027-01-01");
  expect(schoolInput("2026-12-31T19:00:00Z")).toBe("2027-01-01T00:00");
  expect(new Date(schoolISO("2027-01-01T00:00")).toISOString()).toBe(
    "2026-12-31T19:00:00.000Z",
  );
  expect(addDays("2028-02-28", 1)).toBe("2028-02-29");
  expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
});

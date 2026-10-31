import { describe, expect, it } from "vitest";
import { clip, hours, monthOf, weekOf } from "./workloadReport";

describe("workload periods", () => {
  it("takes the Monday and Sunday of the week, across a month and a year", () => {
    expect(weekOf("2026-08-19")).toEqual({
      start: "2026-08-17",
      end: "2026-08-23",
    });
    expect(weekOf("2026-12-31")).toEqual({
      start: "2026-12-28",
      end: "2027-01-03",
    });
    expect(weekOf("2027-01-01")).toEqual({
      start: "2026-12-28",
      end: "2027-01-03",
    });
    expect(weekOf("2026-09-01")).toEqual({
      start: "2026-08-31",
      end: "2026-09-06",
    });
  });
  it("keeps Sunday in its own week and Monday starts the next", () => {
    expect(weekOf("2026-08-23").start).toBe("2026-08-17");
    expect(weekOf("2026-08-24").start).toBe("2026-08-24");
  });
  it("takes the whole calendar month, February included", () => {
    expect(monthOf("2027-02-10")).toEqual({
      start: "2027-02-01",
      end: "2027-02-28",
    });
    expect(monthOf("2028-02-29")).toEqual({
      start: "2028-02-01",
      end: "2028-02-29",
    });
    expect(monthOf("2026-12-05")).toEqual({
      start: "2026-12-01",
      end: "2026-12-31",
    });
  });
  it("clips a preset to the school year", () => {
    const year = { yearStart: "2026-08-17", yearEnd: "2027-06-11" };
    expect(clip(monthOf("2026-08-20"), year)).toEqual({
      start: "2026-08-17",
      end: "2026-08-31",
    });
    expect(clip(monthOf("2027-06-01"), year).end).toBe("2027-06-11");
  });
});

describe("workload hours", () => {
  it("shows clock minutes as hours with one decimal and a comma", () => {
    expect(hours(0)).toBe("0,0");
    expect(hours(90)).toBe("1,5");
    expect(hours(100)).toBe("1,7");
    expect(hours(2400)).toBe("40,0");
  });
});

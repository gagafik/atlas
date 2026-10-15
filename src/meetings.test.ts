import { describe, expect, it } from "vitest";
import { schoolInput, schoolISO, blankDefinition } from "./meetings";
describe("school meeting time", () => {
  it("keeps 17:00 school time independently of device zone", () => {
    expect(schoolISO("2027-04-05T17:00")).toBe("2027-04-05T12:00:00.000Z");
    expect(schoolInput("2027-04-05T12:00:00Z")).toBe("2027-04-05T17:00");
  });
  it("handles a UTC date boundary", () =>
    expect(schoolInput("2027-04-05T23:30:00Z")).toBe("2027-04-06T04:30"));
  it("rejects impossible dates and incomplete input", () => {
    expect(() => schoolISO("2027-02-30T17:00")).toThrow();
    expect(() => schoolISO("")).toThrow();
  });
  it("defaults to the school's 17–19 ten-minute meetings", () => {
    const d = blankDefinition();
    expect(schoolInput(d.startsAt).slice(11)).toBe("17:00");
    expect(schoolInput(d.endsAt).slice(11)).toBe("19:00");
    expect(d.slotMinutes).toBe(10);
  });
});

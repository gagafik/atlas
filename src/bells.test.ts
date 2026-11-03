import { describe, expect, it } from "vitest";
import { placeInSlots, profileFor, slotsFor } from "./bells";
import type { BellProfile } from "./journal";

const slot = (label: string, start: string, end: string) => ({
  label,
  start,
  end,
});
const profile = (dayPeriods: BellProfile["dayPeriods"] = {}): BellProfile => ({
  id: "p",
  name: "Основной",
  periods: [slot("1", "09:30", "10:15"), slot("2", "10:25", "11:10")],
  dayPeriods,
});
const lesson = (start: string, end: string, subject = "Математика") => ({
  start,
  end,
  subject,
});

describe("slotsFor", () => {
  it("uses the weekday's own bells (Monday is 0) and the common ones for other days", () => {
    const monday = [slot("1", "08:00", "08:45"), slot("2", "08:55", "09:40")];
    const bells = profile({ "0": monday });
    expect(slotsFor(bells, "2026-11-02")).toEqual(monday);
    expect(slotsFor(bells, "2026-11-08")).toEqual(bells.periods);
  });
});

describe("placeInSlots", () => {
  const slots = profile().periods;
  it("puts a lesson into the period it overlaps most and keeps empty periods", () => {
    const rows = placeInSlots(slots, [lesson("09:30", "10:15")]);
    expect(rows.map((r) => r.items.length)).toEqual([1, 0]);
    expect(rows[1].label).toBe("2");
  });
  it("chooses by the larger overlap and sends a tie to the earlier period", () => {
    const later = placeInSlots(slots, [lesson("10:05", "10:50")]);
    expect(later.map((r) => r.items.length)).toEqual([0, 1]);
    const tie = placeInSlots(slots, [lesson("10:00", "10:40")]);
    expect(tie.map((r) => r.items.length)).toEqual([1, 0]);
  });
  it("keeps parallel lessons together", () => {
    const rows = placeInSlots(slots, [
      lesson("09:30", "10:15", "Химия"),
      lesson("09:30", "10:15", "Биология"),
    ]);
    expect(rows[0].items.map((i) => i.subject)).toEqual(["Биология", "Химия"]);
  });
  it("gives a lesson outside every bell a row of its own, in time order", () => {
    const rows = placeInSlots(slots, [lesson("18:00", "18:45", "Кружок")]);
    expect(rows.map((r) => r.slot)).toEqual([0, 1, null]);
    expect(rows[2].items[0].subject).toBe("Кружок");
    const early = placeInSlots(slots, [lesson("08:00", "08:45")]);
    expect(early.map((r) => r.slot)).toEqual([null, 0, 1]);
  });
  it("places nothing but still lists every period when there are no lessons", () => {
    expect(placeInSlots(slots, []).map((r) => r.slot)).toEqual([0, 1]);
    expect(
      placeInSlots([], [lesson("09:30", "10:15")]).map((r) => r.slot),
    ).toEqual([null]);
  });
});

describe("profileFor", () => {
  const other = { ...profile(), id: "q" };
  const options = {
    students: [
      { id: "a", profileId: "q" },
      { id: "b", profileId: null },
    ],
    profiles: [profile(), other],
  };
  it("takes the profile of the pupil's class, else the first one", () => {
    expect(profileFor(options, "a")?.id).toBe("q");
    expect(profileFor(options, "b")?.id).toBe("p");
    expect(profileFor({ students: [] }, "a")).toBeUndefined();
  });
});

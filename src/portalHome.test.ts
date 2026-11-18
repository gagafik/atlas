import { describe, expect, it } from "vitest";
import { dateInWords, lessonLine, type HomeRow } from "./portalHome";

const row = (over: Partial<HomeRow> = {}): HomeRow => ({
  start: "08:30",
  end: "09:15",
  subject: "Алгебра",
  group: "9А",
  teachers: "Иванова А.",
  rooms: "204",
  status: "planned",
  activity: "lesson",
  ...over,
});

describe("dateInWords", () => {
  it("names the weekday and the month in the genitive", () => {
    expect(dateInWords(new Date(2026, 10, 18))).toBe("среда, 18 ноября");
    expect(dateInWords(new Date(2026, 0, 4))).toBe("воскресенье, 4 января");
  });
});

describe("lessonLine", () => {
  it("shows a planned lesson quietly with teacher and room", () => {
    const l = lessonLine(row(), false);
    expect(l).toMatchObject({
      time: "08:30–09:15",
      title: "Алгебра · 9А",
      details: "Иванова А. · 204",
      tone: "planned",
      cancelled: false,
      current: false,
    });
  });
  it("says so when no teacher is assigned and leaves out an empty room", () => {
    expect(lessonLine(row({ teachers: "", rooms: "" }), false).details).toBe(
      "Учитель не назначен",
    );
  });
  it("marks a cancelled lesson with its own tone and label", () => {
    const l = lessonLine(row({ status: "cancelled" }), false);
    expect([l.tone, l.state, l.cancelled]).toEqual([
      "cancelled",
      "Отменено",
      true,
    ]);
  });
  it("marks a changed lesson as changed and keeps the current flag", () => {
    const l = lessonLine(row({ status: "changed" }), true);
    expect([l.tone, l.state, l.current]).toEqual(["changed", "Изменено", true]);
  });
  it("keeps a regular duty and a booking quiet, not as a timetable change", () => {
    for (const activity of ["duty", "booking"]) {
      const l = lessonLine(row({ activity }), false);
      expect(l.tone).toBe("planned");
      expect(l.state).not.toBe("По расписанию");
    }
  });
  it("puts everything into the tooltip text", () => {
    expect(lessonLine(row(), false).full).toContain("Иванова А.");
  });
});

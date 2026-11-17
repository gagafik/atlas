import { describe, expect, it } from "vitest";
import {
  KINDS,
  clip,
  filterLessons,
  hours,
  lessonCounts,
  lessonNotes,
  lessonSelection,
  monthOf,
  outcomeTone,
  partsLine,
  reportWeeks,
  validPeriod,
  weekLabel,
  weekOf,
  weekdayDate,
  within,
  type WorkloadLesson,
} from "./workloadReport";

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
  it("finds no period for a preset wholly outside the school year", () => {
    const year = { yearStart: "2026-08-17", yearEnd: "2027-06-11" };
    expect(within(weekOf("2027-07-14"), year)).toBeNull();
    expect(within(monthOf("2026-07-10"), year)).toBeNull();
    expect(within(weekOf("2027-06-09"), year)).toEqual({
      start: "2027-06-07",
      end: "2027-06-11",
    });
  });
});

describe("typed workload periods", () => {
  it("accepts a day, a span and nothing that is empty or reversed", () => {
    expect(validPeriod("2026-08-17", "2026-08-17")).toBe(true);
    expect(validPeriod("2026-08-17", "2026-09-30")).toBe(true);
    expect(validPeriod("", "2026-09-30")).toBe(false);
    expect(validPeriod("2026-08-17", "")).toBe(false);
    expect(validPeriod("2026-09-30", "2026-08-17")).toBe(false);
  });
});

describe("workload hours", () => {
  it("turns clock minutes into hours", () => {
    const value = (minutes: number) => Number(hours(minutes).replace(",", "."));
    expect(value(0)).toBe(0);
    expect(value(90)).toBe(1.5);
    expect(value(100)).toBeCloseTo(1.7, 1);
    expect(value(2400)).toBe(40);
  });
});

const lesson = (over: Partial<WorkloadLesson> = {}): WorkloadLesson => ({
  date: "2026-11-02",
  start: "08:30",
  end: "09:15",
  group: "7А",
  subject: "Алгебра",
  minutes: 45,
  outcome: "taught",
  kind: "own",
  confirmed: true,
  differs: false,
  absent: false,
  ...over,
});

describe("workload row labels", () => {
  it("names the weekday of a date, in UTC, across a year end", () => {
    expect(weekdayDate("2026-11-02")).toBe("Пн 02.11");
    expect(weekdayDate("2026-11-08")).toBe("Вс 08.11");
    expect(weekdayDate("2027-01-01")).toBe("Пт 01.01");
  });
  it("writes a week as two short dates", () => {
    expect(weekLabel({ start: "2026-11-02", end: "2026-11-08" })).toBe(
      "02.11 – 08.11",
    );
  });
  it("lists only the parts that are not zero", () => {
    expect(
      partsLine([
        ["свои", 12],
        ["замены", 1],
        ["дополнительные", 0],
      ]),
    ).toBe("свои 12, замены 1");
    expect(partsLine([["свои", 0]])).toBe("");
  });
});

describe("workload lesson notes", () => {
  it("says nothing about a confirmed lesson without a kind that matches the timetable", () => {
    expect(lessonNotes(lesson({ kind: null }))).toBe("");
  });
  it("names the kind of a taught lesson and says when the journal did not confirm it", () => {
    const notes = lessonNotes(
      lesson({ kind: "substitution", confirmed: false }),
    );
    expect(notes.split(" · ")).toHaveLength(2);
    expect(notes).toContain(KINDS.substitution);
  });
  it("keeps the kind of a lesson that was not taught out of the notes", () => {
    expect(lessonNotes(lesson({ outcome: "cancelled", kind: "own" }))).toBe("");
  });
  it("marks a difference from the timetable and an absent teacher who did not teach", () => {
    const missed = lessonNotes(
      lesson({ outcome: "uncovered", kind: null, absent: true, differs: true }),
    );
    expect(missed.split(" · ")).toHaveLength(2);
    // An absent teacher who taught anyway (a lesson on the day of the absence) is not flagged.
    expect(lessonNotes(lesson({ kind: null, absent: true }))).toBe("");
  });
});

describe("workload outcomes", () => {
  it("gives every outcome a tone, unknown ones the muted one", () => {
    expect(outcomeTone("taught")).toBe("positive");
    expect(outcomeTone("upcoming")).toBe("neutral");
    expect(outcomeTone("uncovered")).toBe("danger");
    expect(outcomeTone("cancelled")).toBe("danger");
    expect(outcomeTone("merged")).toBe("muted");
    expect(outcomeTone("something-new")).toBe("muted");
  });
});

describe("workload lesson filters", () => {
  const all = [
    lesson({ date: "2026-11-02" }),
    lesson({ date: "2026-11-03", outcome: "upcoming" }),
    lesson({ date: "2026-11-04", outcome: "merged" }),
    lesson({ date: "2026-11-10", outcome: "replaced" }),
    lesson({ date: "2026-11-11", outcome: "from-the-future" }),
    lesson({ date: "2026-11-12" }),
  ];
  const week = { start: "2026-11-02", end: "2026-11-08" };
  it("counts the lessons of the whole period by group", () => {
    expect(lessonCounts(all, null)).toEqual({
      all: 6,
      taught: 2,
      upcoming: 1,
      other: 3,
    });
  });
  it("counts only the selected week, its first and last day included", () => {
    expect(lessonCounts(all, week)).toEqual({
      all: 3,
      taught: 1,
      upcoming: 1,
      other: 1,
    });
    expect(
      lessonCounts(
        [lesson({ date: "2026-11-02" }), lesson({ date: "2026-11-08" })],
        week,
      ).all,
    ).toBe(2);
  });
  it("puts every outcome that is neither taught nor upcoming into «not taught»", () => {
    const other = filterLessons(all, "other", null).map((l) => l.outcome);
    expect(other).toEqual(["merged", "replaced", "from-the-future"]);
  });
  it("narrows by the filter and the week together and keeps the order", () => {
    expect(filterLessons(all, "all", null)).toHaveLength(6);
    expect(filterLessons(all, "taught", week).map((l) => l.date)).toEqual([
      "2026-11-02",
    ]);
    expect(
      filterLessons(all, "upcoming", {
        start: "2026-11-09",
        end: "2026-11-15",
      }),
    ).toHaveLength(0);
  });
  it("words the narrowing for the printed sheet, and says nothing when there is none", () => {
    expect(lessonSelection(null, "all")).toBe("");
    expect(lessonSelection(week, "all")).toBe("Неделя 02.11 – 08.11");
    expect(lessonSelection(null, "other")).toBe("Не проведено");
    expect(lessonSelection(week, "other")).toBe(
      "Неделя 02.11 – 08.11 · Не проведено",
    );
  });
});

describe("workload weeks of a report", () => {
  it("takes the weeks of all teachers once, in order", () => {
    const w1 = { start: "2026-11-02", end: "2026-11-08" };
    const w2 = { start: "2026-11-09", end: "2026-11-15" };
    expect(reportWeeks([{ weeks: [w2] }, { weeks: [w1, w2] }, {}])).toEqual([
      w1,
      w2,
    ]);
    expect(reportWeeks([])).toEqual([]);
  });
});

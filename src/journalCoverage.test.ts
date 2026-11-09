import { describe, it, expect } from "vitest";
import {
  classCardLines,
  drillFilters,
  gridWindow,
  runLine,
  summaryFilters,
  weekdays,
  type ClassSummary,
  type Query,
} from "./journalCoverage";

const card: ClassSummary = {
  classId: "c1",
  className: "10A",
  classTeacher: null,
  expected: 4,
  unfilled: 0,
  rosterUnknown: 0,
  lastRecord: null,
  unfilledSince: null,
  unbound: 0,
};

describe("weekdays", () => {
  it("lists the seven days from Monday, as the server counts them", () => {
    expect(weekdays).toHaveLength(7);
    expect(weekdays[0]).toBe("Понедельник");
    expect(weekdays[4]).toBe("Пятница");
    expect(weekdays[6]).toBe("Воскресенье");
  });
});

describe("classCardLines", () => {
  const warned = {
    ...card,
    classTeacher: "Иванова А.",
    expected: 11,
    unfilled: 9,
    lastRecord: "2026-08-14",
    unfilledSince: "2026-08-17",
    unbound: 3,
    rosterUnknown: 5,
  };
  it.each(["unbound", "rosterUnknown", "pendingDays"] as const)(
    "adds a warning for %s only when it is not zero",
    (field) => {
      const quiet = classCardLines({ ...card, [field]: 0 }).length;
      expect(classCardLines({ ...card, [field]: 3 }).length).toBe(quiet + 1);
    },
  );
  it("carries the counts it warns about", () => {
    const text = classCardLines(warned).join("\n");
    for (const count of ["Иванова А.", "9", "11", "3", "5"])
      expect(text).toContain(count);
  });
});

describe("runLine", () => {
  const detail = { lessons: 3, sent: 5, skipped: 7 };
  it("carries the counts of a run", () => {
    const line = runLine({ kind: "lessons", onDate: "2026-08-19", detail });
    for (const count of ["3", "5", "7"]) expect(line).toContain(count);
  });
  it("tells a lesson reminder from the weekly digest", () => {
    const run = (kind: "lessons" | "digest") =>
      runLine({ kind, onDate: "2026-08-19", detail });
    expect(run("lessons")).not.toBe(run("digest"));
  });
  it("tells a postponed run from one that found nothing to send", () => {
    const run = (detail: object) =>
      runLine({ kind: "lessons", onDate: "2026-08-22", detail });
    expect(run({ note: "not a school day" })).not.toBe(
      run({ lessons: 0, sent: 0 }),
    );
  });
});

describe("summaryFilters and drillFilters", () => {
  const form = (): Query => ({
    start: "2026-08-17",
    end: "2026-08-23",
    mode: "teaching",
    classId: "c-demo-d",
    teacherId: "t2",
    subjectId: "sub-eng",
    status: "complete",
  });
  it("counts every teacher of every class, keeping the period and the subject", () => {
    expect(summaryFilters(form())).toEqual({
      ...form(),
      mode: "classes",
      classId: "",
      teacherId: "",
      status: "all",
    });
  });
  it("lists the lessons a card counted: a teacher picked in the form is not applied", () => {
    expect(drillFilters(form(), "c-demo-a")).toEqual({
      start: "2026-08-17",
      end: "2026-08-23",
      mode: "classes",
      classId: "c-demo-a",
      teacherId: "",
      subjectId: "sub-eng",
      status: "issues",
    });
  });
});

describe("gridWindow", () => {
  it("covers the weeks on show and cuts them to the school year", () => {
    // 1 October 2026 is a Thursday: the grid starts on Monday 28 September and runs six weeks.
    expect(gridWindow("2026-10", "2026-08-17", "2027-05-29")).toEqual({
      start: "2026-09-28",
      end: "2026-11-08",
    });
    expect(gridWindow("2026-08", "2026-08-17", "2027-05-29")?.start).toBe(
      "2026-08-17",
    );
    expect(gridWindow("2027-05", "2026-08-17", "2027-05-29")?.end).toBe(
      "2027-05-29",
    );
  });
  it("gives nothing for a month whose weeks lie outside the year", () => {
    expect(gridWindow("2026-07", "2026-08-17", "2027-05-29")).toBeNull();
    expect(gridWindow("2027-06", "2026-08-17", "2027-05-29")).toBeNull();
  });
});

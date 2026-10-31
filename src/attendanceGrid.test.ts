import { describe, it, expect } from "vitest";
import {
  candidates,
  clampRange,
  confirmable,
  marksPayload,
  rangeOf,
  rateText,
  selectLine,
  selectRange,
  shift,
  slots,
  statusMark,
  type Candidate,
  type Counts,
  type Grid,
  type GridDay,
  type GridLesson,
} from "./attendanceGrid";

const counts: Counts = {
  present: 0,
  absent: 0,
  late: 0,
  excused: 0,
  left: 0,
  unmarked: 0,
  noRecord: 0,
};
const rates = {
  marked: 0,
  attendedPercent: null,
  absentPercent: null,
  excusedPercent: null,
};
const lesson = (key: string, start: string, canEdit = true): GridLesson => ({
  lessonKey: key,
  start,
  end: "09:00",
  subject: key,
  subjectId: key,
  group: "10A",
  teachers: "",
  status: "scheduled",
  state: "published",
  hasDraftChanges: false,
  frozen: false,
  archived: false,
  canEdit,
  version: 1,
  publishedVersion: 1,
});
const day = (
  date: string,
  lessons: GridLesson[],
  canConfirm = true,
): GridDay => ({
  date,
  lessons,
  confirmation: {
    status: "none",
    version: 0,
    updated: null,
    actor: null,
    expected: true,
    canConfirm,
  },
  locked: [],
  counts,
  rates,
  openLessons: 0,
});
// Two days; the second lesson of the first day is closed for editing, s2 has no cell in "math".
const grid: Grid = {
  classId: "a",
  className: "10A",
  level: "edit",
  start: "2026-08-17",
  end: "2026-08-18",
  today: "2026-08-18",
  canEdit: true,
  codes: [],
  days: [
    day("2026-08-17", [lesson("eng", "08:20"), lesson("math", "09:10", false)]),
    day("2026-08-18", [lesson("hist", "08:20")], false),
  ],
  pupils: ["s1", "s2"].map((id) => ({
    id,
    name: id,
    active: true,
    counts,
    rates,
  })),
  cells: {
    eng: {
      s1: ["absent", "excused", 0, "illness"],
      s2: ["late", "late", 7, null],
    },
    math: { s1: [null, null, 0, null] },
    hist: { s1: ["present", "present", 0, null], s2: [null, null, 0, null] },
  },
  totals: { counts, rates },
};

describe("selectRange", () => {
  const order = ["a", "b", "c", "d", "e"];
  it("covers the cells between two clicks in either direction", () => {
    expect(selectRange(order, "b", "d")).toEqual(["b", "c", "d"]);
    expect(selectRange(order, "d", "b")).toEqual(["b", "c", "d"]);
    expect(selectRange(order, "c", "c")).toEqual(["c"]);
  });
  it("falls back to the clicked cell when the first one is unknown", () => {
    expect(selectRange(order, "x", "c")).toEqual(["c"]);
    expect(selectRange(order, "a", "x")).toEqual([]);
  });
});

describe("selectLine", () => {
  const cells = candidates(grid);
  it("takes every editable cell of a pupil, lesson or day and none that is disabled", () => {
    const pupil = selectLine(cells, { studentId: "s1" });
    expect(pupil.map((c) => c.lessonKey)).toEqual(["eng", "hist"]);
    expect(
      selectLine(cells, { lessonKey: "eng" }).map((c) => c.studentId),
    ).toEqual(["s1", "s2"]);
    expect(
      selectLine(cells, { date: "2026-08-17" }).map((c) => c.lessonKey),
    ).toEqual(["eng", "eng"]);
    expect(selectLine(cells, { lessonKey: "math" })).toEqual([]);
  });
  it("selects nothing in a grid the account may only read", () => {
    const readonly = candidates({ ...grid, canEdit: false });
    expect(selectLine(readonly, { studentId: "s1" })).toEqual([]);
    expect(readonly.some((c: Candidate) => c.editable)).toBe(false);
  });
});

describe("marksPayload", () => {
  it("carries the mark and the minutes the client saw, and leaves out cells it may not change", () => {
    const body = marksPayload(
      [
        { lessonKey: "eng", studentId: "s2" },
        { lessonKey: "eng", studentId: "s1" },
        { lessonKey: "math", studentId: "s1" },
        { lessonKey: "eng", studentId: "nobody" },
      ],
      grid,
      "present",
      9,
      "Проверено",
      "id",
      "a",
    );
    expect(body.cells).toEqual([
      {
        lessonKey: "eng",
        day: "2026-08-17",
        studentId: "s2",
        expected: "late",
        expectedLate: 7,
      },
      {
        lessonKey: "eng",
        day: "2026-08-17",
        studentId: "s1",
        expected: "absent",
        expectedLate: 0,
      },
    ]);
    // Minutes belong to a late mark only.
    expect(body.lateMinutes).toBe(0);
    expect(
      marksPayload(
        [{ lessonKey: "hist", studentId: "s2" }],
        grid,
        "late",
        5,
        "",
        "id",
        "a",
      ).cells[0],
    ).toMatchObject({ expected: null, expectedLate: 0 });
    expect(
      marksPayload(
        [{ lessonKey: "hist", studentId: "s2" }],
        grid,
        "late",
        5,
        "",
        "id",
        "a",
      ).lateMinutes,
    ).toBe(5);
  });
});

describe("rateText", () => {
  it("shows a placeholder, never zero, while nothing is marked", () => {
    const placeholder = rateText(null);
    expect(rateText(undefined)).toBe(placeholder);
    expect(rateText(0)).not.toBe(placeholder);
    expect(rateText(83.3)).not.toBe(placeholder);
    expect(rateText(0)).not.toBe(rateText(83.3));
  });
});

describe("periods", () => {
  it("moves a month end into the next month and a week by seven days", () => {
    expect(shift({ view: "month", anchor: "2026-08-31" }, 1)).toEqual({
      view: "month",
      anchor: "2026-09-01",
    });
    expect(rangeOf(shift({ view: "month", anchor: "2026-01-31" }, 1))).toEqual({
      start: "2026-02-01",
      end: "2026-02-28",
    });
    expect(shift({ view: "month", anchor: "2026-01-15" }, -1).anchor).toBe(
      "2025-12-01",
    );
    expect(rangeOf(shift({ view: "week", anchor: "2026-08-31" }, 1))).toEqual({
      start: "2026-09-07",
      end: "2026-09-13",
    });
    expect(shift({ view: "day", anchor: "2026-08-31" }, 1).anchor).toBe(
      "2026-09-01",
    );
  });
  it("starts a week on Monday and stays inside the school year", () => {
    expect(rangeOf({ view: "week", anchor: "2026-08-23" })).toEqual({
      start: "2026-08-17",
      end: "2026-08-23",
    });
    expect(
      clampRange(
        { start: "2026-08-01", end: "2026-08-31" },
        "2026-08-17",
        "2027-06-11",
      ),
    ).toEqual({
      start: "2026-08-17",
      end: "2026-08-31",
    });
  });
});

describe("slots and confirmation", () => {
  it("groups lessons that start together", () => {
    const parallel = day("2026-08-17", [
      lesson("b", "09:10"),
      lesson("a", "08:20"),
      lesson("c", "09:10"),
    ]);
    expect(slots(parallel).map((s) => [s.start, s.lessons.length])).toEqual([
      ["08:20", 1],
      ["09:10", 2],
    ]);
  });
  it("lists only the unconfirmed days that can be confirmed, with the version seen", () => {
    expect(confirmable(grid.days)).toEqual([
      { date: "2026-08-17", version: 0 },
    ]);
    const done = { ...grid.days[0].confirmation, status: "confirmed" as const };
    expect(confirmable([{ ...grid.days[0], confirmation: done }])).toEqual([]);
  });
  it("tells a missing record from an empty mark, and a note from the raw absence", () => {
    const missing = statusMark(null, null),
      empty = statusMark("unmarked", "unmarked"),
      absent = statusMark("absent", "absent"),
      excused = statusMark("absent", "excused");
    expect(
      new Set([missing.tone, empty.tone, absent.tone, excused.tone]).size,
    ).toBe(4);
    expect(missing.letter).toBe("");
    expect(empty.letter).not.toBe("");
  });
});

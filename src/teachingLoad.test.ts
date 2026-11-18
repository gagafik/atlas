import { describe, expect, it } from "vitest";
import {
  DEFAULT_SORT,
  NO_FILTERS,
  NO_TEACHER,
  PAGE_SIZE,
  buildRows,
  clampPage,
  filterOptions,
  filterRows,
  nextSort,
  pageCount,
  pageRows,
  sortRows,
  totals,
  type LoadFilters,
  type LoadSchool,
  type SortSpec,
} from "./teachingLoad";
import type { Workload } from "./types";

const load = (id: string, over: Partial<Workload>): Workload => ({
  id,
  groupId: "g5a",
  teacherId: "t1",
  subjectId: "math",
  hours: 3,
  maxPerDay: 2,
  roomIds: [],
  profileId: "p",
  block: "",
  allowedPeriods: [],
  ...over,
});

// Classes are listed out of order on purpose: the school order is grade, then name.
const school: LoadSchool = {
  classes: [
    { id: "c10", name: "10Б", maxDaily: 7, profileId: "p" },
    { id: "c5b", name: "5Б", maxDaily: 7, profileId: "p" },
    { id: "c5a", name: "5А", maxDaily: 7, profileId: "p" },
    { id: "c9", name: "9А", maxDaily: 7, profileId: "p" },
  ],
  groups: [
    { id: "g5a", name: "5А", classIds: ["c5a"], studentIds: [], kind: "whole" },
    { id: "g5b", name: "5Б", classIds: ["c5b"], studentIds: [], kind: "whole" },
    {
      id: "g10",
      name: "10Б",
      classIds: ["c10"],
      studentIds: [],
      kind: "whole",
    },
    {
      id: "gen",
      name: "Английский 5",
      classIds: ["c5b", "c5a"],
      studentIds: [],
      kind: "elective",
    },
  ],
  subjects: [
    { id: "math", name: "Математика", color: "#000" },
    { id: "eng", name: "Английский язык", color: "#000" },
    { id: "art", name: "Рисование", color: "#000" },
  ],
  teachers: [
    { id: "t2", name: "Яков Орлов", unavailable: [] },
    { id: "t1", name: "Анна Белова", unavailable: [] },
  ],
  workloads: [
    load("w1", {}),
    load("w2", { groupId: "g5b", subjectId: "eng", teacherId: "t2", hours: 2 }),
    load("w3", { groupId: "g10", subjectId: "art", teacherId: null, hours: 1 }),
    load("w4", {
      groupId: "gen",
      subjectId: "eng",
      teacherId: "t1",
      additionalTeacherIds: ["t2"],
      hours: 4,
      block: "Б2",
    }),
    load("w5", {
      groupId: "g10",
      subjectId: "math",
      teacherId: null,
      teacherRequired: false,
      hours: 5,
    }),
    load("w6", { groupId: "g5b", subjectId: "art", cycleIds: ["odd"] }),
  ],
};
// w1 is fully placed, w2 half placed, w4 over-placed; the rest has nothing.
const counts = { w1: 3, w2: 1, w4: 5 };
const rows = buildRows(school, counts, "weekly");
const ids = (list: { id: string }[]) => list.map((r) => r.id);
const withFilter = (patch: Partial<LoadFilters>, search = "") =>
  ids(filterRows(rows, { ...NO_FILTERS, ...patch }, search)).sort();

describe("filters", () => {
  it("keeps everything without a filter", () => {
    expect(withFilter({})).toEqual(["w1", "w2", "w3", "w4", "w5", "w6"]);
  });

  it("filters by class: a group of two classes belongs to each of them", () => {
    expect(withFilter({ classId: "c5a" })).toEqual(["w1", "w4"]);
    expect(withFilter({ classId: "c5b" })).toEqual(["w2", "w4", "w6"]);
    expect(withFilter({ classId: "c9" })).toEqual([]);
  });

  it("filters by teacher, an additional teacher included, and by no teacher", () => {
    expect(withFilter({ teacherId: "t2" })).toEqual(["w2", "w4"]);
    expect(withFilter({ teacherId: NO_TEACHER })).toEqual(["w3", "w5"]);
  });

  it("filters by subject", () => {
    expect(withFilter({ subjectId: "eng" })).toEqual(["w2", "w4"]);
  });

  it("state: not placed means the placed lessons differ from the hours of the cycle", () => {
    // w6 is not taught in this cycle: nothing is required and nothing is placed.
    expect(withFilter({ state: "unplaced" })).toEqual(["w2", "w3", "w4", "w5"]);
  });

  it("state: without teacher keeps only the assignments that need one", () => {
    expect(withFilter({ state: "noTeacher" })).toEqual(["w3"]);
  });

  it("state: not taught in this cycle, and the cycle decides", () => {
    expect(withFilter({ state: "inactive" })).toEqual(["w6"]);
    const odd = buildRows(school, {}, "odd");
    const kept = filterRows(odd, { ...NO_FILTERS, state: "inactive" }, "");
    expect(ids(kept)).toEqual([]);
  });

  it("combines the filters with each other and with the search", () => {
    expect(withFilter({ classId: "c5b", subjectId: "eng" })).toEqual([
      "w2",
      "w4",
    ]);
    expect(withFilter({ classId: "c5b", state: "unplaced" })).toEqual([
      "w2",
      "w4",
    ]);
    expect(withFilter({ classId: "c5b" }, "яков")).toEqual(["w2", "w4"]);
    expect(withFilter({ classId: "c5b" }, "рисов")).toEqual(["w6"]);
    expect(withFilter({ classId: "c5a" }, "рисов")).toEqual([]);
  });

  it("searches the group, subject and teacher names, ignoring case", () => {
    expect(withFilter({}, "АНГЛИЙСК")).toEqual(["w2", "w4"]);
    expect(withFilter({}, "белова")).toEqual(["w1", "w4", "w6"]);
    expect(withFilter({}, "10б")).toEqual(["w3", "w5"]);
    expect(withFilter({}, "нет такого")).toEqual([]);
  });
});

describe("totals", () => {
  it("counts the hours of the active cycle and the placed lessons of the shown rows", () => {
    expect(totals(rows)).toEqual({ count: 6, hours: 15, placed: 9 });
    const shown = filterRows(rows, { ...NO_FILTERS, classId: "c5b" }, "");
    expect(totals(shown)).toEqual({ count: 3, hours: 6, placed: 6 });
    expect(totals([])).toEqual({ count: 0, hours: 0, placed: 0 });
  });

  it("takes the hours of the cycle chosen, not the weekly ones", () => {
    const cycled: LoadSchool = {
      ...school,
      workloads: [load("x", { cycleHours: { odd: 1 }, hours: 3 })],
    };
    expect(totals(buildRows(cycled, {}, "odd")).hours).toBe(1);
    expect(totals(buildRows(cycled, {}, "weekly")).hours).toBe(3);
  });
});

describe("sorting", () => {
  const order = (sort: SortSpec) => ids(sortRows(rows, sort));

  it("defaults to the class order, then the subject, then the group", () => {
    // 5А: Английский (gen, first class 5А), Математика; 5Б: Английский, Рисование; 10Б: Математика, Рисование.
    expect(order(DEFAULT_SORT)).toEqual(["w4", "w1", "w2", "w6", "w5", "w3"]);
  });

  it("reverses with a second click", () => {
    const reversed = order({ key: "group", dir: "desc" });
    expect(reversed).toEqual([...order(DEFAULT_SORT)].reverse());
    expect(nextSort(DEFAULT_SORT, "group")).toEqual({
      key: "group",
      dir: "desc",
    });
    expect(nextSort({ key: "group", dir: "desc" }, "group").dir).toBe("asc");
    expect(nextSort(DEFAULT_SORT, "hours")).toEqual({
      key: "hours",
      dir: "asc",
    });
  });

  it("sorts hours and placed as numbers", () => {
    // Hours in this cycle: w6 is not taught (0), then 1, 2, 3, 4, 5.
    expect(order({ key: "hours", dir: "asc" })).toEqual([
      "w6",
      "w3",
      "w2",
      "w1",
      "w4",
      "w5",
    ]);
    expect(order({ key: "hours", dir: "desc" })[0]).toBe("w5");
    const placed = order({ key: "placed", dir: "desc" });
    expect(placed.slice(0, 3)).toEqual(["w4", "w1", "w2"]);
  });

  it("sorts names the Russian way and puts the empty ones first", () => {
    const subjects = order({ key: "subject", dir: "asc" });
    // Английский < Математика < Рисование; equal subjects keep the default order.
    expect(subjects).toEqual(["w4", "w2", "w1", "w5", "w6", "w3"]);
    const teachers = order({ key: "teacher", dir: "asc" });
    expect(teachers.slice(0, 2).sort()).toEqual(["w3", "w5"]);
    expect(teachers.at(-1)).toBe("w2");
  });

  it("sorts by the synchronous block, the rows without one first", () => {
    const blocks = order({ key: "block", dir: "desc" });
    expect(blocks[0]).toBe("w4");
  });
});

describe("paging", () => {
  it("cuts the rows into pages and keeps the page valid when rows disappear", () => {
    const many = Array.from({ length: PAGE_SIZE * 2 + 3 }, (_, i) => i);
    expect(pageCount(many.length)).toBe(3);
    expect(pageRows(many, 0)).toHaveLength(PAGE_SIZE);
    expect(pageRows(many, 2)).toHaveLength(3);
    expect(clampPage(2, 10)).toBe(0);
    expect(clampPage(7, many.length)).toBe(2);
    expect(clampPage(-1, many.length)).toBe(0);
    expect(pageCount(0)).toBe(1);
    expect(pageRows([], 4)).toEqual([]);
  });
});

describe("options", () => {
  it("lists the classes in school order and the teachers and subjects by name", () => {
    const options = filterOptions(school);
    expect(options.classes.map((c) => c.name)).toEqual([
      "5А",
      "5Б",
      "9А",
      "10Б",
    ]);
    expect(options.teachers.map((t) => t.name)).toEqual([
      "Анна Белова",
      "Яков Орлов",
    ]);
    expect(options.subjects.map((x) => x.name)).toEqual([
      "Английский язык",
      "Математика",
      "Рисование",
    ]);
  });
});

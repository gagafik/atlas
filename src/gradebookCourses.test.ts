import { describe, expect, it } from "vitest";
import type { CourseItem } from "./courseTable";
import {
  DEFAULT_SORT,
  NO_FILTERS,
  PAGE_SIZE,
  accessText,
  buildRows,
  clampPage,
  commonAccess,
  courseWord,
  filterRows,
  filtersActive,
  nextSort,
  offeredFilters,
  pageCount,
  pageRows,
  sortRows,
  summaryText,
  type SortKey,
} from "./gradebookCourses";

const course = (over: Partial<CourseItem> & { id: string }): CourseItem => ({
  groupId: `g${over.id}`,
  group: "5А",
  subjectId: "math",
  subject: "Математика",
  classes: ["5А"],
  teachers: ["Орлов Пётр"],
  kind: "course",
  canEdit: true,
  ...over,
});

const LIST: CourseItem[] = [
  course({
    id: "1",
    group: "10Б",
    classes: ["10Б"],
    subjectId: "phys",
    subject: "Физика",
    teachers: ["Седов Иван"],
    kind: "admin",
  }),
  course({ id: "2", kind: "class", canEdit: false }),
  course({
    id: "3",
    group: "9В",
    classes: ["9В"],
    subjectId: "art",
    subject: "Музыка",
    teachers: [],
  }),
  course({
    id: "4",
    group: "5А англ.",
    classes: ["5А", "5Б"],
    subjectId: "eng",
    subject: "Английский",
    teachers: ["Седов Иван", "Орлов Пётр"],
    kind: "admin",
    canEdit: false,
  }),
];
const rows = buildRows(LIST);
// The invented ids "1".."4" of the courses in the order of the rows.
const ids = (r: { course: CourseItem }[]) =>
  r.map((x) => x.course.groupId.slice(1));

describe("filters", () => {
  it("keeps everything without filters", () => {
    expect(filterRows(rows, NO_FILTERS, "")).toHaveLength(4);
  });
  it("filters by class, subject, teacher and access", () => {
    const by = (patch: Partial<typeof NO_FILTERS>) =>
      ids(filterRows(rows, { ...NO_FILTERS, ...patch }, ""));
    expect(by({ className: "5Б" })).toEqual(["4"]);
    expect(by({ subjectId: "phys" })).toEqual(["1"]);
    expect(by({ teacher: "Седов Иван" })).toEqual(["1", "4"]);
    expect(by({ access: "admin" })).toEqual(["1", "4"]);
    expect(by({ access: "course" })).toEqual(["3"]);
  });
  it("searches group, subject, classes and teachers regardless of case", () => {
    expect(ids(filterRows(rows, NO_FILTERS, "музык"))).toEqual(["3"]);
    expect(ids(filterRows(rows, NO_FILTERS, " ПЁТР "))).toEqual(["2", "4"]);
    expect(ids(filterRows(rows, NO_FILTERS, "5б"))).toEqual(["4"]);
    expect(filterRows(rows, NO_FILTERS, "нет такого")).toEqual([]);
  });
  it("combines all of them", () => {
    const f = {
      ...NO_FILTERS,
      teacher: "Орлов Пётр",
      access: "admin" as const,
    };
    expect(ids(filterRows(rows, f, "англ"))).toEqual(["4"]);
    expect(filterRows(rows, f, "физ")).toEqual([]);
  });
  it("narrows by class or subject together with the search and other filters", () => {
    const f = (patch: Partial<typeof NO_FILTERS>) => ({
      ...NO_FILTERS,
      ...patch,
    });
    expect(ids(filterRows(rows, f({ className: "5А" }), "англ"))).toEqual([
      "4",
    ]);
    expect(filterRows(rows, f({ className: "5А" }), "физ")).toEqual([]);
    expect(ids(filterRows(rows, f({ subjectId: "eng" }), "пётр"))).toEqual([
      "4",
    ]);
    expect(filterRows(rows, f({ subjectId: "eng" }), "музык")).toEqual([]);
    expect(
      ids(
        filterRows(
          rows,
          f({ subjectId: "eng", access: "admin", teacher: "Седов Иван" }),
          "",
        ),
      ),
    ).toEqual(["4"]);
    expect(
      filterRows(rows, f({ className: "5Б", access: "course" }), ""),
    ).toEqual([]);
  });
  it("knows when something is set", () => {
    expect(filtersActive(NO_FILTERS, "  ")).toBe(false);
    expect(filtersActive(NO_FILTERS, "а")).toBe(true);
    expect(filtersActive({ ...NO_FILTERS, access: "class" }, "")).toBe(true);
  });
});

describe("offered filters", () => {
  it("offers everything to an administrator list with several kinds", () => {
    const o = offeredFilters(LIST);
    expect(o).toMatchObject({
      showClass: true,
      showSubject: true,
      showTeacher: true,
      showAccess: true,
    });
    expect(o.classes).toEqual(["5А", "5Б", "9В", "10Б"]);
    expect(o.teachers).toEqual(["Орлов Пётр", "Седов Иван"]);
  });
  it("offers neither teacher nor access to a teacher with own courses only", () => {
    const own = [
      course({ id: "a" }),
      course({ id: "b", subjectId: "phys", subject: "Физика" }),
    ];
    expect(offeredFilters(own)).toMatchObject({
      showClass: false,
      showSubject: true,
      showTeacher: false,
      showAccess: false,
    });
  });
  it("does not offer the teacher filter when a colleague only co-teaches one own course", () => {
    const own = [
      course({ id: "a", teachers: ["Орлов Пётр"] }),
      course({
        id: "b",
        subjectId: "eng",
        subject: "Английский",
        teachers: ["Орлов Пётр", "Седов Иван"],
      }),
    ];
    expect(offeredFilters(own).showTeacher).toBe(false);
  });
  it("offers access to a teacher who is also a class teacher", () => {
    const mixed = [
      course({ id: "a" }),
      course({ id: "b", kind: "class", canEdit: false }),
    ];
    expect(offeredFilters(mixed).showAccess).toBe(true);
  });
  it("offers no access filter when every course is of one kind", () => {
    const admin = [
      course({ id: "a", kind: "admin" }),
      course({ id: "b", kind: "admin" }),
    ];
    expect(offeredFilters(admin).showAccess).toBe(false);
  });
});

describe("access text", () => {
  it("follows kind and edit right", () => {
    expect(accessText({ kind: "course", canEdit: true })).toBe("Мой курс");
    expect(accessText({ kind: "class", canEdit: false })).toBe(
      "Классный руководитель — только чтение",
    );
    expect(accessText({ kind: "admin", canEdit: true })).toBe("Администратор");
    expect(accessText({ kind: "admin", canEdit: false })).toBe(
      "Администратор — только чтение",
    );
  });
  it("is said once when it is the same everywhere", () => {
    const admin = [
      course({ id: "a", kind: "admin" }),
      course({ id: "b", kind: "admin" }),
    ];
    expect(commonAccess(admin)).toBe("Администратор");
    expect(commonAccess(LIST)).toBeNull();
    expect(commonAccess([])).toBeNull();
  });
  it("is not common when one kind differs in the edit right", () => {
    const list = [
      course({ id: "a", kind: "admin" }),
      course({ id: "b", kind: "admin", canEdit: false }),
    ];
    expect(commonAccess(list)).toBeNull();
  });
});

describe("order", () => {
  it("is by class numerically, then subject, then group", () => {
    const list = [
      course({ id: "1", group: "10Б", classes: ["10Б"] }),
      course({ id: "2", group: "9В", classes: ["9В"] }),
      course({ id: "3", subjectId: "p", subject: "Физика" }),
      course({ id: "4" }),
    ];
    expect(ids(sortRows(buildRows(list), DEFAULT_SORT))).toEqual([
      "4",
      "3",
      "2",
      "1",
    ]);
  });
  it("puts courses without a class last", () => {
    const list = [
      course({ id: "1", group: "Кружок", classes: [] }),
      course({ id: "2", group: "11А", classes: ["11А"] }),
    ];
    expect(ids(sortRows(buildRows(list), DEFAULT_SORT))).toEqual(["2", "1"]);
  });
  it("sorts by subject and reverses", () => {
    const by = (dir: "asc" | "desc") =>
      ids(sortRows(rows, { key: "subject", dir }));
    expect(by("asc")).toEqual(["4", "2", "3", "1"]);
    expect(by("desc")).toEqual(["1", "3", "2", "4"]);
  });
  it("sorts by teacher with courses without a teacher first, and reverses", () => {
    const by = (dir: "asc" | "desc") =>
      ids(sortRows(rows, { key: "teacher", dir }));
    expect(by("asc")).toEqual(["3", "2", "1", "4"]);
    expect(by("desc")).toEqual(["4", "1", "2", "3"]);
  });
  it("sorts by access text and reverses", () => {
    const by = (key: SortKey, dir: "asc" | "desc") =>
      ids(sortRows(rows, { key, dir }));
    // Администратор, Администратор — только чтение, Классный руководитель…, Мой курс.
    expect(by("access", "asc")).toEqual(["1", "4", "2", "3"]);
    expect(by("access", "desc")).toEqual(["3", "2", "4", "1"]);
  });
  it("sorts by group in the default order, reversed on demand", () => {
    expect(ids(sortRows(rows, { key: "group", dir: "desc" }))).toEqual(
      ids(sortRows(rows, DEFAULT_SORT)).reverse(),
    );
  });
  it("starts a new column ascending and reverses the same one", () => {
    expect(nextSort(DEFAULT_SORT, "subject")).toEqual({
      key: "subject",
      dir: "asc",
    });
    expect(nextSort({ key: "subject", dir: "asc" }, "subject")).toEqual({
      key: "subject",
      dir: "desc",
    });
    expect(nextSort({ key: "subject", dir: "desc" }, "subject")).toEqual({
      key: "subject",
      dir: "asc",
    });
  });
});

describe("paging", () => {
  const many = Array.from({ length: PAGE_SIZE * 2 + 3 }, (_, i) => i);
  it("cuts pages and counts them", () => {
    expect(pageCount(0)).toBe(1);
    expect(pageCount(PAGE_SIZE)).toBe(1);
    expect(pageCount(many.length)).toBe(3);
    expect(pageRows(many, 0)).toHaveLength(PAGE_SIZE);
    expect(pageRows(many, 2)).toEqual([40, 41, 42]);
  });
  it("clamps a page that no longer exists", () => {
    expect(clampPage(9, many.length)).toBe(2);
    expect(clampPage(-1, many.length)).toBe(0);
    expect(pageRows(many, 9)).toEqual([40, 41, 42]);
  });
});

describe("word forms and summary", () => {
  it("follow the number", () => {
    const form = (n: number) => `${n} ${courseWord(n)}`;
    expect(form(1)).toBe("1 курс");
    expect(form(2)).toBe("2 курса");
    expect(form(5)).toBe("5 курсов");
    expect(form(11)).toBe("11 курсов");
    expect(form(14)).toBe("14 курсов");
    expect(form(21)).toBe("21 курс");
    expect(form(112)).toBe("112 курсов");
  });
  it("puts the total after «из» in the genitive", () => {
    expect(summaryText(5, 21, true, null)).toBe("Показано 5 из 21 курса");
    expect(summaryText(3, 22, true, null)).toBe("Показано 3 из 22 курсов");
    expect(summaryText(3, 11, true, null)).toBe("Показано 3 из 11 курсов");
  });
  it("says the total alone without filters and adds the common access", () => {
    expect(summaryText(240, 240, false, null)).toBe("240 курсов");
    expect(summaryText(12, 240, true, null)).toBe("Показано 12 из 240 курсов");
    expect(summaryText(2, 2, false, "Администратор")).toContain(
      "Администратор",
    );
  });
});

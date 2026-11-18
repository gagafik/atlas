import { activeIn, cycleHours } from "./cycles";
import type { State, Workload } from "./types";
import { gradeOf, named, teacherIds, teacherNames } from "./utils";

// The rules of the «Учебная нагрузка» list: which rows a filter keeps, what the totals of the shown rows are, how the
// rows are ordered and cut into pages. The screen only draws what these functions return.

export const PAGE_SIZE = 25;
/** The value of the teacher filter that keeps the assignments without any teacher. */
export const NO_TEACHER = "__none__";

export type LoadSchool = Pick<
  State,
  "classes" | "groups" | "subjects" | "teachers" | "workloads"
>;
export type LoadStateFilter = "all" | "unplaced" | "noTeacher" | "inactive";
export type LoadFilters = {
  classId: string;
  teacherId: string;
  subjectId: string;
  state: LoadStateFilter;
};
export type SortKey =
  "group" | "subject" | "teacher" | "hours" | "placed" | "block";
export type SortSpec = { key: SortKey; dir: "asc" | "desc" };

export const NO_FILTERS: LoadFilters = {
  classId: "",
  teacherId: "",
  subjectId: "",
  state: "all",
};
export const DEFAULT_SORT: SortSpec = { key: "group", dir: "asc" };

export type LoadRow = {
  id: string;
  workload: Workload;
  groupName: string;
  /** Names of the classes of the group, in school order. */
  classNames: string[];
  classIds: string[];
  /** The group is a whole class: its name already says which one. */
  wholeClass: boolean;
  subjectId: string;
  subjectName: string;
  teacherIds: string[];
  teacherText: string;
  /** A teacher is required and none is set. */
  teacherMissing: boolean;
  /** The assignment is taught in the active cycle. */
  active: boolean;
  /** Hours required in the active cycle (0 when it is not taught). */
  hours: number;
  placed: number;
  block: string;
  /** Position of the first class of the group in school order. */
  classRank: number;
};

const collator = new Intl.Collator("ru", {
  numeric: true,
  sensitivity: "base",
});
const byText = (a: string, b: string) => collator.compare(a, b);

/** Classes in school order: grade, then name. */
export function sortedClasses(classes: LoadSchool["classes"]) {
  return [...classes].sort(
    (a, b) => gradeOf(a.name) - gradeOf(b.name) || byText(a.name, b.name),
  );
}

export function buildRows(
  s: LoadSchool,
  counts: Record<string, number>,
  cycleId: string,
): LoadRow[] {
  const rank = new Map(sortedClasses(s.classes).map((c, i) => [c.id, i]));
  const groups = new Map(s.groups.map((g) => [g.id, g]));
  const subjects = new Map(s.subjects.map((x) => [x.id, x]));
  return s.workloads.map((w) => {
    const group = groups.get(w.groupId);
    const ids = group?.classIds ?? [];
    const ordered = [...ids].sort(
      (a, b) => (rank.get(a) ?? Infinity) - (rank.get(b) ?? Infinity),
    );
    const active = activeIn(w, cycleId);
    const hasTeacher = teacherIds(w).length > 0;
    return {
      id: w.id,
      workload: w,
      groupName: group?.name ?? "Не указан",
      classNames: ordered.map((id) => named(s.classes, id)),
      classIds: ids,
      wholeClass: group?.kind === "whole",
      subjectId: w.subjectId,
      subjectName: subjects.get(w.subjectId)?.name ?? "Не указан",
      teacherIds: teacherIds(w),
      teacherText: teacherNames(s as State, w),
      teacherMissing: w.teacherRequired !== false && !hasTeacher,
      active,
      hours: active ? cycleHours(w, cycleId) : 0,
      placed: counts[w.id] || 0,
      block: w.block || "",
      classRank: ordered.length ? (rank.get(ordered[0]) ?? Infinity) : Infinity,
    };
  });
}

export function filtersActive(f: LoadFilters) {
  return !!(f.classId || f.teacherId || f.subjectId || f.state !== "all");
}

/** The search of the page: group, subject and teacher names and the synchronous block. */
function matchesSearch(r: LoadRow, search: string) {
  if (!search) return true;
  const q = search.toLowerCase();
  return (
    r.groupName.toLowerCase().includes(q) ||
    r.subjectName.toLowerCase().includes(q) ||
    r.teacherText.toLowerCase().includes(q) ||
    r.block.toLowerCase().includes(q) ||
    `${r.groupName} · ${r.subjectName}`.toLowerCase().includes(q)
  );
}

export function filterRows(
  rows: LoadRow[],
  f: LoadFilters,
  search: string,
): LoadRow[] {
  return rows.filter((r) => {
    if (f.classId && !r.classIds.includes(f.classId)) return false;
    if (f.teacherId === NO_TEACHER) {
      if (r.teacherIds.length) return false;
    } else if (f.teacherId && !r.teacherIds.includes(f.teacherId)) return false;
    if (f.subjectId && r.subjectId !== f.subjectId) return false;
    if (f.state === "unplaced" && r.placed === r.hours) return false;
    if (f.state === "noTeacher" && !r.teacherMissing) return false;
    if (f.state === "inactive" && r.active) return false;
    return matchesSearch(r, search);
  });
}

export function totals(rows: LoadRow[]) {
  return {
    count: rows.length,
    hours: rows.reduce((n, r) => n + r.hours, 0),
    // Only lessons that fill a required hour count: extra lessons of one row must not cover missing ones of another.
    placed: rows.reduce((n, r) => n + Math.min(r.placed, r.hours), 0),
  };
}

const byDefault = (a: LoadRow, b: LoadRow) =>
  a.classRank - b.classRank ||
  byText(a.subjectName, b.subjectName) ||
  byText(a.groupName, b.groupName);

const teacherKey = (r: LoadRow) => (r.teacherIds.length ? r.teacherText : "");

const primary: Record<SortKey, (a: LoadRow, b: LoadRow) => number> = {
  group: byDefault,
  subject: (a, b) => byText(a.subjectName, b.subjectName),
  teacher: (a, b) => byText(teacherKey(a), teacherKey(b)),
  hours: (a, b) => a.hours - b.hours,
  placed: (a, b) => a.placed - b.placed,
  block: (a, b) => byText(a.block, b.block),
};

/** The «group» column is the default order itself; the other columns fall back to it for equal values. */
export function sortRows(rows: LoadRow[], sort: SortSpec): LoadRow[] {
  const sign = sort.dir === "desc" ? -1 : 1;
  return [...rows].sort(
    (a, b) => sign * primary[sort.key](a, b) || byDefault(a, b),
  );
}

/** A click on a heading: another column starts ascending, the same column reverses. */
export function nextSort(current: SortSpec, key: SortKey): SortSpec {
  if (current.key !== key) return { key, dir: "asc" };
  return { key, dir: current.dir === "asc" ? "desc" : "asc" };
}

export function pageCount(total: number) {
  return Math.max(1, Math.ceil(total / PAGE_SIZE));
}
export function clampPage(page: number, total: number) {
  return Math.min(Math.max(0, page), pageCount(total) - 1);
}
export function pageRows<T>(rows: T[], page: number): T[] {
  const p = clampPage(page, rows.length);
  return rows.slice(p * PAGE_SIZE, (p + 1) * PAGE_SIZE);
}

/** What the selects offer: classes in school order, teachers and subjects by name. */
export function filterOptions(s: LoadSchool) {
  const byName = <T extends { name: string }>(items: T[]) =>
    [...items].sort((a, b) => byText(a.name, b.name));
  return {
    classes: sortedClasses(s.classes),
    teachers: [{ id: NO_TEACHER, name: "Без учителя" }, ...byName(s.teachers)],
    subjects: byName(s.subjects),
  };
}

import type { CourseItem, CourseKind } from "./courseTable";
import { PAGE_SIZE, wordForm } from "./teachingLoad";

// The rules of the course picker of «Оценки»: which rows a filter keeps, which filters are offered, the access text,
// the order and the pages. The screen only draws what these functions return.

export { PAGE_SIZE, wordForm };

export type AccessFilter = "all" | CourseKind;
export type CourseFilters = {
  className: string;
  subjectId: string;
  teacher: string;
  access: AccessFilter;
};
export const NO_FILTERS: CourseFilters = {
  className: "",
  subjectId: "",
  teacher: "",
  access: "all",
};
export type SortKey = "group" | "subject" | "teacher" | "access";
export type SortSpec = { key: SortKey; dir: "asc" | "desc" };
export const DEFAULT_SORT: SortSpec = { key: "group", dir: "asc" };

export type CourseRow = {
  id: string;
  course: CourseItem;
  /** Classes in school order. */
  classes: string[];
  teacherText: string;
  accessText: string;
  /** The classes are not just the name of the group: they follow it in the list. */
  showClasses: boolean;
};

const collator = new Intl.Collator("ru", { numeric: true });
const byText = (a: string, b: string) => collator.compare(a, b);

export const courseKey = (c: Pick<CourseItem, "groupId" | "subjectId">) =>
  `${c.groupId}|${c.subjectId}`;

/** The access of the user to a course in words. */
export function accessText(c: Pick<CourseItem, "kind" | "canEdit">) {
  if (c.kind === "admin")
    return c.canEdit ? "Администратор" : "Администратор — только чтение";
  return c.kind === "class"
    ? "Классный руководитель — только чтение"
    : "Мой курс";
}

export const ACCESS_FILTERS: { value: AccessFilter; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "course", label: "Мои курсы" },
  { value: "class", label: "Классный руководитель" },
  { value: "admin", label: "Администратор" },
];

export function buildRows(courses: CourseItem[]): CourseRow[] {
  return courses.map((c) => {
    const classes = [...c.classes].sort(byText);
    return {
      id: courseKey(c),
      course: c,
      classes,
      teacherText: c.teachers.join(", "),
      accessText: accessText(c),
      showClasses: !!classes.length && classes.join(", ") !== c.group,
    };
  });
}

export function filtersActive(f: CourseFilters, search: string) {
  return !!(
    search.trim() ||
    f.className ||
    f.subjectId ||
    f.teacher ||
    f.access !== "all"
  );
}

export function filterRows(
  rows: CourseRow[],
  f: CourseFilters,
  search: string,
): CourseRow[] {
  const q = search.trim().toLocaleLowerCase();
  return rows.filter(({ course: c, classes }) => {
    if (f.className && !c.classes.includes(f.className)) return false;
    if (f.subjectId && c.subjectId !== f.subjectId) return false;
    if (f.teacher && !c.teachers.includes(f.teacher)) return false;
    if (f.access !== "all" && c.kind !== f.access) return false;
    return (
      !q ||
      `${c.group} ${c.subject} ${classes.join(" ")} ${c.teachers.join(" ")}`
        .toLocaleLowerCase()
        .includes(q)
    );
  });
}

/** Which filters the list gives reason to offer, and what they offer. */
export function offeredFilters(courses: CourseItem[]) {
  const classes = [...new Set(courses.flatMap((c) => c.classes))].sort(byText);
  const subjects = new Map<string, string>();
  for (const c of courses) subjects.set(c.subjectId, c.subject);
  const teachers = [...new Set(courses.flatMap((c) => c.teachers))].sort(
    byText,
  );
  const kinds = new Set(courses.map((c) => c.kind));
  return {
    classes,
    subjects: [...subjects]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => byText(a.name, b.name)),
    teachers,
    showClass: classes.length > 1,
    showSubject: subjects.size > 1,
    // A teacher shared by every course (an ordinary teacher with own courses, some co-taught) splits nothing.
    showTeacher:
      teachers.length > 1 &&
      !teachers.some((t) => courses.every((c) => c.teachers.includes(t))),
    showAccess: kinds.size > 1,
  };
}

/** The one access text of the whole list, or null when the texts differ (then the column is shown). */
export function commonAccess(courses: CourseItem[]): string | null {
  if (!courses.length) return null;
  const texts = new Set(courses.map(accessText));
  return texts.size === 1 ? [...texts][0] : null;
}

const classKey = (r: CourseRow) => r.classes[0] ?? "";
const byDefault = (a: CourseRow, b: CourseRow) =>
  (a.classes.length ? 0 : 1) - (b.classes.length ? 0 : 1) ||
  byText(classKey(a), classKey(b)) ||
  byText(a.course.subject, b.course.subject) ||
  byText(a.course.group, b.course.group);

const primary: Record<SortKey, (a: CourseRow, b: CourseRow) => number> = {
  group: byDefault,
  subject: (a, b) => byText(a.course.subject, b.course.subject),
  teacher: (a, b) => byText(a.teacherText, b.teacherText),
  access: (a, b) => byText(a.accessText, b.accessText),
};

/** The «group» column is the default order itself; the others fall back to it for equal values. */
export function sortRows(rows: CourseRow[], sort: SortSpec): CourseRow[] {
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

export const courseWord = (n: number) => wordForm(n, "курс", "курса", "курсов");

/** «Показано 12 из 240 курсов», or «240 курсов» when nothing is filtered; the common access follows when there is one. */
export function summaryText(
  shown: number,
  total: number,
  filtered: boolean,
  access: string | null,
) {
  const base = filtered
    ? `Показано ${shown} из ${total} ${wordForm(total, "курса", "курсов", "курсов")}`
    : `${total} ${courseWord(total)}`;
  return access ? `${base} · ${access}` : base;
}

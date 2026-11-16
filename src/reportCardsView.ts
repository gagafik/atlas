// «Табели»: what the screens of the report-card part show, worked out from the API data. Pure functions, no React.
import { finalLabel, names } from "./finalGrades";
import type {
  Book,
  ClassReport,
  Matrix,
  Named,
  Period,
  Published,
  Row,
} from "./finalGradesTypes";

const natural = (a: string, b: string) =>
  a.localeCompare(b, "ru", { numeric: true });

// ---- Periods and statements -----------------------------------------------

/** The period the screen opens at: the newest with a prepared statement, else the newest closed one. `periods` come newest first. */
export function defaultPeriod(periods: Period[], books: Book[]): string {
  const prepared = new Set(books.map((b) => b.period.id));
  return (
    periods.find((p) => prepared.has(p.id))?.id ||
    periods.find((p) => p.status === "closed")?.id ||
    periods[0]?.id ||
    ""
  );
}

/** The statement of a period: the newest one, unless an older one of the same period was picked. `books` come newest first. */
export function bookFor(
  periodId: string,
  books: Book[],
  overrideId?: string,
): Book | undefined {
  const own = books.filter((b) => b.period.id === periodId);
  return own.find((b) => b.id === overrideId) || own[0];
}

/** Periods a teacher can choose: those of the own statements and the given ones, once each, newest start first. */
export function teacherPeriods(books: Book[], periods: Period[]): Period[] {
  const found = new Map<string, Period>();
  for (const p of [...books.map((b) => b.period), ...periods])
    if (!found.has(p.id)) found.set(p.id, p);
  return [...found.values()].sort((a, b) =>
    a.start === b.start ? 0 : a.start < b.start ? 1 : -1,
  );
}

/** The id of a statement a finished job produced that the list of books does not know yet, or ''. */
export function finishedBook(
  jobs: { periodId: string; status: string; bookId: string }[],
  periodId: string,
  books: { id: string }[],
): string {
  const known = new Set(books.map((b) => b.id));
  return (
    jobs.find(
      (j) =>
        j.periodId === periodId &&
        j.status === "succeeded" &&
        !!j.bookId &&
        !known.has(j.bookId),
    )?.bookId || ""
  );
}

/**
 * The periods a family can choose for one child, newest first: those the child has a published card in.
 * An older server does not say which child has which, so then every period is offered.
 */
export function childPeriods(
  periods: Period[],
  studentPeriods: Record<string, string[]> | undefined,
  pupilId: string,
): Period[] {
  const own = studentPeriods?.[pupilId];
  return own ? periods.filter((p) => own.includes(p.id)) : periods;
}

/** The period a family sees: the one it picked for this child if the child still has it, else the newest of the child. */
export function familyPeriod(
  available: Period[],
  picked: { pupilId: string; periodId: string } | null,
  pupilId: string,
): string {
  if (
    picked &&
    picked.pupilId === pupilId &&
    available.some((p) => p.id === picked.periodId)
  )
    return picked.periodId;
  return available[0]?.id ?? "";
}

/** Where the card of a pupil in the print dialog comes from: the class report, the pupil's own card when he is no longer in the class, or nowhere yet. */
export function pupilCardSource(
  report: ClassReport | undefined,
  pupilId: string,
): "class" | "personal" | "none" {
  if (!report || !pupilId) return "none";
  return report.pupils.some((p) => p.id === pupilId) ? "class" : "personal";
}

/** Whether the settings tab is read-only: an archived year cannot be changed, but its statements can still be looked through. */
export function settingsLocked(year: string | undefined, tab: string): boolean {
  return !!year && tab !== "preparation";
}

// ---- Where a class or a course stands -------------------------------------

export type Counts = {
  total: number;
  draft: number;
  submitted: number;
  approved: number;
  published: number;
  proposed: number;
  unassigned: number;
  unavailable: number;
};
export type StateKind =
  "empty" | "unassigned" | "drafting" | "waiting" | "ready" | "published";
export type ClassState = {
  kind: StateKind;
  count: number;
  done: number;
  total: number;
};

/** The one thing a class or course waits for, in the order the people involved have to act. */
export function classState(c: Counts): ClassState {
  // A saved draft is already a result set by the teacher, so it counts as well; a submitted, approved or published row always has one.
  const done = Math.max(c.proposed, c.submitted + c.approved + c.published);
  const state = (kind: StateKind, count: number): ClassState => ({
    kind,
    count,
    done,
    total: c.total,
  });
  if (c.total <= 0) return state("empty", 0);
  if (c.unassigned + c.unavailable > 0)
    return state("unassigned", c.unassigned + c.unavailable);
  if (c.draft > 0) return state("drafting", c.draft);
  if (c.submitted > 0) return state("waiting", c.submitted);
  if (c.approved > 0) return state("ready", c.approved);
  return state("published", c.published);
}

export function stateText(s: ClassState): string {
  switch (s.kind) {
    case "empty":
      return "Нет строк";
    case "unassigned":
      return `Без ответственного: ${s.count}`;
    case "drafting":
      return `Учителя выставляют: ${s.done} из ${s.total}`;
    case "waiting":
      return `Ждёт утверждения: ${s.count}`;
    case "ready":
      return "Готово к публикации";
    case "published":
      return "Опубликовано";
  }
}

const emptyCounts = (): Counts => ({
  total: 0,
  draft: 0,
  submitted: 0,
  approved: 0,
  published: 0,
  proposed: 0,
  unassigned: 0,
  unavailable: 0,
});

export type ClassRow = Named & Counts & { pupils: number; state: ClassState };
/** One row per class of the statement, in the order of the matrix, with the counters of all its subjects added up. */
export function classRows(matrix: Matrix): ClassRow[] {
  return matrix.classes.map((c) => {
    const sum = emptyCounts();
    for (const cell of matrix.cells)
      if (cell.classId === c.id)
        for (const key of Object.keys(sum) as (keyof Counts)[])
          sum[key] += cell[key];
    return { ...c, ...sum, pupils: c.pupils ?? 0, state: classState(sum) };
  });
}

export type CourseRow = Counts & {
  classId: string;
  subjectId: string;
  className: string;
  subjectName: string;
  state: ClassState;
};
/** One row per class and subject the viewer has rows in, by class and then by subject. */
export function courseRows(matrix: Matrix): CourseRow[] {
  const name = (list: Named[], id: string) =>
    list.find((x) => x.id === id)?.name || id;
  return matrix.cells
    .map((c) => ({
      ...c,
      className: name(matrix.classes, c.classId),
      subjectName: name(matrix.subjects, c.subjectId),
      state: classState(c),
    }))
    .sort(
      (a, b) =>
        natural(a.className, b.className) ||
        natural(a.subjectName, b.subjectName) ||
        natural(a.subjectId, b.subjectId),
    );
}

// ---- The class statement as a grid ----------------------------------------

export type GridLayout = { order: string[]; hidden: string[] };
export type GridSubject = Named & { hidden: boolean };
export type Grid = {
  pupils: Named[];
  subjects: GridSubject[];
  cell: (pupilId: string, subjectId: string) => Row | undefined;
};

/** Pupils in rows, subjects of the report card in columns: the saved order first, the rest by name, hidden ones last. */
export function classGrid(rows: Row[], layout: GridLayout): Grid {
  const pupils = new Map<string, Named>();
  const subjects = new Map<string, Named>();
  const cells = new Map<string, Row>();
  for (const r of rows) {
    if (!pupils.has(r.studentId))
      pupils.set(r.studentId, { id: r.studentId, name: r.source.student.name });
    if (!subjects.has(r.subjectId))
      subjects.set(r.subjectId, { id: r.subjectId, name: r.source.subject });
    const key = JSON.stringify([r.studentId, r.subjectId]);
    if (!cells.has(key)) cells.set(key, r);
  }
  const rank = new Map(layout.order.map((id, i) => [id, i]));
  const hidden = new Set(layout.hidden);
  const columns = [...subjects.values()]
    .map((s) => ({ ...s, hidden: hidden.has(s.id) }))
    .sort(
      (a, b) =>
        Number(a.hidden) - Number(b.hidden) ||
        (rank.get(a.id) ?? 1e6) - (rank.get(b.id) ?? 1e6) ||
        natural(a.name, b.name) ||
        natural(a.id, b.id),
    );
  return {
    pupils: [...pupils.values()].sort(
      (a, b) => natural(a.name, b.name) || natural(a.id, b.id),
    ),
    subjects: columns,
    cell: (pupilId, subjectId) =>
      cells.get(JSON.stringify([pupilId, subjectId])),
  };
}

/** What a grid cell shows: the grade of the proposal, else the published one, else a dash. */
export function cellLabel(row: Row): string {
  const result = row.proposal || row.published;
  return (result && finalLabel(result)) || "—";
}

/** A percentage of a calculation for reading: two decimals at most, a comma, «нет» when there is none. */
export function percentShown(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "нет";
  const n = Number(value);
  return Number.isFinite(n)
    ? `${n.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} %`
    : value;
}

/** A day of the period as the school writes it: 05.10.2026. */
export function dayText(day: string): string {
  const [y, m, d] = day.slice(0, 10).split("-");
  return d && m && y ? `${d}.${m}.${y}` : day;
}
/** The dates of a period in one piece. */
export const periodDates = (p: { start: string; end: string }) =>
  `${dayText(p.start)}–${dayText(p.end)}`;

const MARKS: Record<string, string> = {
  draft: "чер.",
  submitted: "ждёт",
  approved: "утв.",
  published: "опубл.",
};
/** A status as a short text for a narrow cell and the full word for a tooltip, so it never depends on colour. */
export function statusMark(status: string): { short: string; full: string } {
  return { short: MARKS[status] || status, full: names[status] || status };
}

// ---- Average of the grid --------------------------------------------------

const cents = (value: string | number) => Math.round(Number(value) * 100);

function bandCents(row: Row): number | null {
  const p = row.proposal;
  if (!p || p.outcome !== "graded") return null;
  const points = p.scale.bands.find((b) => b.label === p.label)?.points;
  return points === null || points === undefined || points === ""
    ? null
    : cents(points);
}

/** Whether the average means anything for these rows: some scale in use gives points to its grades. */
export function hasPoints(rows: Row[]): boolean {
  return rows.some((r) =>
    r.proposal?.scale.bands.some(
      (b) => b.points !== null && b.points !== undefined && b.points !== "",
    ),
  );
}

/** Mean of the points of the graded proposals as a Russian decimal, or null when nothing counts. Hidden subjects are left out. */
export function gridGpa(rows: Row[], hiddenIds: string[]): string | null {
  const hidden = new Set(hiddenIds);
  let sum = 0;
  let count = 0;
  for (const r of rows) {
    if (hidden.has(r.subjectId)) continue;
    const c = bandCents(r);
    if (c === null) continue;
    sum += c;
    count += 1;
  }
  if (!count) return null;
  // Half-up on the exact ratio, in whole cents.
  const units = Math.floor((2 * sum + count) / (2 * count));
  return `${Math.floor(units / 100)},${String(units % 100).padStart(2, "0")}`;
}

// ---- Search of a pupil ----------------------------------------------------

const fold = (text: string) => text.toLowerCase().replaceAll("ё", "е").trim();

/** Pupils whose name contains the query, in the school's order; nothing for an empty query. */
export function searchPupils(students: Named[], query: string, limit = 8) {
  const q = fold(query);
  if (!q) return [];
  return students
    .filter((s) => fold(s.name).includes(q))
    .sort((a, b) => natural(a.name, b.name) || natural(a.id, b.id))
    .slice(0, limit);
}

// ---- «Скачать табель CSV» -------------------------------------------------

/** The header and the rows of the personal report card as a CSV table. */
export function cardCsvRows(items: Published[]): (string | number)[][] {
  return [
    [
      "Ученик",
      "Класс",
      "Период",
      "Предмет",
      "Итог",
      "Комментарий",
      "Шкала",
      "Редакция шкалы",
      "Закрытие",
      "Источник актуален",
      "Опубликовано",
    ],
    ...items.map((r) => [
      r.student.name,
      r.student.className,
      r.period,
      r.subject,
      finalLabel(r),
      r.comment,
      r.scale.name,
      r.scale.version,
      r.closureVersion,
      r.current ? "Да" : "Нет",
      r.publishedAt,
    ]),
  ];
}

// ---- Actions on one row ---------------------------------------------------

export const ROW_ACTIONS = [
  "approve",
  "return",
  "publish",
  "reopen",
  "withdraw",
] as const;

/** Whether the server accepts the action on a row in this status; a statement of an older closure only allows withdrawing. */
export function rowActionAllowed(
  action: string,
  status: string,
  current: boolean,
): boolean {
  if (action === "withdraw") return true;
  if (!current) return false;
  switch (action) {
    case "assign":
      return status === "draft";
    case "approve":
      return status === "submitted";
    case "return":
      return status === "submitted" || status === "approved";
    case "publish":
      return status === "approved";
    case "reopen":
      return status === "published";
    default:
      return false;
  }
}

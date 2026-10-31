/** Pure helpers of the class attendance grid: periods, selection, the payload of an edit and the short marks of a cell. */
import { attendanceNames } from "./journal";

export type Counts = {
  present: number;
  absent: number;
  late: number;
  excused: number;
  left: number;
  unmarked: number;
  noRecord: number;
};
// Shares of the marked lessons; null while nothing is marked, never zero.
export type Rates = {
  marked: number;
  attendedPercent: number | null;
  absentPercent: number | null;
  excusedPercent: number | null;
};
export type GridLesson = {
  lessonKey: string;
  start: string;
  end: string;
  subject: string;
  subjectId: string;
  group: string;
  teachers: string;
  status: string;
  state: "published" | "draft" | "none";
  hasDraftChanges: boolean;
  frozen: boolean;
  archived: boolean;
  canEdit: boolean;
  version: number;
  publishedVersion: number | null;
};
export type Confirmation = {
  status: "none" | "confirmed" | "changed" | "revoked";
  version: number;
  updated: string | null;
  actor: string | null;
  expected: boolean;
  canConfirm: boolean;
};
export type GridDay = {
  date: string;
  lessons: GridLesson[];
  confirmation: Confirmation;
  locked: string[];
  counts: Counts;
  rates: Rates;
  openLessons: number;
};
// [raw mark, mark after an accepted note, minutes late, type of the note]; null raw: no record yet.
export type Cell = [string | null, string | null, number, string | null];
export type GridPupil = {
  id: string;
  name: string;
  active: boolean;
  counts: Counts;
  rates: Rates;
};
export type Grid = {
  classId: string;
  className: string;
  level: "edit" | "read" | "own";
  start: string;
  end: string;
  today: string;
  canEdit: boolean;
  codes: { id: string; name: string }[];
  days: GridDay[];
  pupils: GridPupil[];
  cells: Record<string, Record<string, Cell>>;
  totals: { counts: Counts; rates: Rates };
};
export type Picked = { lessonKey: string; studentId: string };
export type Candidate = Picked & { date: string; editable: boolean };

export const cellId = (c: Picked) => `${c.lessonKey}:${c.studentId}`;

/** Lessons of a day side by side in one slot when they start together (parallel groups). */
export function slots(
  day: GridDay,
): { start: string; lessons: GridLesson[] }[] {
  const found = new Map<string, GridLesson[]>();
  for (const lesson of day.lessons)
    found.set(lesson.start, [...(found.get(lesson.start) ?? []), lesson]);
  return [...found.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([start, lessons]) => ({ start, lessons }));
}

const shortDays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

/** «Пн 17.08»: the weekday and the date of a column. */
export function dayTitle(date: string) {
  const [, month, day] = date.split("-");
  return `${shortDays[(parse(date).getUTCDay() + 6) % 7]} ${day}.${month}`;
}

export type View = "day" | "week" | "month";
export type Period = { view: View; anchor: string };

const MS = 86400000;
const parse = (iso: string) => new Date(iso + "T00:00:00Z");
const text = (date: Date) => date.toISOString().slice(0, 10);

/** Monday to Sunday of the week that holds the date. */
export function weekOf(date: string): { start: string; end: string } {
  const day = parse(date);
  const offset = (day.getUTCDay() + 6) % 7;
  return {
    start: text(new Date(day.getTime() - offset * MS)),
    end: text(new Date(day.getTime() + (6 - offset) * MS)),
  };
}

/** The first and the last day of the month that holds the date. */
export function monthOf(date: string): { start: string; end: string } {
  const day = parse(date);
  const first = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), 1));
  const last = new Date(
    Date.UTC(day.getUTCFullYear(), day.getUTCMonth() + 1, 0),
  );
  return { start: text(first), end: text(last) };
}

export function rangeOf(period: Period): { start: string; end: string } {
  if (period.view === "week") return weekOf(period.anchor);
  if (period.view === "month") return monthOf(period.anchor);
  return { start: period.anchor, end: period.anchor };
}

/** The next or the previous day, week or month; a month always starts on its first day, so a month end never skips one. */
export function shift(period: Period, direction: 1 | -1): Period {
  const day = parse(period.anchor);
  if (period.view === "month")
    return {
      view: "month",
      anchor: text(
        new Date(
          Date.UTC(day.getUTCFullYear(), day.getUTCMonth() + direction, 1),
        ),
      ),
    };
  const step = period.view === "week" ? 7 : 1;
  return {
    view: period.view,
    anchor: text(new Date(day.getTime() + direction * step * MS)),
  };
}

/** The school year bounds the grid: the server refuses dates outside it. */
export function clampRange(
  range: { start: string; end: string },
  yearStart: string,
  yearEnd: string,
) {
  const start = range.start < yearStart ? yearStart : range.start;
  const end = range.end > yearEnd ? yearEnd : range.end;
  return { start, end: end < start ? start : end };
}

/** Every cell from one to another in display order, whichever comes first; an unknown end selects only the second. */
export function selectRange(order: string[], from: string, to: string) {
  const a = order.indexOf(from),
    b = order.indexOf(to);
  if (a < 0 || b < 0) return b < 0 ? [] : [order[b]];
  return order.slice(Math.min(a, b), Math.max(a, b) + 1);
}

/** The editable cells of a whole pupil, lesson or day; cells the class teacher may not change are never selected. */
export function selectLine(
  cells: Candidate[],
  line: { studentId?: string; lessonKey?: string; date?: string },
): Candidate[] {
  return cells.filter(
    (c) =>
      c.editable &&
      (line.studentId === undefined || c.studentId === line.studentId) &&
      (line.lessonKey === undefined || c.lessonKey === line.lessonKey) &&
      (line.date === undefined || c.date === line.date),
  );
}

/** The lessons of the loaded grid that are open for editing. */
export function openLessons(grid: Grid) {
  return new Set(
    grid.days.flatMap((d) =>
      d.lessons.filter((l) => l.canEdit).map((l) => l.lessonKey),
    ),
  );
}

/** Whether a cell of the loaded grid may be changed: its lesson is open for editing and the pupil is in its roster. */
export function editable(
  grid: Grid,
  lessonKey: string,
  studentId: string,
  open = openLessons(grid),
) {
  return (
    grid.canEdit && open.has(lessonKey) && !!grid.cells[lessonKey]?.[studentId]
  );
}

/** Every cell of the grid in reading order: lessons by time within a day, pupils by name. */
export function candidates(grid: Grid): Candidate[] {
  const open = openLessons(grid);
  return grid.days.flatMap((day) =>
    slots(day)
      .flatMap((slot) => slot.lessons)
      .flatMap((lesson) =>
        grid.pupils
          .filter((p) => grid.cells[lesson.lessonKey]?.[p.id])
          .map((p) => ({
            lessonKey: lesson.lessonKey,
            studentId: p.id,
            date: day.date,
            editable: editable(grid, lesson.lessonKey, p.id, open),
          })),
      ),
  );
}

/** The body of an edit: each cell carries the mark the client saw, so the server refuses a cell that changed meanwhile. */
export function marksPayload(
  selection: Picked[],
  grid: Grid,
  attendance: string,
  lateMinutes: number,
  reason: string,
  requestId: string,
  classId: string,
) {
  const dates = new Map(
    grid.days.flatMap((d) => d.lessons.map((l) => [l.lessonKey, d.date])),
  );
  const open = openLessons(grid);
  const cells = selection
    .filter((c) => editable(grid, c.lessonKey, c.studentId, open))
    .map((c) => {
      const [raw, , late] = grid.cells[c.lessonKey][c.studentId];
      return {
        lessonKey: c.lessonKey,
        day: dates.get(c.lessonKey)!,
        studentId: c.studentId,
        expected: raw,
        expectedLate: late,
      };
    });
  return {
    requestId,
    classId,
    cells,
    attendance,
    lateMinutes: attendance === "late" ? lateMinutes : 0,
    reason,
  };
}

/** «83,3 %»; the placeholder while nothing is marked. */
export function rateText(value: number | null | undefined) {
  return value === null || value === undefined
    ? "—"
    : `${String(value).replace(".", ",")} %`;
}

const letters: Record<string, string> = {
  present: "+",
  absent: "Н",
  late: "О",
  excused: "У",
  left: "Р",
  unmarked: "?",
};

/** A short letter and a class for a cell; colour is never the only sign. A missing record is empty. */
export function statusMark(raw: string | null, effective: string | null) {
  if (raw === null || effective === null)
    return { letter: "", tone: "none", label: "Нет записи" };
  return {
    letter: letters[effective] ?? "?",
    tone: effective,
    label: attendanceNames[effective] ?? effective,
  };
}

// What the grid may set: an excused mark comes from an accepted note, and an early leave needs the time in the lesson's editor.
export const settableMarks = ["unmarked", "present", "absent", "late"];

const stateNames: Record<string, string> = {
  none: "Не подтверждён",
  confirmed: "Подтверждён",
  changed: "Изменён после подтверждения",
  revoked: "Отозван",
};
export const confirmationName = (status: string) =>
  stateNames[status] ?? stateNames.none;

/** The unconfirmed days of a week that the class teacher may confirm now, with the version the client saw. */
export function confirmable(days: GridDay[]) {
  return days
    .filter(
      (d) => d.confirmation.canConfirm && d.confirmation.status !== "confirmed",
    )
    .map((d) => ({ date: d.date, version: d.confirmation.version }));
}

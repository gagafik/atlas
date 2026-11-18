// «Замены»: the pure rules of three lists (absences, all changes, monthly totals): dates in words,
// what a row says, which columns exist, filters, paging, the figures of the report and their breakdowns.
import type { ChangeType, DatedLesson, TeacherAbsence } from "./types";
import {
  CHANGE_TYPES,
  effectiveType,
  type ReportTeacher,
} from "./substitutions";
import { wordForm } from "./teachingLoad";

export { wordForm };

const SHORT_WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

function parts(iso: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = new Date(Date.UTC(y, mo - 1, d));
  if (
    t.getUTCFullYear() !== y ||
    t.getUTCMonth() !== mo - 1 ||
    t.getUTCDate() !== d
  )
    return null;
  return {
    text: `${m[3]}.${m[2]}.${m[1]}`,
    weekday: SHORT_WEEKDAYS[t.getUTCDay()],
  };
}

/** «2026-11-02» -> «02.11.2026»; anything that is not a real date comes back as it was. */
export function dateNumeric(iso: string): string {
  return parts(iso)?.text ?? iso;
}

/** «2026-11-02» -> «пн, 02.11.2026». */
export function dateShort(iso: string): string {
  const p = parts(iso);
  return p ? `${p.weekday}, ${p.text}` : iso;
}

/** One day with its weekday, a period as two dates; equal ends are one day. */
export function periodText(start: string, end: string): string {
  return !end || start === end
    ? dateShort(start)
    : `${dateNumeric(start)} — ${dateNumeric(end)}`;
}

/** The daily time of an absence. */
export function absenceTime(startTime: string, endTime: string): string {
  return startTime === "00:00" && endTime === "23:59"
    ? "весь день"
    : `каждый день ${startTime}–${endTime}`;
}

// ---- Absences ----

export interface AbsenceRowView {
  id: string;
  teacher: string;
  reason: string;
  period: string;
  time: string;
  /** The accessible names of the four buttons, each with the teacher and the first date. */
  labels: { plan: string; lessons: string; edit: string; remove: string };
}

export function absenceRows(
  absences: TeacherAbsence[],
  teacherName: (id: string) => string,
): { absence: TeacherAbsence; view: AbsenceRowView }[] {
  return absences
    .slice()
    .sort((a, b) => b.start.localeCompare(a.start))
    .map((a) => {
      const teacher = teacherName(a.teacherId);
      const who = `${teacher}, ${dateNumeric(a.start)}`;
      return {
        absence: a,
        view: {
          id: a.id,
          teacher,
          reason: a.name,
          period: periodText(a.start, a.end),
          time: absenceTime(a.startTime, a.endTime),
          labels: {
            plan: `Замены на день: ${who}`,
            lessons: `К урокам: ${who}`,
            edit: `Изменить отсутствие: ${who}`,
            remove: `Удалить отсутствие: ${who}`,
          },
        },
      };
    });
}

// ---- All changes ----

/** What the screen knows by id; an unknown id answers an empty string. */
export interface ChangeNames {
  group: (id: string) => string;
  subject: (id: string) => string;
  teacher: (id: string) => string;
  /** The group and subject a timetable placement carries, when the record names only the placement. */
  placement: (
    id: string,
  ) => { groupId: string | null; subjectId: string | null } | undefined;
}

export interface ChangeRowView {
  id: string;
  date: string;
  type: ChangeType;
  typeText: string;
  /** False when neither a group nor a subject could be found. */
  resolved: boolean;
  lesson: string;
  /** The record's own name, only when it says more than the type does. */
  name: string;
  sourceDate: string | null;
  labels: { open: string; edit: string; remove: string };
}

export const LESSON_UNKNOWN = "Занятие не определено";

export function changeRow(c: DatedLesson, names: ChangeNames): ChangeRowView {
  const type = effectiveType(c);
  const typeText = CHANGE_TYPES[type];
  const placed = c.placementId ? names.placement(c.placementId) : undefined;
  const group = names.group(c.groupId ?? placed?.groupId ?? "");
  const subject = names.subject(c.subjectId ?? placed?.subjectId ?? "");
  const teachers = (c.teacherIds ?? [])
    .map((id) => names.teacher(id))
    .filter(Boolean)
    .join(", ");
  const resolved = Boolean(group || subject);
  const lesson = resolved
    ? [
        [group, subject].filter(Boolean).join(" · "),
        c.startTime && c.endTime ? `${c.startTime}–${c.endTime}` : "",
        teachers ? `учитель: ${teachers}` : "",
      ]
        .filter(Boolean)
        .join(" · ")
    : LESSON_UNKNOWN;
  const name = c.name.trim() === typeText ? "" : c.name.trim();
  const sourceDate =
    c.sourceDate && c.sourceDate !== c.onDate ? c.sourceDate : null;
  const who = `${dateNumeric(c.onDate)}, ${resolved ? lesson : typeText}`;
  return {
    id: c.id,
    date: c.onDate,
    type,
    typeText,
    resolved,
    lesson,
    name,
    sourceDate,
    labels: {
      open: `Открыть дату: ${who}`,
      edit: `Изменить запись: ${who}`,
      remove: `Убрать изменение: ${who}`,
    },
  };
}

/** Newest date first, as the list always was. */
export function changeRows(
  changes: DatedLesson[],
  names: ChangeNames,
): { change: DatedLesson; view: ChangeRowView }[] {
  return changes
    .slice()
    .sort((a, b) => b.onDate.localeCompare(a.onDate))
    .map((change) => ({ change, view: changeRow(change, names) }));
}

/** The «Исходная дата» column exists only when some record has a source date. */
export function changeColumns(views: ChangeRowView[]): { source: boolean } {
  return { source: views.some((v) => v.sourceDate !== null) };
}

export interface ChangeFilters {
  search: string;
  type: ChangeType | "";
}
export const NO_CHANGE_FILTERS: ChangeFilters = { search: "", type: "" };

export const changeFiltersActive = (f: ChangeFilters) =>
  f.search.trim() !== "" || f.type !== "";

export function filterChanges<T extends { view: ChangeRowView }>(
  rows: T[],
  f: ChangeFilters,
): T[] {
  const q = f.search.trim().toLowerCase();
  return rows.filter(({ view: v }) => {
    if (f.type && v.type !== f.type) return false;
    return (
      !q ||
      [v.lesson, v.typeText, v.name].some((t) => t.toLowerCase().includes(q))
    );
  });
}

/** Only the types that occur, in the order of the type list. */
export function changeTypeOptions(
  views: ChangeRowView[],
): { type: ChangeType; text: string }[] {
  const present = new Set(views.map((v) => v.type));
  return (Object.keys(CHANGE_TYPES) as ChangeType[])
    .filter((t) => present.has(t))
    .map((t) => ({ type: t, text: CHANGE_TYPES[t] }));
}

export const CHANGES_PAGE_SIZE = 30;
export const changePageCount = (total: number) =>
  Math.max(1, Math.ceil(total / CHANGES_PAGE_SIZE));
export const clampChangePage = (page: number, total: number) =>
  Math.min(Math.max(0, page), changePageCount(total) - 1);
export function changePage<T>(rows: T[], page: number): T[] {
  const p = clampChangePage(page, rows.length);
  return rows.slice(p * CHANGES_PAGE_SIZE, (p + 1) * CHANGES_PAGE_SIZE);
}

// ---- Monthly totals ----

export interface ReportColumn {
  key: string;
  /** The short heading. */
  label: string;
  /** The full name, for the tooltip and the abbreviation. */
  title: string;
  value: (t: ReportTeacher) => number;
}

/** The figures of the former cards, in their order. */
export const REPORT_COLUMNS: ReportColumn[] = [
  {
    key: "planned",
    label: "План",
    title: "По расписанию",
    value: (t) => t.planned,
  },
  { key: "held", label: "Проведено", title: "Проведено", value: (t) => t.held },
  {
    key: "substitutions",
    label: "Замены",
    title: "Замены за других",
    value: (t) => t.substitutions,
  },
  {
    key: "merged",
    label: "Присоед.",
    title: "Присоединено",
    value: (t) => t.merged ?? 0,
  },
  {
    key: "missed",
    label: "Пропущено",
    title: "Пропущено",
    value: (t) => t.missed,
  },
  {
    key: "moved",
    label: "Перенесено",
    title: "Перенесено",
    value: (t) => t.moved,
  },
  {
    key: "upcoming",
    label: "Предстоит",
    title: "Предстоит",
    value: (t) => t.upcoming ?? 0,
  },
];

/** The small lines under the figures, as the cards showed them; empty ones are left out. */
export function breakdownLines(t: ReportTeacher): string[] {
  const types = (["substitution", "self_study", "move"] as const)
    .filter((k) => t.byType[k])
    .map((k) => `${CHANGE_TYPES[k]}: ${t.byType[k]}`)
    .join(", ");
  return [
    `Проведено: свои ${t.own}, за других ${t.substitutions}, дополнительные ${t.extra}`,
    types ? `Замены за других: ${types}` : "",
    `Присоединено: объединение групп, часов не добавляет`,
    `Пропущено: при отсутствии ${t.missedAbsent}, без замены ${t.uncovered ?? 0}, отменено ${t.cancelled}`,
  ].filter(Boolean);
}

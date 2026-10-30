import { csvCell, download } from "./utils";
import { categoryOf, type CategoryConfig } from "./assessmentCategories";
import type { MaterialSnapshot } from "./teachingMaterials";
import type { TopicReference } from "./curriculum";
export interface Assessment {
  id: string;
  name: string;
  kind: "points" | "label";
  maxScore: number;
  weight: number;
  // The server fills an empty category with the school default; older documents carry none and read as ФО.
  category?: string;
  // The assessment-calendar booking this work was entered from (staff only).
  bookingId?: string | null;
}
// A live assessment-calendar booking of the lesson, offered to the journal editor.
export interface JournalBooking {
  id: string;
  status: string;
  typeId: string;
  type: string;
  short: string;
  topic: string;
  calendarCategory: string;
  journalCategory: string;
  assessmentId: string | null;
}
export interface Grade {
  assessmentId: string;
  score: number | null;
  label: string;
  comment: string;
}
export interface PupilRecord {
  studentId: string;
  attendance: string;
  lateMinutes: number;
  comment: string;
  grades: Grade[];
}
export interface JournalContent {
  topic: string;
  homework: string;
  homeworkFiles?: string[];
  materials?: MaterialSnapshot[];
  curriculum?: TopicReference | null;
  homeworkLatePolicy?: "allow" | "block";
  homeworkSubmission?: "online" | "offline";
  dueDate: string | null;
  assessments: Assessment[];
  records: PupilRecord[];
}
export interface JournalLesson {
  date: string;
  start: string;
  end: string;
  subjectId: string;
  subject: string;
  group: string;
  teachers: string;
  rooms: string;
  status: string;
  rosterComplete: boolean;
  students: {
    id: string;
    name: string;
    className: string;
    enrolled: boolean;
  }[];
}
export interface JournalDocument {
  lesson: JournalLesson;
  content: JournalContent;
}
export interface JournalEntry {
  lessonKey: string;
  version: number;
  revision: number;
  updated: string | null;
  actor: string | null;
  document: JournalDocument;
  writable: boolean;
  needsSync: boolean;
  currentDate: string | null;
  futureLesson?: boolean;
  // A publication of an earlier day keeps its roster, time and teachers.
  rosterFrozen?: boolean;
  hasPublished: boolean;
  publishedVersion: number | null;
  publishedAt: string | null;
  canWithdraw: boolean;
  periodLocks?: {
    id: string;
    name: string;
    start: string;
    end: string;
    version: number;
  }[];
  attendanceApprovals?: Record<string, { id: string; version: number }>;
  bookings?: JournalBooking[];
}
export interface JournalRow {
  lessonKey: string;
  lesson: Omit<JournalLesson, "students">;
  student: { id: string; name: string };
  content: Omit<JournalContent, "records">;
  record: PupilRecord;
  version: number;
  publishedAt: string;
  absenceApproval?: { id: string; version: number };
}
export interface JournalSummary {
  attendance: Record<string, number>;
  lateMinutes: number;
  subjects: {
    subjectId: string;
    subject: string;
    pointsCount: number;
    labelCount: number;
    percentage: number | null;
    // Share of every category and the composite by the school's rule; absent in reports of older servers.
    categories?: {
      id: string;
      name: string;
      color: string;
      numeric: number;
      percentage: number;
    }[];
    sharePercentage?: number | null;
    rule?: string;
  }[];
}
export interface JournalReport {
  rows: JournalRow[];
  summary: JournalSummary;
  nextAfter: string | null;
}
// The server sends the summary of the whole period only with the first page.
export interface JournalReportPage {
  rows: JournalRow[];
  summary: JournalSummary | null;
  nextAfter: string | null;
}
const DAY_MS = 86_400_000;
function shiftDay(day: string, days: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * DAY_MS)
    .toISOString()
    .slice(0, 10);
}
// The report opens on the last 30 days and the next 14, kept inside the school year.
export function defaultReportPeriod(
  today: string,
  yearStart: string,
  yearEnd: string,
): { start: string; end: string } {
  const anchor =
    today < yearStart ? yearStart : today > yearEnd ? yearEnd : today;
  const start = shiftDay(anchor, -30),
    end = shiftDay(anchor, 14);
  return {
    start: start < yearStart ? yearStart : start,
    end: end > yearEnd ? yearEnd : end,
  };
}
const emptySummary = (): JournalSummary => ({
  attendance: { unmarked: 0, present: 0, absent: 0, late: 0, excused: 0 },
  lateMinutes: 0,
  subjects: [],
});
const rowKey = (row: JournalRow) => `${row.lessonKey}:${row.student.id}`;
// A first page replaces the report; later pages are appended without duplicates
// (a publication between two requests can repeat a row) and keep the period summary.
export function mergeReportPage(
  current: JournalReport | null,
  page: JournalReportPage,
): JournalReport {
  if (page.summary !== null || !current)
    return {
      rows: page.rows,
      summary: page.summary ?? emptySummary(),
      nextAfter: page.nextAfter,
    };
  const seen = new Set(current.rows.map(rowKey));
  return {
    rows: [
      ...current.rows,
      ...page.rows.filter((row) => !seen.has(rowKey(row))),
    ],
    summary: current.summary,
    nextAfter: page.nextAfter,
  };
}
// CSV and print cover the whole period: fetch every remaining page, with a hard stop.
export async function loadRemainingReport(
  report: JournalReport,
  fetchPage: (after: string) => Promise<JournalReportPage>,
  maxPages = 40,
): Promise<JournalReport> {
  let current = report;
  for (let loaded = 0; current.nextAfter; loaded++) {
    if (loaded >= maxPages)
      throw new Error("Слишком много записей: сократите период.");
    current = mergeReportPage(current, await fetchPage(current.nextAfter));
  }
  return current;
}
export type JournalPanelName =
  "finalGrades" | "absences" | "periods" | "coverage" | "attendance";
// The server says which management panels have anything for this account; they follow the lesson list.
export function journalPanels(options: {
  panels?: Partial<Record<JournalPanelName, boolean>>;
}): JournalPanelName[] {
  const order: JournalPanelName[] = [
    "finalGrades",
    "absences",
    "periods",
    "coverage",
    "attendance",
  ];
  return order.filter((name) => options.panels?.[name]);
}
export const attendanceNames: Record<string, string> = {
  unmarked: "Не отмечено",
  present: "Присутствует",
  absent: "Отсутствует",
  late: "Опоздание",
  excused: "Уважительная причина",
};
export function gradeLabel(grade: Grade | undefined, assessment: Assessment) {
  return !grade
    ? "—"
    : assessment.kind === "points"
      ? grade.score === null
        ? "—"
        : `${grade.score} / ${assessment.maxScore}`
      : grade.label || "—";
}
// An erased number input is "", which Number() would silently turn into 0.
export function numberField(value: string): number {
  return value.trim() === "" ? Number.NaN : Number(value);
}
export function numberValue(value: number): number | "" {
  return Number.isNaN(value) ? "" : value;
}
const maxScoreOk = (v: number) => Number.isFinite(v) && v > 0 && v <= 10000;
const weightOk = (v: number) => Number.isFinite(v) && v >= 0 && v <= 100;
// The server validates both numbers for every format, but only points show them,
// so switching to a text grade replaces an invalid hidden value with the default.
export function withKind(a: Assessment, kind: Assessment["kind"]): Assessment {
  if (kind === "points") return { ...a, kind };
  return {
    ...a,
    kind,
    maxScore: maxScoreOk(a.maxScore) ? a.maxScore : 10,
    weight: weightOk(a.weight) ? a.weight : 1,
  };
}
// First Russian problem that the server would reject, or "" when the content may be sent.
export function contentProblem(c: JournalContent): string {
  for (const a of c.assessments) {
    if (a.kind !== "points") continue;
    if (!maxScoreOk(a.maxScore))
      return `«${a.name}»: укажите максимум баллов больше 0 и не больше 10000.`;
    if (!weightOk(a.weight))
      return `«${a.name}»: укажите вес работы от 0 до 100.`;
  }
  const units = c.curriculum?.units;
  if (c.curriculum && !(Number.isInteger(units) && units! >= 1 && units! <= 12))
    return "Укажите часы по теме целым числом от 1 до 12.";
  return "";
}
// The server pages history 50 at a time, newest first; a full page ending above version 1 may have more.
export function hasEarlierRevisions(page: { version: number }[]): boolean {
  return page.length === 50 && page[page.length - 1].version > 1;
}
// Opening range of the attendance summary: the current journal period, else the school month of today, kept inside the school year.
export function defaultAttendancePeriod(
  today: string,
  yearStart: string,
  yearEnd: string,
  period?: { start: string; end: string } | null,
) {
  const clamp = (day: string) =>
    day < yearStart ? yearStart : day > yearEnd ? yearEnd : day;
  if (period) return { start: clamp(period.start), end: clamp(period.end) };
  const day = clamp(today);
  const last = new Date(
    Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)), 0),
  ).getUTCDate();
  return {
    start: clamp(day.slice(0, 8) + "01"),
    end: clamp(day.slice(0, 8) + String(last).padStart(2, "0")),
  };
}
export function journalCsv(
  rows: JournalRow[],
  filename = "Учебный журнал.csv",
  categories?: CategoryConfig | null,
) {
  const values: unknown[][] = [
    [
      "Ученик",
      "Дата",
      "Начало",
      "Конец",
      "Предмет",
      "Группа",
      "Учителя",
      "Посещаемость",
      "Минуты опоздания",
      "Комментарий ученику",
      "Работа",
      "Оценка",
      "Максимум баллов",
      "Вес",
      "Категория",
      "Комментарий к оценке",
      "Тема",
      "Домашнее задание",
      "Срок",
    ],
  ];
  for (const row of rows) {
    const { lesson, content, record } = row;
    for (const a of content.assessments.length ? content.assessments : [null]) {
      const g = record.grades.find((g) => g.assessmentId === a?.id);
      values.push([
        row.student.name,
        lesson.date,
        lesson.start,
        lesson.end,
        lesson.subject,
        lesson.group,
        lesson.teachers,
        attendanceNames[record.attendance],
        record.lateMinutes,
        record.comment,
        a?.name || "",
        a?.kind === "points" ? (g?.score ?? "") : g?.label || "",
        a?.kind === "points" ? a.maxScore : "",
        a?.kind === "points" ? a.weight : "",
        a ? categoryOf(a, categories).name : "",
        g?.comment || "",
        content.topic,
        content.homework,
        content.dueDate || "",
      ]);
    }
  }
  download(
    filename,
    "\uFEFF" +
      values
        .map((r) => r.map((v) => csvCell(String(v ?? ""))).join(";"))
        .join("\r\n"),
    "text/csv;charset=utf-8",
  );
}
export function documentRows(
  document: JournalDocument,
  lessonKey: string,
  version: number,
  approvals: Record<string, { id: string; version: number }> = {},
): JournalRow[] {
  return document.content.records.map((record) => ({
    lessonKey,
    version,
    publishedAt: "",
    lesson: document.lesson,
    content: document.content,
    record:
      approvals[record.studentId] && record.attendance === "absent"
        ? { ...record, attendance: "excused" }
        : record,
    student: {
      id: record.studentId,
      name:
        document.lesson.students.find((s) => s.id === record.studentId)?.name ||
        record.studentId,
    },
  }));
}

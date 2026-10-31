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
  // Early leave carries the time the pupil left; the absence type is a label of «Отсутствует» and never excuses by itself.
  leftAt?: string | null;
  absenceCode?: string;
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
  // The lesson has begun. A writable lesson without any mark then opens with everybody present (attendanceDefaulted):
  // the server only shows this, nothing is stored until the teacher saves.
  lessonBegun?: boolean;
  attendanceDefaulted?: boolean;
  // The school's absence types, switched-off ones included (they still name old marks).
  absenceCodes?: AbsenceCodeOption[];
}
export interface AbsenceCodeOption {
  id: string;
  name: string;
  active: boolean;
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
  // Lateness as a share of the lessons, in lessons (see LATE_SHARE_FROM); absent in reports of older servers.
  lateShare?: number;
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
  // Names of the school's absence types, for the teacher's label on a mark.
  absenceCodes?: Record<string, string>;
}
// The server sends the summary of the whole period only with the first page.
export interface JournalReportPage {
  rows: JournalRow[];
  summary: JournalSummary | null;
  nextAfter: string | null;
  absenceCodes?: Record<string, string>;
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
  attendance: {
    unmarked: 0,
    present: 0,
    absent: 0,
    late: 0,
    excused: 0,
    left: 0,
  },
  lateMinutes: 0,
  lateShare: 0,
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
      absenceCodes: page.absenceCodes ?? current?.absenceCodes,
    };
  const seen = new Set(current.rows.map(rowKey));
  return {
    rows: [
      ...current.rows,
      ...page.rows.filter((row) => !seen.has(rowKey(row))),
    ],
    summary: current.summary,
    nextAfter: page.nextAfter,
    absenceCodes: page.absenceCodes ?? current.absenceCodes,
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
  | "courseTable"
  | "finalGrades"
  | "absences"
  | "periods"
  | "coverage"
  | "attendance"
  | "attendanceGrid";
// The server says which management panels have anything for this account; they follow the lesson list.
export function journalPanels(options: {
  panels?: Partial<Record<JournalPanelName, boolean>>;
}): JournalPanelName[] {
  const order: JournalPanelName[] = [
    "courseTable",
    "finalGrades",
    "absences",
    "periods",
    "coverage",
    "attendance",
    "attendanceGrid",
  ];
  return order.filter((name) => options.panels?.[name]);
}
export const attendanceNames: Record<string, string> = {
  unmarked: "Не отмечено",
  present: "Присутствует",
  absent: "Отсутствует",
  late: "Опоздание",
  excused: "Уважительная причина",
  left: "Ушёл раньше",
};
// Lateness of this many minutes or more also counts as a share of the lesson (the server uses the same threshold).
export const LATE_SHARE_FROM = 5;
const clockMinutes = (time: string) =>
  Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
export const lessonLength = (lesson: { start: string; end: string }) =>
  clockMinutes(lesson.end) - clockMinutes(lesson.start);
// The part of the lesson missed, at most the whole lesson; short lateness counts in minutes only.
export function lateShare(minutes: number, start: string, end: string) {
  const length = lessonLength({ start, end });
  return minutes >= LATE_SHARE_FROM && length > 0
    ? Math.min(1, minutes / length)
    : 0;
}
// "0,25 урока", "2 урока": a whole number of lessons declines by the number, a fraction is always "урока".
export function lessonsText(lessons: number) {
  const rounded = Math.round(lessons * 100) / 100;
  const text = String(rounded).replace(".", ",");
  if (!Number.isInteger(rounded)) return `${text} урока`;
  const last = rounded % 10,
    tens = rounded % 100;
  if (last === 1 && tens !== 11) return `${text} урок`;
  return last >= 2 && last <= 4 && (tens < 12 || tens > 14)
    ? `${text} урока`
    : `${text} уроков`;
}
export const absenceCodeNames = (
  codes: { id: string; name: string }[] = [],
): Record<string, string> =>
  Object.fromEntries(codes.map((c) => [c.id, c.name]));
// Initials for the avatar of a pupil card: the first letter of the first two words that start with a letter.
export function initials(name: string) {
  const words = name.split(/\s+/).filter((w) => /^\p{L}/u.test(w));
  return words.length
    ? words
        .slice(0, 2)
        .map((w) => w[0])
        .join("")
        .toUpperCase()
    : "·";
}
// "Опоздание · 10 мин · 25% урока", "Ушёл раньше · в 09:20", "Отсутствует · Болезнь". An absence accepted by a note
// reads «Уважительная причина» and does not repeat the teacher's label as its reason.
export function attendanceText(
  record: {
    attendance: string;
    lateMinutes: number;
    leftAt?: string | null;
    absenceCode?: string | null;
  },
  lesson: { start: string; end: string },
  codeNames: Record<string, string> = {},
) {
  const parts = [attendanceNames[record.attendance] ?? record.attendance];
  if (record.attendance === "late" && record.lateMinutes > 0) {
    parts.push(`${record.lateMinutes} мин`);
    const share = lateShare(record.lateMinutes, lesson.start, lesson.end);
    if (share > 0) parts.push(`${Math.round(share * 100)}% урока`);
  }
  if (record.attendance === "left" && record.leftAt)
    parts.push(`в ${record.leftAt}`);
  if (record.attendance === "absent" && record.absenceCode)
    parts.push(codeNames[record.absenceCode] ?? record.absenceCode);
  return parts.join(" · ");
}
// Choosing a status drops what belongs only to another one: minutes, leaving time and absence type.
export function changeAttendance(
  record: PupilRecord,
  status: string,
): PupilRecord {
  return {
    ...record,
    attendance: status,
    lateMinutes: status === "late" ? record.lateMinutes : 0,
    leftAt: status === "left" ? (record.leftAt ?? null) : null,
    absenceCode: status === "absent" ? (record.absenceCode ?? "") : "",
  };
}
// First Russian problem the server would refuse in the marks, or "" when they may be sent.
export function attendanceProblem(document: JournalDocument): string {
  const { lesson, content } = document;
  for (const record of content.records) {
    if (record.attendance !== "left") continue;
    const name =
      lesson.students.find((s) => s.id === record.studentId)?.name ??
      record.studentId;
    if (!record.leftAt) return `${name}: укажите время ухода.`;
    if (record.leftAt <= lesson.start || record.leftAt >= lesson.end)
      return `${name}: время ухода должно быть внутри урока, ${lesson.start}–${lesson.end}.`;
  }
  return "";
}
type Roster = JournalLesson["students"];
type Approvals = Record<string, unknown>;
const enrolledIds = (students: Roster) =>
  new Set(students.filter((s) => s.enrolled).map((s) => s.id));
// Nobody to mark as absent: every pupil of the roster is present or not marked yet, or is absent with an accepted note.
export function noAbsentees(
  content: JournalContent,
  students: Roster,
  approvals: Approvals = {},
) {
  const enrolled = enrolledIds(students);
  return content.records.every(
    (r) =>
      !enrolled.has(r.studentId) ||
      r.attendance === "present" ||
      r.attendance === "unmarked" ||
      (r.attendance === "absent" && r.studentId in approvals),
  );
}
// Pupils of the roster who are not marked become present, or absent where a school note covers the lesson.
// Marks already made and pupils of the earlier roster stay as they are.
export function withNoAbsentees(
  content: JournalContent,
  students: Roster,
  approvals: Approvals = {},
): JournalContent {
  const enrolled = enrolledIds(students);
  return {
    ...content,
    records: content.records.map((r) =>
      r.attendance === "unmarked" && enrolled.has(r.studentId)
        ? changeAttendance(r, r.studentId in approvals ? "absent" : "present")
        : r,
    ),
  };
}
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
  codeNames: Record<string, string> = {},
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
      "Время ухода",
      "Тип отсутствия",
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
        record.leftAt || "",
        // The teacher's type is a label on an absence; a row excused by a school note does not show it as the reason.
        record.attendance === "absent" && record.absenceCode
          ? (codeNames[record.absenceCode] ?? record.absenceCode)
          : "",
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

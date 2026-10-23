import { csvCell, download } from "./utils";
import type { MaterialSnapshot } from "./teachingMaterials";
import type { TopicReference } from "./curriculum";
export interface Assessment {
  id: string;
  name: string;
  kind: "points" | "label";
  maxScore: number;
  weight: number;
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
export interface JournalReport {
  rows: JournalRow[];
  summary: {
    attendance: Record<string, number>;
    lateMinutes: number;
    subjects: {
      subjectId: string;
      subject: string;
      pointsCount: number;
      labelCount: number;
      percentage: number | null;
    }[];
  };
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
export function journalCsv(
  rows: JournalRow[],
  filename = "Учебный журнал.csv",
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

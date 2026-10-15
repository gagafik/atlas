import { csvCell, download } from "./utils";
import type { MaterialSnapshot } from "./teachingMaterials";
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

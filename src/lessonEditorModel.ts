// The pure steps behind the opened lesson: one row per pupil, the counters over the marks, the date line and the state.
// The editor itself holds no rules; every number here is derived from the entry the server sent.
import { dayText } from "./pupilJournal";
import {
  attendanceNames,
  type AbsenceCodeOption,
  type JournalContent,
  type JournalEntry,
  type JournalLesson,
  type PupilRecord,
} from "./journal";

type Student = JournalLesson["students"][number];

// The marks a teacher chooses with one click. «Не отмечено» and the old «Уважительная причина» are never offered:
// an excused absence comes from a note accepted by the school.
export const ATTENDANCE_CHOICES = [
  { status: "present", label: "Был" },
  { status: "absent", label: "Нет" },
  { status: "late", label: "Опоздал" },
  { status: "left", label: "Ушёл" },
] as const;
export type AttendanceChoice = (typeof ATTENDANCE_CHOICES)[number]["status"];

// The absence types to offer for one record: the active ones, and the type the record holds even if it was switched off
// (or is no longer in the list), so a correction does not drop it.
export function absenceOptions(
  codes: AbsenceCodeOption[] | undefined,
  current: string | undefined,
): AbsenceCodeOption[] {
  const all = [...(codes ?? [])];
  if (current && !all.some((c) => c.id === current))
    all.push({ id: current, name: current, active: false });
  return all.filter((c) => c.active || c.id === current);
}

export interface PupilRow {
  number: number;
  student: Student;
  record: PupilRecord;
  // A note accepted by the school covers this lesson.
  approved: boolean;
  // An old mark that a button cannot set again.
  legacy: "unmarked" | "excused" | null;
  // The mark carries fields of its own (type, minutes, time).
  extra: "absent" | "late" | "left" | null;
  // Filled comments: the one to the pupil and those under the grades.
  comments: number;
  codes: AbsenceCodeOption[];
}

export function pupilRows(entry: JournalEntry): PupilRow[] {
  const { lesson, content } = entry.document;
  return lesson.students.map((student, index) => {
    const record: PupilRecord = content.records.find(
      (p) => p.studentId === student.id,
    ) ?? {
      studentId: student.id,
      attendance: "unmarked",
      lateMinutes: 0,
      comment: "",
      grades: [],
    };
    const status = record.attendance;
    return {
      number: index + 1,
      student,
      record,
      approved: !!entry.attendanceApprovals?.[student.id],
      legacy: status === "unmarked" || status === "excused" ? status : null,
      extra:
        status === "absent" || status === "late" || status === "left"
          ? status
          : null,
      comments: [record.comment, ...record.grades.map((g) => g.comment)].filter(
        Boolean,
      ).length,
      codes: absenceOptions(entry.absenceCodes, record.absenceCode),
    };
  });
}

// The lesson mixes classes, so every row names the class of the pupil.
export const showClasses = (students: Pick<Student, "className">[]) =>
  new Set(students.map((s) => s.className)).size > 1;

// The column with the fields of a mark shows only while some row has any.
export const needsDetails = (rows: PupilRow[]) =>
  rows.some((r) => r.extra !== null || r.legacy !== null || r.approved);

// Pupils of the roster by mark. Those who left the group are not counted: they are not in the lesson any more.
export function attendanceCounts(
  content: Pick<JournalContent, "records">,
  students: Pick<Student, "id" | "enrolled">[],
): Record<string, number> {
  const enrolled = new Set(students.filter((s) => s.enrolled).map((s) => s.id));
  const counts: Record<string, number> = Object.fromEntries(
    Object.keys(attendanceNames).map((status) => [status, 0]),
  );
  for (const r of content.records)
    if (enrolled.has(r.studentId))
      counts[r.attendance] = (counts[r.attendance] ?? 0) + 1;
  return counts;
}

const SUMMARY: [string, string][] = [
  ["absent", "Нет"],
  ["late", "Опоздали"],
  ["left", "Ушли"],
  ["excused", "Уважительно"],
  ["unmarked", "Не отмечены"],
];
// What differs from «all present», in the order of the line: «Нет: 2», «Опоздали: 1».
export const summaryParts = (counts: Record<string, number>) =>
  SUMMARY.filter(([status]) => counts[status] > 0).map(
    ([status, label]): [string, number] => [label, counts[status]],
  );

// «16.11.2026 · 08:30–09:15 · Учитель · 301»; a part that is empty is left out.
export function lessonLine(
  lesson: Pick<JournalLesson, "date" | "start" | "end" | "teachers" | "rooms">,
) {
  return [
    lesson.date ? dayText(lesson.date) : "",
    lesson.start && lesson.end ? `${lesson.start}–${lesson.end}` : "",
    lesson.teachers,
    lesson.rooms,
  ]
    .filter(Boolean)
    .join(" · ");
}

export type EditorStateKind =
  "unsaved" | "readonly" | "new" | "published" | "draft";

// The one state of the lesson record shown beside the save buttons.
export function editorState(
  entry: Pick<
    JournalEntry,
    "writable" | "version" | "hasPublished" | "publishedVersion"
  >,
  dirty: boolean,
): { kind: EditorStateKind; text: string } {
  if (dirty) return { kind: "unsaved", text: "Есть несохранённые поля" };
  if (!entry.writable) return { kind: "readonly", text: "Только просмотр" };
  if (!entry.version) return { kind: "new", text: "Новая запись" };
  if (entry.hasPublished && entry.publishedVersion === entry.version)
    return {
      kind: "published",
      text: `Опубликовано · редакция ${entry.version}`,
    };
  return { kind: "draft", text: `Черновик · редакция ${entry.version}` };
}

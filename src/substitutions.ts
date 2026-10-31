import { csvCell } from "./utils";
import type { ChangeType, DatedLesson, SubstitutionWeights } from "./types";

// Same words as backend/calendar_ops.TYPES.
export const CHANGE_TYPES: Record<ChangeType, string> = {
  substitution: "Замена",
  merge: "Объединение",
  move: "Перенос",
  self_study: "Самостоятельная работа",
  cancel: "Отмена",
  extra: "Дополнительный урок",
};
export const DEFAULT_WEIGHTS: SubstitutionWeights = {
  subject: 40,
  group: 30,
  presence: 20,
  fairness: 20,
  building: 10,
};
export const WEIGHT_LABELS: [keyof SubstitutionWeights, string][] = [
  ["subject", "Ведёт предмет"],
  ["group", "Знает учеников"],
  ["presence", "Уже в школе"],
  ["fairness", "Равномерность замен"],
  ["building", "Здание"],
];

export interface Factor {
  key: string;
  label: string;
  points: number;
  max: number;
  detail: string;
}
export interface RankedTeacher {
  id: string;
  name: string;
  available: boolean;
  reasons: string[];
  lessons: number;
  score: number;
  substitutions: number;
  factors: Factor[];
  checked?: boolean;
}
export interface MergeOption {
  rowId: string;
  group: string;
  subject: string;
  teachers: string;
  rooms: string;
  teacherIds: string[];
  roomIds: string[];
  start: string;
  end: string;
  capacity: number | null;
  attendance: number | null;
  fits: boolean | null;
}
export interface ReportTeacher {
  teacherId?: string;
  name?: string;
  planned: number;
  held: number;
  own: number;
  substitutions: number;
  extra: number;
  missed: number;
  missedAbsent: number;
  cancelled: number;
  moved: number;
  merged?: number;
  upcoming?: number;
  uncovered?: number;
  byType: Record<string, number>;
}
export interface SubstitutionReportData {
  revision: number;
  month: string;
  first: string;
  last: string;
  teachers: (ReportTeacher & { teacherId: string; name: string })[];
  totals: ReportTeacher;
}

// Mirrors backend calendar_ops.change_type for records made before types existed.
export function effectiveType(
  c: Pick<DatedLesson, "kind" | "changeType" | "onDate" | "sourceDate">,
): ChangeType {
  if (c.changeType) return c.changeType;
  if (c.kind === "cancel") return "cancel";
  if (c.kind === "add") return "extra";
  return c.onDate !== c.sourceDate ? "move" : "substitution";
}

// What blocks saving a lesson change before the server check. `row` is the lesson as
// the day shows it and `saved` the stored record being edited, if any: a stored
// same-date «Перенос» is displayed at its already shifted time, so an unchanged
// time is that shift, not an absent one.
export function lessonProblem(
  l: DatedLesson,
  row: { start: string; end: string } | null,
  saved?: DatedLesson | null,
) {
  if (l.kind !== "change") return "";
  if (l.changeType === "merge" && !l.mergeWith)
    return "Выберите урок, к которому присоединяется группа.";
  if (l.changeType === "self_study" && !l.teacherIds?.length)
    return "Для самостоятельной работы укажите ответственного сотрудника.";
  const kept =
    saved?.changeType === "move" &&
    saved.onDate === saved.sourceDate &&
    saved.startTime === l.startTime &&
    saved.endTime === l.endTime;
  if (
    l.changeType === "move" &&
    l.onDate === l.sourceDate &&
    row &&
    l.startTime === row.start &&
    l.endTime === row.end &&
    !kept
  )
    return "Перенос должен менять дату или время урока.";
  return "";
}

export function factorLine(f: Factor) {
  return `${f.label}: ${f.points}/${f.max} — ${f.detail}`;
}

const REPORT_HEADER = [
  "Учитель",
  "По расписанию",
  "Проведено",
  "Свои уроки",
  "Замены за других",
  "Замена",
  "Объединение",
  "Самостоятельная работа",
  "Перенос (с заменой)",
  "Дополнительные",
  "Пропущено",
  "Из них при отсутствии",
  "Отменено",
  "Перенесено",
];
const reportLine = (name: string, t: ReportTeacher) =>
  [
    name,
    t.planned,
    t.held,
    t.own,
    t.substitutions,
    t.byType.substitution || 0,
    t.byType.merge || 0,
    t.byType.self_study || 0,
    t.byType.move || 0,
    t.extra,
    t.missed,
    t.missedAbsent,
    t.cancelled,
    t.moved,
  ].map(String);
// Semicolon CSV with a BOM for Excel; names are protected against formulas.
export function reportCsv(report: SubstitutionReportData) {
  return (
    "\uFEFF" +
    [
      REPORT_HEADER,
      ...report.teachers.map((t) => reportLine(t.name, t)),
      reportLine("Итого", report.totals),
    ]
      .map((row) => row.map(csvCell).join(";"))
      .join("\r\n")
  );
}

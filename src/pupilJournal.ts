// The pupil's and the parent's journal: types of the server answers and the pure steps between an answer and a screen.
// Nothing here recomputes an average or a total: the school's rules live on the server and arrive in the summary.
import {
  lateShare,
  type BellSlot,
  type JournalPeriodOption,
  type JournalRow,
  type JournalSummary,
} from "./journal";
import { placeInSlots } from "./bells";

export interface PupilMark {
  lessonKey: string;
  date: string;
  start: string;
  end: string;
  subjectId: string;
  subject: string;
  teachers: string;
  assessmentId: string;
  name: string;
  kind: "points" | "label";
  category: string;
  maxScore: number;
  weight: number;
  score: number | null;
  label: string;
  comment: string;
  // Not yet signed by this account (a parent signs, a pupil marks as seen).
  unsigned: boolean;
}
export interface PupilAttendanceRow {
  lessonKey: string;
  date: string;
  start: string;
  end: string;
  subjectId: string;
  subject: string;
  teachers: string;
  group: string;
  rooms: string;
  attendance: string;
  lateMinutes: number;
  leftAt: string | null;
  absenceCode: string;
  // An absence that an accepted note of the family turned into an excused one.
  byNote: boolean;
}
export interface GradeScale {
  name: string;
  kind: "thresholds" | "labels";
  // The percent arrives as a string from the school's Decimal scale.
  bands: { label: string; minPercent: string | number }[];
}
export interface PupilPeriod {
  studentId: string;
  start: string;
  end: string;
  source: "published";
  summary: JournalSummary;
  marks: PupilMark[];
  attendance: PupilAttendanceRow[];
  absenceCodes: Record<string, string>;
  scale: GradeScale | null;
  signedAt: string | null;
}
export interface BehaviourNote {
  id: string;
  version: number;
  kind: "praise" | "remark";
  day: string;
  category: string;
  levelTitle: string;
  text: string;
  author: string;
  lesson: string;
  letter: boolean;
  letterNumber: string;
  requireAck: boolean;
  acknowledged: boolean;
  subjectId: string | null;
  subject: string | null;
}
export interface BehaviourSubject {
  subjectId: string | null;
  subject: string | null;
  praise: number;
  remark: number;
}
export interface BehaviourFamily {
  items: BehaviourNote[];
  nextBefore: number | null;
  praise: number;
  remark: number;
  subjects: BehaviourSubject[];
}
export interface FinalMark {
  label: string;
  outcome: string;
}

const DAY_MS = 86_400_000;
const parseDay = (day: string) => Date.parse(`${day}T00:00:00Z`);
const textDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);
export const shiftDate = (day: string, days: number) =>
  textDay(parseDay(day) + days * DAY_MS);
export const daysBetween = (start: string, end: string) =>
  (parseDay(end) - parseDay(start)) / DAY_MS;
const spanDays = (a: { start: string; end: string }) =>
  daysBetween(a.start, a.end);
const round2 = (value: number) => Math.round(value * 100) / 100;

// ——— Periods ———
export interface PeriodChoice {
  id: string;
  name: string;
  start: string;
  end: string;
}
// The longest range the server answers is 367 days; the choice for the whole year stays inside it.
export const YEAR_CHOICE = "school-year";
export function periodChoices(
  periods: JournalPeriodOption[],
  yearStart: string,
  yearEnd: string,
): PeriodChoice[] {
  const end =
    yearEnd < shiftDate(yearStart, 366) ? yearEnd : shiftDate(yearStart, 366);
  const choices = periods.map(({ id, name, start, end }) => ({
    id,
    name,
    start,
    end,
  }));
  const covered = periods.some(
    (p) => p.kind === "year" && p.start <= yearStart && p.end >= end,
  );
  return covered
    ? choices
    : [
        ...choices,
        { id: YEAR_CHOICE, name: "Весь учебный год", start: yearStart, end },
      ];
}
// The shortest period that holds today, else the latest one that has ended, else the first. The whole-year choice
// is the last resort: a school with periods opens on one of them.
export function defaultChoice(
  choices: PeriodChoice[],
  today: string,
): PeriodChoice {
  const own = choices.filter((c) => c.id !== YEAR_CHOICE);
  const holding = own
    .filter((c) => c.start <= today && today <= c.end)
    .sort(
      (a, b) => spanDays(a) - spanDays(b) || a.start.localeCompare(b.start),
    );
  if (holding[0]) return holding[0];
  const ended = own
    .filter((c) => c.end < today)
    .sort((a, b) => b.end.localeCompare(a.end) || spanDays(a) - spanDays(b));
  return ended[0] ?? own[0] ?? choices[0];
}

// ——— Marks, subjects, notes ———
export interface SubjectRow {
  subjectId: string;
  subject: string;
  marks: PupilMark[];
  // The school's average (shares of the categories); null when the rule cannot give one.
  average: number | null;
  // The plain weighted percentage of the points, for the explanation.
  plain: number | null;
  rule: string;
  categories: NonNullable<JournalSummary["subjects"][number]["categories"]>;
  pointsCount: number;
  labelCount: number;
  final: FinalMark | null;
  notes: { praise: number; remark: number };
}
const inOrder = (a: PupilMark, b: PupilMark) =>
  a.date.localeCompare(b.date) ||
  a.start.localeCompare(b.start) ||
  a.assessmentId.localeCompare(b.assessmentId);
// One row per subject the period has lessons in, with or without marks, in the order of the school's alphabet.
export function subjectRows(
  period: PupilPeriod,
  finals: Record<string, FinalMark>,
  notes: Pick<BehaviourSubject, "subjectId" | "praise" | "remark">[],
): SubjectRow[] {
  const given = new Map(notes.map((n) => [n.subjectId, n]));
  return period.summary.subjects
    .map((s) => ({
      subjectId: s.subjectId,
      subject: s.subject,
      marks: period.marks
        .filter((m) => m.subjectId === s.subjectId)
        .sort(inOrder),
      average: s.sharePercentage ?? null,
      plain: s.percentage,
      rule: s.rule ?? "",
      categories: s.categories ?? [],
      pointsCount: s.pointsCount,
      labelCount: s.labelCount,
      final: finals[s.subjectId] ?? null,
      notes: {
        praise: given.get(s.subjectId)?.praise ?? 0,
        remark: given.get(s.subjectId)?.remark ?? 0,
      },
    }))
    .sort((a, b) => a.subject.localeCompare(b.subject, "ru"));
}
// The first row of EduPage's summary: praise and remarks of the period; absent when there are none.
export function behaviourRow(
  notes: { praise: number; remark: number } | null,
): { praise: number; remark: number } | null {
  return notes && (notes.praise || notes.remark)
    ? { praise: notes.praise, remark: notes.remark }
    : null;
}
export function marksByDate(marks: PupilMark[]): PupilMark[] {
  return [...marks].sort(
    (a, b) =>
      b.date.localeCompare(a.date) ||
      b.start.localeCompare(a.start) ||
      b.assessmentId.localeCompare(a.assessmentId),
  );
}
const decimal = (value: number, digits: number) =>
  value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
export const formatNumber = (value: number, digits = 2) =>
  decimal(value, digits);
export const dayText = (day: string) => day.split("-").reverse().join(".");
const weekdays = [
  "воскресенье",
  "понедельник",
  "вторник",
  "среда",
  "четверг",
  "пятница",
  "суббота",
];
export const weekdayName = (day: string) =>
  weekdays[new Date(`${day}T00:00:00Z`).getUTCDay()];
/** «Ноябрь 2026 г.»: only the first letter is capital, so the «г.» stays small. */
export function monthTitle(day: string) {
  const text = new Date(`${day}T00:00:00Z`).toLocaleDateString("ru-RU", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
  return text.charAt(0).toUpperCase() + text.slice(1);
}
/** «9 / 10 = 90%»: the points of a work, its maximum and the percent. */
export function markText(mark: Pick<PupilMark, "score" | "maxScore">) {
  if (mark.score === null) return "—";
  const percent = (mark.score / mark.maxScore) * 100;
  return `${decimal(mark.score, 2)} / ${decimal(mark.maxScore, 2)} = ${decimal(percent, 1)}%`;
}
export interface ChartBlock {
  key: string;
  category: string;
  text: string;
  percent: number;
  // The part of the bar this work takes: its weight times its maximum among all the blocks.
  share: number;
}
// A bar of one subject: one block per numeric work with a weight. Text grades and works of weight 0 stay out.
export function chartBlocks(marks: PupilMark[]): {
  blocks: ChartBlock[];
  excluded: number;
} {
  const counted = marks.filter(
    (m) => m.kind === "points" && m.score !== null && m.weight > 0,
  );
  const total = counted.reduce((sum, m) => sum + m.weight * m.maxScore, 0);
  return {
    blocks: counted.map((m) => ({
      key: `${m.lessonKey}:${m.assessmentId}`,
      category: m.category,
      text: markText(m),
      percent: (m.score! / m.maxScore) * 100,
      share: (m.weight * m.maxScore) / total,
    })),
    excluded: marks.length - counted.length,
  };
}
export interface BandSegment {
  label: string;
  from: number;
  to: number;
  // 0 is the lowest band (red) and the highest number the best one (green).
  tone: number;
}
// The background of the chart: the bands of a thresholds scale from the lowest to the highest. A labels scale has none.
export function bandSegments(scale: GradeScale | null): BandSegment[] {
  if (!scale || scale.kind !== "thresholds") return [];
  const bands = scale.bands
    .map((b) => ({ label: b.label, from: Number(b.minPercent) }))
    .filter((b) => Number.isFinite(b.from))
    .sort((a, b) => a.from - b.from);
  return bands.map((b, i) => ({
    label: b.label,
    from: b.from,
    to: bands[i + 1]?.from ?? 100,
    tone: i,
  }));
}

// ——— Attendance ———
export type MarkKind =
  "present" | "absent" | "excused" | "late" | "left" | "school" | "unmarked";
// An absence typed «Представляет школу» is a mark of its own; every other state is the one the teacher set.
export const markKind = (
  row: Pick<PupilAttendanceRow, "attendance" | "absenceCode">,
): MarkKind =>
  row.attendance === "absent" && row.absenceCode === "school"
    ? "school"
    : (row.attendance as MarkKind);
export interface AttendanceColumn {
  // The index in the bell list; null for the column of lessons outside every bell period.
  slot: number | null;
  label: string;
}
export interface AttendanceDay {
  date: string;
  // The records of the day by column.
  cells: PupilAttendanceRow[][];
  absent: number;
  excused: number;
  late: number;
  lateShare: number;
  left: number;
  unmarked: number;
}
export function foldAttendance(
  rows: PupilAttendanceRow[],
  slotsForDate: (date: string) => BellSlot[],
): { columns: AttendanceColumn[]; days: AttendanceDay[] } {
  const dates = [...new Set(rows.map((r) => r.date))].sort();
  const labels = new Map<number, string>();
  let outside = false;
  const placed = dates.map((date) => {
    const found = placeInSlots(
      slotsForDate(date),
      rows.filter((r) => r.date === date),
    );
    for (const row of found) {
      if (row.slot === null) outside = true;
      else if (!labels.has(row.slot)) labels.set(row.slot, row.label);
    }
    return { date, found };
  });
  const columns: AttendanceColumn[] = [...labels]
    .sort((a, b) => a[0] - b[0])
    .map(([slot, label]) => ({ slot, label }));
  if (outside) columns.push({ slot: null, label: "Вне расписания" });
  const days = placed.map(({ date, found }) => {
    const own = found.flatMap((r) => r.items);
    const count = (state: string) =>
      own.filter((r) => r.attendance === state).length;
    return {
      date,
      cells: columns.map((c) =>
        found.filter((r) => r.slot === c.slot).flatMap((r) => r.items),
      ),
      absent: count("absent"),
      excused: count("excused"),
      late: count("late"),
      lateShare: round2(
        own.reduce(
          (sum, r) => sum + lateShare(r.lateMinutes, r.start, r.end),
          0,
        ),
      ),
      left: count("left"),
      unmarked: count("unmarked"),
    };
  });
  return { columns, days };
}

// ——— The day ———
export interface TimetableRow {
  start: string;
  end: string;
  subject: string;
  group: string;
  teachers: string;
  rooms: string;
  status: string;
  activity: string;
}
export interface DayRow {
  key: string;
  start: string;
  end: string;
  subject: string;
  group: string;
  teachers: string;
  rooms: string;
  // The timetable's word about the lesson: cancelled, changed, added; empty for a lesson that only the journal knows.
  status: string;
  record: JournalRow | null;
}
const overlaps = (
  a: { start: string; end: string },
  b: { start: string; end: string },
) => a.start < b.end && b.start < a.end;
// The lessons of one day: the timetable's lessons, each with its published record when there is one, and the
// records the timetable no longer has (a moved or removed lesson stays visible on the day it was published).
export function mergeDay(
  timetable: TimetableRow[],
  published: JournalRow[],
): DayRow[] {
  const lessons = timetable.filter((r) => r.activity === "lesson");
  const taken = new Map<number, JournalRow>();
  const used = new Set<JournalRow>();
  const claim = (index: number, record: JournalRow | undefined) => {
    if (!record) return;
    taken.set(index, record);
    used.add(record);
  };
  const free = (test: (r: JournalRow) => boolean) =>
    published.find((r) => !used.has(r) && test(r));
  lessons.forEach((l, i) =>
    claim(
      i,
      free((r) => r.lesson.subject === l.subject && r.lesson.start === l.start),
    ),
  );
  lessons.forEach((l, i) => {
    if (!taken.has(i))
      claim(
        i,
        free((r) => r.lesson.subject === l.subject && overlaps(r.lesson, l)),
      );
  });
  const rows: DayRow[] = lessons.map((l, i) => ({
    key: taken.get(i)?.lessonKey ?? `${l.start}-${l.subject}-${i}`,
    start: l.start,
    end: l.end,
    subject: l.subject,
    group: l.group,
    teachers: l.teachers,
    rooms: l.rooms,
    status: l.status,
    record: taken.get(i) ?? null,
  }));
  for (const r of published)
    if (!used.has(r))
      rows.push({
        key: r.lessonKey,
        start: r.lesson.start,
        end: r.lesson.end,
        subject: r.lesson.subject,
        group: r.lesson.group,
        teachers: r.lesson.teachers,
        rooms: r.lesson.rooms,
        status: "",
        record: r,
      });
  return rows.sort(
    (a, b) =>
      a.start.localeCompare(b.start) ||
      a.subject.localeCompare(b.subject, "ru"),
  );
}

// ——— Notes ———
export type NoteGrouping = "subject" | "teacher" | "date" | "type";
export interface NoteGroup {
  key: string;
  title: string;
  items: BehaviourNote[];
}
const noteKinds: Record<BehaviourNote["kind"], string> = {
  praise: "Похвала",
  remark: "Замечание",
};
export const noteKind = (note: Pick<BehaviourNote, "kind">) =>
  noteKinds[note.kind];
export function groupNotes(
  items: BehaviourNote[],
  by: NoteGrouping,
): NoteGroup[] {
  const groups = new Map<string, NoteGroup>();
  for (const note of items) {
    const [key, title] =
      by === "subject"
        ? [note.subjectId ?? "", note.subject ?? "Без предмета"]
        : by === "teacher"
          ? [note.author, note.author]
          : by === "date"
            ? [note.day, note.day]
            : [note.kind, noteKind(note)];
    const group = groups.get(key) ?? { key, title, items: [] };
    group.items.push(note);
    groups.set(key, group);
  }
  const found = [...groups.values()];
  // Notes without a subject come last; dates run from the newest, the other groupings alphabetically.
  if (by === "date") return found.sort((a, b) => b.key.localeCompare(a.key));
  if (by === "type") return found.sort((a, b) => a.key.localeCompare(b.key));
  return found.sort(
    (a, b) =>
      Number(a.key === "") - Number(b.key === "") ||
      a.title.localeCompare(b.title, "ru"),
  );
}

// ——— The month calendar ———
export interface MonthCell {
  date: string;
  inMonth: boolean;
}
// Six weeks from the Monday on or before the first of the month.
export function monthCells(anyDate: string): MonthCell[] {
  const first = parseDay(`${anyDate.slice(0, 7)}-01`);
  const offset = (new Date(first).getUTCDay() + 6) % 7;
  return Array.from({ length: 42 }, (_, i) => {
    const date = textDay(first + (i - offset) * DAY_MS);
    return { date, inMonth: date.slice(0, 7) === anyDate.slice(0, 7) };
  });
}

// ——— What the four screens share while the pupil moves between them ———
// A plain map, nothing is stored: the child and the period chosen on one screen stay chosen on the next.
export interface PeriodSelection {
  id: string;
  start: string;
  end: string;
}
const memory = new Map<string, { child?: string; period?: PeriodSelection }>();
const slotOf = (workspace: string) => memory.get(workspace) ?? {};
export const rememberChild = (workspace: string, child: string) =>
  memory.set(workspace, { ...slotOf(workspace), child });
export const recallChild = (workspace: string) => slotOf(workspace).child;
export const rememberPeriod = (workspace: string, period: PeriodSelection) =>
  memory.set(workspace, { ...slotOf(workspace), period });
export const recallPeriod = (workspace: string) => slotOf(workspace).period;

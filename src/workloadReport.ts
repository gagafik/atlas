// Types and date helpers of the teacher workload report. The server decides who may see what and clips periods to the school year.
export interface WorkloadFigures {
  planned: number;
  plannedToDate: number;
  taught: number;
  minutes: number;
  own: number;
  substitutions: number;
  extra: number;
  journalOnly: number;
  merged: number;
  missed: number;
  missedAbsent: number;
  uncovered: number;
  cancelled: number;
  moved: number;
  upcoming: number;
  confirmed: number;
  differs: number;
  byType: Record<string, number>;
  delta: number;
}
export interface WorkloadLesson {
  date: string;
  start: string;
  end: string;
  group: string;
  subject: string;
  minutes: number;
  outcome: string;
  kind: string | null;
  confirmed: boolean;
  differs: boolean;
  absent: boolean;
}
export interface WorkloadWeek extends WorkloadFigures {
  start: string;
  end: string;
}
export interface WorkloadTeacher extends WorkloadFigures {
  teacherId: string;
  name: string;
  weeks?: WorkloadWeek[];
  lessons?: WorkloadLesson[];
}
export interface WorkloadReport {
  revision: number;
  start: string;
  end: string;
  asOf: string;
  source: string;
  teachers: WorkloadTeacher[];
  totals: WorkloadFigures;
}
export interface WorkloadOptions {
  canSeeAll: boolean;
  teachers: { id: string; name: string }[];
  yearStart: string;
  yearEnd: string;
  today: string;
  periods: { id: string; name: string; start: string; end: string }[];
}

export const OUTCOMES: Record<string, string> = {
  taught: "Проведён",
  merged: "Присоединён к другому уроку",
  upcoming: "Предстоит",
  moved: "Перенесён",
  cancelled: "Отменён",
  replaced: "Заменён",
  uncovered: "Без замены",
};
export const KINDS: Record<string, string> = {
  own: "Свой урок",
  extra: "Дополнительный урок",
  journal: "По журналу вне расписания",
  substitution: "Замена",
  merge: "Объединение",
  self_study: "Самостоятельная работа",
  move: "Перенос",
};

const DAY = 86400000;
// Dates are calendar days, not instants: compute in UTC so the browser's zone never shifts them.
const parse = (day: string) => {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
};
const iso = (date: Date) => date.toISOString().slice(0, 10);

// Monday and Sunday of the week that holds the day.
export function weekOf(day: string) {
  const date = parse(day);
  const start = new Date(date.getTime() - ((date.getUTCDay() + 6) % 7) * DAY);
  return { start: iso(start), end: iso(new Date(start.getTime() + 6 * DAY)) };
}

// First and last day of the calendar month that holds the day.
export function monthOf(day: string) {
  const date = parse(day);
  const year = date.getUTCFullYear(),
    month = date.getUTCMonth();
  return {
    start: iso(new Date(Date.UTC(year, month, 1))),
    end: iso(new Date(Date.UTC(year, month + 1, 0))),
  };
}

// Academic hours are lessons; this is clock time, one decimal with a comma.
export const hours = (minutes: number) =>
  (minutes / 60).toFixed(1).replace(".", ",");

export const clip = (
  range: { start: string; end: string },
  year: { yearStart: string; yearEnd: string },
) => ({
  start: range.start < year.yearStart ? year.yearStart : range.start,
  end: range.end > year.yearEnd ? year.yearEnd : range.end,
});

// Whether typed dates make a period the server can count: while a date is being edited an input is empty or the end is before the start.
export const validPeriod = (start: string, end: string) =>
  !!start && !!end && start <= end;

// The part of a range inside the school year; null when none of it is (a preset in the summer break).
export function within(
  range: { start: string; end: string },
  year: { yearStart: string; yearEnd: string },
) {
  const found = clip(range, year);
  return found.start <= found.end ? found : null;
}

// dd.mm.yyyy and dd.mm of an ISO day.
export const dayLabel = (day: string) =>
  `${day.slice(8, 10)}.${day.slice(5, 7)}.${day.slice(0, 4)}`;
const shortDay = (day: string) => `${day.slice(8, 10)}.${day.slice(5, 7)}`;
export const signed = (n: number) => (n > 0 ? `+${n}` : String(n));

const WEEKDAYS = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
// «Пн 02.11»: the weekday and the date of a lesson row.
export const weekdayDate = (day: string) =>
  `${WEEKDAYS[parse(day).getUTCDay()]} ${shortDay(day)}`;
// «02.11 – 08.11»: a week in the select and in the label of its button.
export const weekLabel = (week: { start: string; end: string }) =>
  `${shortDay(week.start)} – ${shortDay(week.end)}`;

// The small breakdown under a figure: only the parts that are not zero, «свои 12, замены 1»; nothing when all are zero.
export const partsLine = (parts: [string, number][]) =>
  parts
    .filter(([, n]) => n > 0)
    .map(([label, n]) => `${label} ${n}`)
    .join(", ");

// What is worth saying about a lesson besides its outcome.
export function lessonNotes(
  l: Pick<
    WorkloadLesson,
    "outcome" | "kind" | "confirmed" | "differs" | "absent"
  >,
) {
  const notes: string[] = [];
  if (l.outcome === "taught" && l.kind) notes.push(KINDS[l.kind] ?? l.kind);
  if (l.outcome === "taught" && !l.confirmed)
    notes.push("не подтверждён журналом");
  if (l.differs) notes.push("расходится с расписанием");
  if (l.absent && l.outcome !== "taught") notes.push("учитель отсутствует");
  return notes.join(" · ");
}

export type OutcomeTone = "positive" | "neutral" | "danger" | "muted";
export const outcomeTone = (outcome: string): OutcomeTone =>
  outcome === "taught"
    ? "positive"
    : outcome === "upcoming"
      ? "neutral"
      : outcome === "uncovered" || outcome === "cancelled"
        ? "danger"
        : "muted";

// The chips of the lessons tab: «other» is every outcome that is neither taught nor upcoming, unknown ones included.
export type LessonFilter = "all" | "taught" | "upcoming" | "other";
export const LESSON_FILTERS: [LessonFilter, string][] = [
  ["all", "Все"],
  ["taught", "Проведено"],
  ["upcoming", "Предстоит"],
  ["other", "Не проведено"],
];
export const lessonGroup = (outcome: string): Exclude<LessonFilter, "all"> =>
  outcome === "taught"
    ? "taught"
    : outcome === "upcoming"
      ? "upcoming"
      : "other";

type Span = { start: string; end: string };
type Row = Pick<WorkloadLesson, "date" | "outcome">;
const inSpan = (date: string, week: Span | null) =>
  !week || (date >= week.start && date <= week.end);

export function filterLessons<T extends Row>(
  lessons: T[],
  filter: LessonFilter,
  week: Span | null,
) {
  return lessons.filter(
    (l) =>
      inSpan(l.date, week) &&
      (filter === "all" || lessonGroup(l.outcome) === filter),
  );
}

// What the rows of the lessons tab are narrowed to, for the printed sheet; empty when nothing is: «Неделя 02.11 – 08.11 · Не проведено».
export function lessonSelection(week: Span | null, filter: LessonFilter) {
  const label = LESSON_FILTERS.find(([id]) => id === filter)?.[1] ?? "";
  return [week ? `Неделя ${weekLabel(week)}` : "", filter === "all" ? "" : label]
    .filter(Boolean)
    .join(" · ");
}

// Counts on the chips: the lessons of the selected week (or of the whole period) by group.
export function lessonCounts(lessons: Row[], week: Span | null) {
  const counts: Record<LessonFilter, number> = {
    all: 0,
    taught: 0,
    upcoming: 0,
    other: 0,
  };
  for (const l of lessons) {
    if (!inSpan(l.date, week)) continue;
    counts.all += 1;
    counts[lessonGroup(l.outcome)] += 1;
  }
  return counts;
}

// The weeks of all shown teachers, each once, in order.
export function reportWeeks(teachers: { weeks?: Span[] }[]): Span[] {
  const found = new Map<string, Span>();
  for (const t of teachers)
    for (const w of t.weeks ?? [])
      if (!found.has(w.start))
        found.set(w.start, { start: w.start, end: w.end });
  return [...found.values()].sort((a, b) => a.start.localeCompare(b.start));
}

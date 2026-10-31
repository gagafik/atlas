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

// The part of a range inside the school year; null when none of it is (a preset in the summer break).
export function within(
  range: { start: string; end: string },
  year: { yearStart: string; yearEnd: string },
) {
  const found = clip(range, year);
  return found.start <= found.end ? found : null;
}

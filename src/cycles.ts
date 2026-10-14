import type { State, Workload, TimetableRule } from "./types";
export const patterns = (s: State) =>
  s.cycles?.patterns || [{ id: "weekly", name: "Каждая неделя" }];
export const activeIn = (item: Workload | TimetableRule, id: string) =>
  !item.cycleIds?.length || item.cycleIds.includes(id);
export const cycleHours = (w: Workload, id: string) =>
  w.cycleHours?.[id] ?? w.hours;
export function weekState(s: State, id: string): State {
  return {
    ...s,
    workloads: s.workloads
      .filter((w) => activeIn(w, id))
      .map((w) => ({
        ...w,
        hours: cycleHours(w, id),
        lessonLengths: w.cycleLessonLengths?.[id] ?? w.lessonLengths,
      })),
    placements: s.placements.filter((p) => (p.cycleId || "weekly") === id),
    rules: (s.rules || []).filter((r) => activeIn(r, id)),
  };
}
export function patternOn(s: State, day: string): string | null {
  if (day < s.settings.yearStart || day > s.settings.yearEnd) return null;
  const periods = s.cycles?.periods || [];
  const term = periods.find((p) => p.start <= day && day <= p.end);
  if (periods.length && !term) return null;
  const sequence = term?.patternIds || patterns(s).map((p) => p.id);
  const yearStart = new Date(s.settings.yearStart + "T00:00:00Z");
  const anchor = term
    ? Date.parse(term.anchor + "T00:00:00Z")
    : yearStart.getTime() - ((yearStart.getUTCDay() + 6) % 7) * 86400000;
  const week = Math.floor(
    (Date.parse(day + "T00:00:00Z") - anchor) / (7 * 86400000),
  );
  return sequence[
    ((week % sequence.length) + sequence.length) % sequence.length
  ];
}

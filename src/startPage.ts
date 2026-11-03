// The countdown and the bell strip of the start page. Pure: the caller passes the day and the minute.
import { patternOn } from "./cycles";
import { clockMinutes } from "./journal";
import type { CalendarEvent, State } from "./types";

export interface Bell {
  start: string;
  end: string;
}
// index counts the bells sorted by start. A period lasts from its start up to, but not including, its end.
export type BellState =
  | { phase: "before"; index: 0; minutes: number }
  | { phase: "lesson"; index: number; minutes: number }
  | { phase: "break"; index: number; minutes: number }
  | { phase: "after" };

const byStart = (a: Bell, b: Bell) =>
  clockMinutes(a.start) - clockMinutes(b.start);

/** Where the day stands at `minutes` after midnight: minutes left in the lesson, or to the next bell. */
export function bellState(bells: Bell[], minutes: number): BellState | null {
  const sorted = [...bells].sort(byStart);
  if (!sorted.length) return null;
  const now = sorted.findIndex(
    (bell) =>
      clockMinutes(bell.start) <= minutes && minutes < clockMinutes(bell.end),
  );
  if (now >= 0)
    return {
      phase: "lesson",
      index: now,
      minutes: clockMinutes(sorted[now].end) - minutes,
    };
  const next = sorted.findIndex((bell) => clockMinutes(bell.start) > minutes);
  if (next < 0) return { phase: "after" };
  const left = clockMinutes(sorted[next].start) - minutes;
  return next === 0
    ? { phase: "before", index: 0, minutes: left }
    : { phase: "break", index: next, minutes: left };
}

export const minutesNow = (date: Date) =>
  date.getHours() * 60 + date.getMinutes();

/**
 * The periods of a school day, sorted; none on a weekend, when the whole school is closed, or outside the year.
 * The rules are the server's: «день в расписании» replaces the weekday, and an event closes the school only
 * if it cancels lessons (or is a holiday or winter break) for every class.
 */
export function todayBells(state: State, day: string) {
  if (!patternOn(state, day)) return [];
  const moved = (state.scheduleDays || []).find(
    (d) => d.onDate === day && !d.classIds.length,
  );
  const weekday =
    moved?.day ?? (new Date(day + "T12:00:00Z").getUTCDay() + 6) % 7;
  if (weekday >= state.settings.days.length) return [];
  const closes = (e: CalendarEvent) =>
    (e.effect === "cancel" || e.kind === "holiday" || e.kind === "winter") &&
    (!e.classIds?.length ||
      state.classes.every((c) => e.classIds!.includes(c.id)));
  if (
    (state.events || []).some(
      (e) => e.start <= day && day <= e.end && closes(e),
    )
  )
    return [];
  // The same default profile as the schedule page.
  const profile =
    state.profiles.find((p) => p.id === "secondary") ?? state.profiles[0];
  if (!profile) return [];
  return [...(profile.dayPeriods?.[String(weekday)] ?? profile.periods)].sort(
    byStart,
  );
}

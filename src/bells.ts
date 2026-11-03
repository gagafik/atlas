// Bell periods of a school day and the place of a lesson in them. Pure functions, no React.
import { clockMinutes, type BellProfile, type BellSlot } from "./journal";

/** The bells of a date: the weekday's own list, else the common one. The profile keys are Python weekdays (Monday is "0"). */
export function slotsFor(profile: BellProfile, isoDate: string): BellSlot[] {
  const weekday = (new Date(`${isoDate}T00:00:00Z`).getUTCDay() + 6) % 7;
  return profile.dayPeriods[String(weekday)] ?? profile.periods;
}

export interface Timed {
  start: string;
  end: string;
  subject?: string;
}
export interface SlotRow<T> {
  // The index in the bell list; null for a lesson that fits no bell period.
  slot: number | null;
  label: string;
  start: string;
  end: string;
  items: T[];
}

const overlap = (a: Timed | BellSlot, b: Timed | BellSlot) =>
  Math.min(clockMinutes(a.end), clockMinutes(b.end)) -
  Math.max(clockMinutes(a.start), clockMinutes(b.start));
const byTime = (a: Timed, b: Timed) =>
  a.start.localeCompare(b.start) ||
  (a.subject ?? "").localeCompare(b.subject ?? "", "ru");

/**
 * Puts lessons into bell periods by the largest overlap (a tie goes to the earlier period). Every period stays,
 * empty ones too; lessons that overlap no period get rows of their own, placed by time. Parallel lessons share a row.
 */
export function placeInSlots<T extends Timed>(
  slots: BellSlot[],
  items: T[],
): SlotRow<T>[] {
  const rows: SlotRow<T>[] = slots.map((slot, index) => ({
    slot: index,
    label: slot.label,
    start: slot.start,
    end: slot.end,
    items: [],
  }));
  const extra = new Map<string, SlotRow<T>>();
  for (const item of items) {
    let best = -1,
      most = 0;
    slots.forEach((slot, index) => {
      const shared = overlap(item, slot);
      if (shared > most) {
        most = shared;
        best = index;
      }
    });
    if (best >= 0) {
      rows[best].items.push(item);
      continue;
    }
    const key = `${item.start}-${item.end}`;
    const own = extra.get(key) ?? {
      slot: null,
      label: "",
      start: item.start,
      end: item.end,
      items: [],
    };
    own.items.push(item);
    extra.set(key, own);
  }
  for (const row of [...rows, ...extra.values()]) row.items.sort(byTime);
  return [...rows, ...extra.values()].sort(
    (a, b) => clockMinutes(a.start) - clockMinutes(b.start),
  );
}

/**
 * The bells a day of lessons mostly runs on: the profile most of the lessons name (the earlier one of the school on a tie),
 * else the first profile of the school. Lessons of another profile are placed by their overlap or get rows of their own.
 */
export function dayProfile(
  lessons: { profileId?: string | null }[],
  profiles: BellProfile[] = [],
): BellProfile | undefined {
  const counts = new Map<string, number>();
  for (const lesson of lessons)
    if (lesson.profileId)
      counts.set(lesson.profileId, (counts.get(lesson.profileId) ?? 0) + 1);
  let best: BellProfile | undefined,
    most = 0;
  for (const profile of profiles) {
    const count = counts.get(profile.id) ?? 0;
    if (count > most) {
      best = profile;
      most = count;
    }
  }
  return best ?? profiles[0];
}

/** The bell profile of a pupil's class, else the first one of the school. */
export function profileFor(
  options: {
    students: { id: string; profileId?: string | null }[];
    profiles?: BellProfile[];
  },
  studentId: string,
): BellProfile | undefined {
  const id = options.students.find((s) => s.id === studentId)?.profileId;
  return options.profiles?.find((p) => p.id === id) ?? options.profiles?.[0];
}

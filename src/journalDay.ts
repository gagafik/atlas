// «Классный журнал»: how the lessons of one day are laid out for the staff. Pure functions, no React.
import { placeInSlots } from "./bells";
import {
  dayLessonFill,
  dayLessonState,
  type BellSlot,
  type DayLesson,
  type DayLessonFill,
} from "./journal";

/** The row for lessons that name no class at all, so that nothing a teacher can hold falls out of the administrator's view. */
export const NO_CLASS = "Без класса";
/** Most lessons the school-wide search shows at once. */
export const SEARCH_LIMIT = 50;

/** The school's order of classes: 2А comes before 10А. */
export const naturalOrder = (a: string, b: string) =>
  a.localeCompare(b, "ru", { numeric: true });

/** The classes a lesson belongs to; a lesson of a mixed group belongs to each of them. */
export const classesOf = (l: DayLesson) =>
  l.classes.length ? l.classes : [NO_CLASS];

/** The class an administrator is taken to when he is sent to this lesson. */
export const classOfLesson = (l: DayLesson) =>
  [...classesOf(l)].sort(naturalOrder)[0];

/** The moment the day is read at: the date as YYYY-MM-DD and the time as HH:MM. */
export interface ClockNow {
  date: string;
  time: string;
}
/** A lesson that is over (or on a past day). */
export const lessonEnded = (l: DayLesson, now: ClockNow) =>
  l.lesson.date < now.date ||
  (l.lesson.date === now.date && l.lesson.end <= now.time);

/** What a row shows: the state of the card, except that a lesson nobody could have filled yet is «ahead». */
export type RowFill = DayLessonFill | "upcoming";
export const rowFill = (l: DayLesson, now: ClockNow): RowFill => {
  const fill = dayLessonFill(l);
  return fill === "empty" && !lessonEnded(l, now) ? "upcoming" : fill;
};
export const rowState = (l: DayLesson, now: ClockNow) =>
  rowFill(l, now) === "upcoming" ? "Урок ещё не прошёл" : dayLessonState(l);

export interface FillCount {
  // Lessons that take place: a cancelled lesson is neither filled nor missing and is not counted.
  lessons: number;
  filled: number;
  // Not filled although the lesson is over.
  unfilled: number;
  // Not filled yet, and the lesson has not ended: nothing is missing.
  upcoming: number;
}
/**
 * What the card of a lesson shows as «Не заполнено» is unfilled once the lesson is over; before that it is ahead.
 * Any other record of a held lesson is filled (a draft is the one the family does not see yet; the server's own
 * «контроль заполнения» is stricter about it).
 */
export function countFill(lessons: DayLesson[], now: ClockNow): FillCount {
  const count: FillCount = { lessons: 0, filled: 0, unfilled: 0, upcoming: 0 };
  for (const l of lessons) {
    const fill = rowFill(l, now);
    if (fill === "cancelled") continue;
    count.lessons++;
    if (fill === "empty") count.unfilled++;
    else if (fill === "upcoming") count.upcoming++;
    else count.filled++;
  }
  return count;
}

export interface ClassSummary extends FillCount {
  name: string;
  state: string;
}
export const classState = (c: FillCount) =>
  !c.lessons
    ? "Уроков нет"
    : c.unfilled
      ? `Не заполнено: ${c.unfilled}`
      : c.upcoming
        ? `Уроков впереди: ${c.upcoming}`
        : "Всё заполнено";

/**
 * One row per class: the classes with lessons that day first, then the rest of the school's classes (`known`),
 * each group in natural order. A lesson counts for every class it includes.
 */
export function classSummaries(
  lessons: DayLesson[],
  known: string[],
  now: ClockNow,
): ClassSummary[] {
  const byClass = new Map<string, DayLesson[]>();
  for (const name of known) byClass.set(name, []);
  for (const l of lessons)
    for (const name of classesOf(l)) {
      const own = byClass.get(name);
      if (own) own.push(l);
      else byClass.set(name, [l]);
    }
  const rows = [...byClass.entries()].map(([name, own]) => {
    const count = countFill(own, now);
    return { name, ...count, state: classState(count) };
  });
  const order = (a: ClassSummary, b: ClassSummary) =>
    (a.name === NO_CLASS ? 1 : 0) - (b.name === NO_CLASS ? 1 : 0) ||
    naturalOrder(a.name, b.name);
  return [
    ...rows.filter((r) => r.lessons).sort(order),
    ...rows.filter((r) => !r.lessons).sort(order),
  ];
}

export const lessonsOfClass = (lessons: DayLesson[], name: string) =>
  lessons.filter((l) => classesOf(l).includes(name));

/**
 * The class to switch the administrator to so that the editor of an open lesson sits in a row of its table, or null
 * when the view already shows the lesson (its class, or a search that was typed).
 */
export const classToShow = (l: DayLesson, classFilter: string, query: string) =>
  (classFilter ? !classesOf(l).includes(classFilter) : !query)
    ? classOfLesson(l)
    : null;

/** The text a search looks in. */
const searchText = (l: DayLesson) =>
  `${l.lesson.subject} ${l.lesson.group} ${l.lesson.teachers} ${l.classes.join(" ")}`.toLocaleLowerCase();
export const matchesQuery = (l: DayLesson, query: string) =>
  searchText(l).includes(query.trim().toLocaleLowerCase());

/** Lessons that match the query in time order, at most `limit` of them, and how many more there are. */
export function searchLessons(
  lessons: DayLesson[],
  query: string,
  limit = SEARCH_LIMIT,
): { rows: DayLesson[]; more: number } {
  const found = lessons
    .filter((l) => matchesQuery(l, query))
    .sort(
      (a, b) =>
        a.lesson.start.localeCompare(b.lesson.start) ||
        naturalOrder(classOfLesson(a), classOfLesson(b)) ||
        a.lesson.subject.localeCompare(b.lesson.subject, "ru"),
    );
  return {
    rows: found.slice(0, limit),
    more: Math.max(0, found.length - limit),
  };
}

export interface LessonRow {
  lesson: DayLesson;
  // The bell period of the lesson («1», «2»…); empty for a lesson that fits no bell period.
  number: string;
}
/** The lessons of a day as table rows in time order, each with the number of its bell period. */
export function lessonRows(
  lessons: DayLesson[],
  slots: BellSlot[],
): LessonRow[] {
  return placeInSlots(
    slots,
    lessons.map((l) => ({
      start: l.lesson.start,
      end: l.lesson.end,
      subject: l.lesson.subject,
      day: l,
    })),
  ).flatMap((row) =>
    row.items.map((item) => ({ lesson: item.day, number: row.label })),
  );
}

/** The class column: the classes of the lesson, else the name of its group. */
export const classLabel = (l: DayLesson) =>
  l.classes.join(", ") || l.lesson.group;
/** The group column: empty when the group is the whole class (or the lesson names no class and the group is its only label). */
export const groupLabel = (l: DayLesson) =>
  l.classes.length && !isWholeClasses(l.lesson.group, l.classes)
    ? l.lesson.group
    : "";
/** The group names exactly these classes, in whatever order (the server sorts them as text, people write them in school order). */
const isWholeClasses = (group: string, classes: string[]) => {
  const named = group.split(",").map((c) => c.trim()).sort();
  const own = [...classes].sort();
  return named.length === own.length && named.every((c, i) => c === own[i]);
};
export const lessonAttendance = (l: DayLesson) =>
  l.present === null
    ? "не отмечено"
    : `присутствуют ${l.present}/${l.studentCount}`;

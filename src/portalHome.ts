import { CHANGE_TYPES } from "./substitutions";
import type { ChangeType } from "./types";

// The rules of the portal's start page «Начало»: the date in words and the line of one lesson.
export interface HomeRow {
  start: string;
  end: string;
  subject: string;
  group: string;
  teachers: string;
  rooms: string;
  status: string;
  activity: string;
  changeType?: string | null;
}

export type LessonTone = "planned" | "changed" | "cancelled";

export interface LessonLine {
  time: string;
  title: string;
  details: string;
  state: string;
  tone: LessonTone;
  cancelled: boolean;
  current: boolean;
  // The whole line as one text, for the tooltip of a line cut by an ellipsis.
  full: string;
}

const PLANNED = "По расписанию";

export const stateLabel = (r: HomeRow) =>
  r.status === "cancelled"
    ? "Отменено"
    : r.activity === "duty"
      ? "Дежурство"
      : r.activity === "booking"
        ? "Бронирование"
        : r.changeType
          ? CHANGE_TYPES[r.changeType as ChangeType] || r.changeType
          : r.status === "changed"
            ? "Изменено"
            : r.status === "added"
              ? "Разовый урок"
              : PLANNED;

export function lessonLine(r: HomeRow, current: boolean): LessonLine {
  const state = stateLabel(r),
    cancelled = r.status === "cancelled",
    time = `${r.start}–${r.end}`,
    title = `${r.subject} · ${r.group}`,
    details =
      (r.teachers || "Учитель не назначен") + (r.rooms ? " · " + r.rooms : "");
  return {
    time,
    title,
    details,
    state,
    tone: cancelled
      ? "cancelled"
      : state === PLANNED || r.activity === "duty" || r.activity === "booking"
        ? "planned"
        : "changed",
    cancelled,
    current,
    full: `${time}, ${title}, ${details}, ${state}`,
  };
}

const WEEKDAYS = [
  "воскресенье",
  "понедельник",
  "вторник",
  "среда",
  "четверг",
  "пятница",
  "суббота",
];
const MONTHS = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

// «среда, 18 ноября»
export const dateInWords = (d: Date) =>
  `${WEEKDAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;

// «Замены»: the pure rules of the day view: what a lesson row says and offers, the date in words, the tab keys.
import type { ChangeType } from "./types";
import { CHANGE_TYPES } from "./substitutions";

export type DayTabId =
  "day" | "plan" | "absences" | "changes" | "days" | "report";

export const DAY_TABS: { id: DayTabId; label: string }[] = [
  { id: "day", label: "Занятия на дату" },
  { id: "plan", label: "Замена на день" },
  { id: "absences", label: "Отсутствия учителей" },
  { id: "changes", label: "Все изменения" },
  { id: "days", label: "Дни в расписании" },
  { id: "report", label: "Итоги замен" },
];

// Left and Right wrap around, Home and End jump to the ends; any other key is not ours.
export const nextDayTab = (
  index: number,
  key: string,
  count = DAY_TABS.length,
): number | null =>
  key === "ArrowRight"
    ? (index + 1) % count
    : key === "ArrowLeft"
      ? (index - 1 + count) % count
      : key === "Home"
        ? 0
        : key === "End"
          ? count - 1
          : null;

export interface DayRowFacts {
  activity?: "lesson" | "booking" | "duty";
  commitmentId?: string;
  changeId: string | null;
  status: "scheduled" | "cancelled" | "added" | "changed";
  changeType?: ChangeType | null;
  teachers: string;
  rooms: string;
  start: string;
  end: string;
  subject: string;
  group: string;
}

export type StateTone = "quiet" | "change" | "cancelled";
export type RowAction = "commitment" | "change" | "cancel" | "remove";

const STATUS = {
  scheduled: "По расписанию",
  changed: "Изменение",
  added: "Разовый урок",
  cancelled: "Отменён",
};

// «excluded»: the chosen class or pupil does not attend this lesson.
export function stateText(r: DayRowFacts, excluded: boolean): string {
  return r.activity === "booking"
    ? "Бронирование"
    : r.activity === "duty"
      ? "Дежурство"
      : r.changeType && !excluded && r.status !== "scheduled"
        ? CHANGE_TYPES[r.changeType]
        : STATUS[excluded ? "cancelled" : r.status];
}

export function stateTone(r: DayRowFacts, excluded: boolean): StateTone {
  if (r.activity === "booking" || r.activity === "duty") return "quiet";
  if (excluded || r.status === "cancelled") return "cancelled";
  return r.status === "scheduled" ? "quiet" : "change";
}

// The same rules as before the table: an event record, change/cancel while the lesson stands
// and has no commitment, removing a change when there is one.
export function rowActions(r: DayRowFacts): RowAction[] {
  const out: RowAction[] = [];
  if (r.commitmentId) out.push("commitment");
  if (r.status !== "cancelled" && !r.commitmentId) out.push("change", "cancel");
  if (r.changeId) out.push("remove");
  return out;
}

export const ACTION_TEXT: Record<RowAction, string> = {
  commitment: "Открыть запись события",
  change: "Заменить / перенести",
  cancel: "Отменить урок",
  remove: "Убрать изменение",
};

// With three buttons in a row the visible texts are shortened so the actions column does not squeeze the other cells; the accessible name stays full.
const SHORT_TEXT: Record<RowAction, string> = {
  commitment: "Открыть запись",
  change: "Заменить",
  cancel: "Отменить",
  remove: "Убрать",
};

export interface DayRowView {
  time: string;
  lesson: string;
  teacher: string;
  teacherMissing: boolean;
  room: string;
  roomMissing: boolean;
  state: string;
  tone: StateTone;
  struck: boolean;
  actions: { action: RowAction; text: string; label: string }[];
}

export function dayRowView(r: DayRowFacts, excluded: boolean): DayRowView {
  const lesson = `${r.subject} · ${r.group}`;
  const acts = rowActions(r);
  return {
    time: `${r.start}–${r.end}`,
    lesson,
    teacher: r.teachers || "Учитель не назначен",
    teacherMissing: !r.teachers,
    room: r.rooms || "Кабинет не назначен",
    roomMissing: !r.rooms,
    state: stateText(r, excluded),
    tone: stateTone(r, excluded),
    struck: excluded || r.status === "cancelled",
    actions: acts.map((action) => ({
      action,
      text: acts.length >= 3 ? SHORT_TEXT[action] : ACTION_TEXT[action],
      label: `${ACTION_TEXT[action]}: ${lesson}, ${r.start}`,
    })),
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

// «2026-11-18» -> «среда, 18.11.2026»; anything that is not a real date is returned as it came.
/** The reasons of a change as one text; a reason that only repeats the state («Замена» beside the badge «Замена») is left out. */
export function reasonText(reasons: string[], state: string): string {
  const same = state.trim().toLocaleLowerCase("ru");
  return reasons
    .map((x) => x.trim())
    .filter((x) => x && x.toLocaleLowerCase("ru") !== same)
    .join("; ");
}

export function dateInWords(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = new Date(Date.UTC(y, mo - 1, d));
  if (
    t.getUTCFullYear() !== y ||
    t.getUTCMonth() !== mo - 1 ||
    t.getUTCDate() !== d
  )
    return iso;
  return `${WEEKDAYS[t.getUTCDay()]}, ${m[3]}.${m[2]}.${m[1]}`;
}

export type DayMode = "class" | "teacher" | "room" | "student" | "all";

export interface DayRowRecords {
  originalClassIds: string[];
  originalStudentIds: string[];
  teacherIds: string[];
  originalTeacherIds: string[];
  roomIds: string[];
}

// Does the row belong to the chosen record of the mode? No record, or the whole school, takes every row.
export function rowInRecord(
  r: DayRowRecords,
  mode: DayMode,
  id: string,
): boolean {
  if (!id || mode === "all") return true;
  return mode === "class"
    ? r.originalClassIds.includes(id)
    : mode === "student"
      ? r.originalStudentIds.includes(id)
      : mode === "teacher"
        ? r.teacherIds.includes(id) || r.originalTeacherIds.includes(id)
        : r.roomIds.includes(id);
}

// The record to start with: the first of the list that has a row on the date; when none has, the current one stays.
export function firstRecordWithRows(
  ids: string[],
  rows: DayRowRecords[],
  mode: DayMode,
  current: string,
): string {
  return (
    ids.find((id) => rows.some((r) => rowInRecord(r, mode, id))) ?? current
  );
}

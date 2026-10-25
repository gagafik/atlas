import type { Profile, State } from "./types";
import { csvCell } from "./utils";

export interface DutyProps {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
}
export interface DutyTemplate {
  id: string;
  version: number;
  active: boolean;
  name: string;
  weekday: number;
  profileId: string;
  afterPeriod: number | null;
  startTime: string | null;
  endTime: string | null;
  location: string;
  roomIds: string[];
  buildingId: string | null;
  requiredTeachers: number;
  eligibleTeacherIds: string[];
  teacherIds: string[];
  cycleIds: string[];
  note: string;
}
/** Fields the server accepts back when a template is saved; anything else (id, version, extra keys) is refused. */
export type TemplateFields = Omit<DutyTemplate, "id" | "version" | "active">;
const TEMPLATE_KEYS: (keyof TemplateFields)[] = [
  "name",
  "weekday",
  "profileId",
  "afterPeriod",
  "startTime",
  "endTime",
  "location",
  "roomIds",
  "buildingId",
  "requiredTeachers",
  "eligibleTeacherIds",
  "teacherIds",
  "cycleIds",
  "note",
];
/** The editable part of a listed template, copied by name so a key the server adds later is never posted back. */
export function templateFields(t: DutyTemplate): TemplateFields {
  return Object.fromEntries(
    TEMPLATE_KEYS.map((key) => [key, t[key]]),
  ) as unknown as TemplateFields;
}
/** A teacher may ask again unless a request is waiting; a rejected or withdrawn one does not block. */
export function canAskSwap(item: {
  canSwap: boolean;
  swap: "pending" | "approved" | "rejected" | null;
}) {
  return item.canSwap && item.swap !== "pending";
}
export interface DutySettings {
  version: number;
  enabled: boolean;
  daysBefore: 0 | 1;
  remindAt: string;
  notifyChanges: boolean;
}
export interface RosterItem {
  id: string;
  start: string;
  end: string;
  name: string;
  teachers: string[];
  required: number;
  missing: number;
}
export interface RosterData {
  school: string;
  start: string;
  end: string;
  days: { date: string; weekday: number; name: string; closed: string }[];
  places: { key: string; label: string }[];
  cells: { place: string; date: string; items: RosterItem[] }[];
  teachers: {
    teacherId: string;
    name: string;
    minutes: number;
    items: {
      date: string;
      weekday: string;
      start: string;
      end: string;
      place: string;
    }[];
  }[];
  unfilled: number;
}
export interface FairnessData {
  start: string;
  end: string;
  rows: {
    teacherId: string;
    name: string;
    duties: number;
    minutes: number;
    places: number;
    deviation: number;
    firstDate: string | null;
    lastDate: string | null;
  }[];
  summary: {
    requiredMinutes: number;
    assignedMinutes: number;
    unfilledPlaces: number;
    mean: number;
    max: number;
    min: number;
    people: number;
  };
}
export interface DutyRange {
  id: string;
  label: string;
  start: string;
  end: string;
}

/** Event kinds a generation can skip, labelled like the calendar. */
export const SKIP_KINDS: [string, string][] = [
  ["holiday", "Праздник"],
  ["winter", "Winter Break"],
  ["reading", "Reading Week"],
  ["extra", "Каникулы 1 классов"],
  ["exam", "Экзамены"],
];
/** One id per distinct request body, so a repeated click or retry is recognised by the server. */
export function requestId(
  slot: { current: { key: string; id: string } },
  body: unknown,
) {
  const key = JSON.stringify(body);
  if (slot.current.key !== key) slot.current = { key, id: crypto.randomUUID() };
  return slot.current.id;
}

const DAY = 86400000;
function parse(date: string) {
  return new Date(date + "T12:00:00Z");
}
function text(value: Date) {
  return value.toISOString().slice(0, 10);
}
export function addDays(date: string, days: number) {
  return text(new Date(parse(date).getTime() + days * DAY));
}
/** Monday to Sunday of the week that contains the date. */
export function weekRange(date: string): [string, string] {
  const offset = (parse(date).getUTCDay() + 6) % 7;
  const start = addDays(date, -offset);
  return [start, addDays(start, 6)];
}
/** First to last day of the calendar month that contains the date. */
export function monthRange(date: string): [string, string] {
  const value = parse(date);
  const first = new Date(
    Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), 1, 12),
  );
  const last = new Date(
    Date.UTC(value.getUTCFullYear(), value.getUTCMonth() + 1, 0, 12),
  );
  return [text(first), text(last)];
}
export function shortDate(date: string) {
  const [y, m, d] = date.split("-");
  return `${d}.${m}.${y}`;
}
export function clamp(date: string, low: string, high: string) {
  return date < low ? low : date > high ? high : date;
}
/** Dates the generator can be pointed at: each teaching period, the whole year or a custom range. */
export function generationRanges(s: State): DutyRange[] {
  const year = { start: s.settings.yearStart, end: s.settings.yearEnd };
  return [
    ...(s.cycles?.periods || []).map((p) => ({
      id: p.id,
      label: `${p.name} · ${shortDate(p.start)}–${shortDate(p.end)}`,
      start: clamp(p.start, year.start, year.end),
      end: clamp(p.end, year.start, year.end),
    })),
    { id: "year", label: "Весь учебный год", ...year },
    { id: "custom", label: "Свой период", ...year },
  ];
}
/** The pause after a lesson in the bell profile of that weekday, or null when there is none. */
export function breakAfter(
  profile: Profile | undefined,
  weekday: number,
  index: number,
) {
  const bells = profile?.dayPeriods?.[String(weekday)] || profile?.periods;
  if (!bells || index + 1 >= bells.length) return null;
  const start = bells[index].end,
    end = bells[index + 1].start;
  return end > start
    ? { start, end, label: bells[index].label || String(index + 1) }
    : null;
}
export function templateTime(t: DutyTemplate, profiles: Profile[]) {
  if (t.afterPeriod === null) return `${t.startTime}–${t.endTime}`;
  const pause = breakAfter(
    profiles.find((p) => p.id === t.profileId),
    t.weekday,
    t.afterPeriod,
  );
  return pause
    ? `перемена после ${pause.label} урока (${pause.start}–${pause.end})`
    : `перемена после урока ${t.afterPeriod + 1} (в звонках её больше нет)`;
}
export function templatePlace(t: DutyTemplate, s: State) {
  return (
    t.location ||
    t.roomIds
      .map((id) => s.rooms.find((r) => r.id === id)?.name || id)
      .join(", ")
  );
}
function table(rows: string[][]) {
  return rows.map((r) => r.map(csvCell).join(";")).join("\r\n");
}
/** One row per duty of the printed period; the caller adds the byte order mark. */
export function rosterCsv(data: RosterData) {
  const places = new Map(data.places.map((p) => [p.key, p.label]));
  const days = new Map(data.days.map((d) => [d.date, d.name]));
  const rows = data.cells
    .flatMap((cell) =>
      cell.items.map((item) => ({
        cell,
        item,
        place: places.get(cell.place) || "",
      })),
    )
    .sort((a, b) =>
      `${a.cell.date} ${a.item.start} ${a.place}`.localeCompare(
        `${b.cell.date} ${b.item.start} ${b.place}`,
      ),
    );
  return table([
    [
      "Дата",
      "День",
      "Начало",
      "Конец",
      "Место",
      "Сотрудники",
      "Требуется",
      "Не хватает",
    ],
    ...rows.map(({ cell, item, place }) => [
      cell.date,
      days.get(cell.date) || "",
      item.start,
      item.end,
      place,
      item.teachers.join(", "),
      String(item.required),
      String(item.missing),
    ]),
  ]);
}
export function fairnessCsv(data: FairnessData) {
  return table([
    [
      "Сотрудник",
      "Дежурств",
      "Минут",
      "Отклонение от среднего",
      "Мест",
      "Первое дежурство",
      "Последнее дежурство",
    ],
    ...data.rows.map((r) => [
      r.name,
      String(r.duties),
      String(r.minutes),
      String(r.deviation),
      String(r.places),
      r.firstDate || "",
      r.lastDate || "",
    ]),
  ]);
}
/** Minutes as hours and minutes for the summary line. */
export function minutesText(value: number) {
  const hours = Math.floor(value / 60);
  return hours ? `${hours} ч ${value % 60} мин` : `${value} мин`;
}

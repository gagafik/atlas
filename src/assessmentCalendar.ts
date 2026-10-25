// Calendar dates are ISO strings handled with UTC arithmetic, so a device
// time zone (or Asia/Qyzylorda itself) never shifts a day.
const DAY = 86400000;
const parse = (iso: string) => {
  const [year, month, day] = iso.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
};
const format = (ms: number) => new Date(ms).toISOString().slice(0, 10);

export function addDays(iso: string, count: number) {
  return format(parse(iso) + count * DAY);
}
export function mondayOf(iso: string) {
  return addDays(iso, -((new Date(parse(iso)).getUTCDay() + 6) % 7));
}
export function monthRange(month: string) {
  const [year, number] = month.split("-").map(Number);
  return { start: `${month}-01`, end: format(Date.UTC(year, number, 0)) };
}
// Monday-first weeks that cover the whole month, adjacent-month days included.
export function monthWeeks(month: string) {
  const { start, end } = monthRange(month);
  const weeks: string[][] = [];
  for (let monday = mondayOf(start); monday <= end; monday = addDays(monday, 7))
    weeks.push(Array.from({ length: 7 }, (_, i) => addDays(monday, i)));
  return weeks;
}
export function shiftMonth(month: string, count: number) {
  const [year, number] = month.split("-").map(Number);
  return format(Date.UTC(year, number - 1 + count, 1)).slice(0, 7);
}
export function dayLabel(iso: string) {
  const [year, month, day] = iso.split("-");
  return `${day}.${month}.${year}`;
}
export function shortDay(iso: string) {
  return dayLabel(iso).slice(0, 5);
}
const names = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
export const weekdayNames = names.slice(1).concat(names[0]);
export function weekday(iso: string) {
  return names[new Date(parse(iso)).getUTCDay()];
}
export function loadClass(load: number, max: number | null) {
  return max != null && load > max ? "over" : "";
}
export function clock(start: string, end: string) {
  return `${start}–${end}`;
}
export function monthTitle(month: string) {
  return new Date(parse(`${month}-01`)).toLocaleDateString("ru-RU", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

export type Category = "summative_unit" | "summative_term" | "other";
export type Status = "planned" | "published" | "cancelled";
export type WorkType = {
  id: string;
  title: string;
  short: string;
  category: Category;
  counted: boolean;
  window: boolean;
  active: boolean;
};
export type Pair = {
  groupId: string;
  group: string;
  subjectId: string;
  subject: string;
  classIds: string[];
  classes: string;
};
export type Period = {
  id: string;
  name: string;
  start: string;
  end: string;
  status: string;
};
export type SchoolEvent = {
  name: string;
  start: string;
  end: string;
  effect: "note" | "cancel";
};
export type Options = {
  admin: boolean;
  coordinator: boolean;
  canBook: boolean;
  family: boolean;
  today: string;
  yearStart: string;
  yearEnd: string;
  types: WorkType[];
  categories: Record<Category, string>;
  states: Record<Status, string>;
  maxPerDay: number | null;
  maxPerWeek: number | null;
  windows: { periodId: string; start: string; end: string; period: string }[];
  periods: Period[];
  pairs: Pair[];
  classes: { id: string; name: string }[];
  teachers: { id: string; name: string }[];
  students: { id: string; name: string; className: string }[];
  configVersion: number | null;
  coordinatorIds: string[];
  candidates: { id: string; name: string; role: string }[];
  schoolEvents: SchoolEvent[];
};
// Everybody who can book, coordinate or read gets the menu entry; the administration may
// name an editor as coordinator, so the roles of coordinators are a subset of the menu roles.
export const coordinatorRoles = ["editor", "teacher", "staff"];
export const menuRoles = [
  "admin",
  "editor",
  "teacher",
  "staff",
  "student",
  "parent",
];
// The pair and type a booking form may keep once the options of a workspace are known: a value
// from another workspace, or a type that has been switched off, gives way to the default.
// A move keeps what it was opened with.
export function keepChoices(
  form: { mode: string; pair: string; typeId: string },
  options: Pick<Options, "pairs" | "types">,
) {
  if (form.mode === "move") return { pair: form.pair, typeId: form.typeId };
  const pairs = options.pairs.map((p) => `${p.groupId}|${p.subjectId}`);
  const active = options.types.filter((t) => t.active);
  return {
    pair: pairs.includes(form.pair)
      ? form.pair
      : pairs.length === 1
        ? pairs[0]
        : "",
    typeId: active.some((t) => t.id === form.typeId)
      ? form.typeId
      : active[0]?.id || "",
  };
}

export type Booking = {
  id: string;
  version: number;
  status: Status;
  statusTitle: string;
  day: string;
  start: string;
  end: string;
  lessonId: string;
  typeId: string;
  type: string;
  short: string;
  category: Category;
  topic: string;
  note: string;
  groupId: string;
  group: string;
  subjectId: string;
  subject: string;
  classIds: string[];
  classes: string;
  teachers: string;
  author: string;
  created: string;
  updated: string;
  override: string;
  overrideBy: string;
  cancelReason: string;
  canChange: boolean;
  slotValid: boolean;
  slotMessage: string;
};
export type FamilyItem = {
  id: string;
  day: string;
  start: string;
  end: string;
  subject: string;
  group: string;
  short: string;
  type: string;
  topic: string;
  teachers: string;
  slotValid: boolean;
  slotMessage?: string;
};
export type Slot = {
  lessonId: string;
  start: string;
  end: string;
  status: string;
  teachers: string;
  rooms: string;
  substitute: boolean;
  classes: string;
  booked: { id: string; short: string; topic: string } | null;
};
export type SlotsResult = {
  day: string;
  past: boolean;
  closed: string[];
  outsideYear: boolean;
  slots: Slot[];
  issues: string[];
  canOverride: boolean;
};
export type LoadData = {
  days: string[];
  weeks: string[];
  rows: {
    classId: string;
    name: string;
    cells: Record<
      string,
      {
        load: number;
        items: { id: string; short: string; subject: string; status: Status }[];
      }
    >;
    weeks: Record<string, number>;
  }[];
  maxPerDay: number | null;
  maxPerWeek: number | null;
};
export const actionLabels: Record<string, string> = {
  created: "Назначена",
  published: "Опубликована",
  edited: "Изменена",
  moved: "Перенесена",
  cancelled: "Отменена",
};
export function groupByDay<T extends { day: string }>(items: T[]) {
  const result = new Map<string, T[]>();
  for (const item of items)
    result.set(item.day, [...(result.get(item.day) || []), item]);
  return result;
}
export function eventsOn(events: SchoolEvent[], day: string) {
  return events.filter((e) => e.start <= day && day <= e.end);
}

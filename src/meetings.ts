export type Named = { id: string; name: string };
export type Host = {
  accountId: string;
  teacherId: string | null;
  scope: "teaching" | "classes";
  classIds: string[];
  roomId: string | null;
  location: string;
  name?: string;
  roomName?: string;
  current?: boolean;
  studentIds?: string[];
};
export type Definition = {
  title: string;
  description: string;
  startsAt: string;
  endsAt: string;
  bookingOpensAt: string | null;
  bookingClosesAt: string | null;
  slotMinutes: number;
  gapMinutes: number;
  cancelMinutes: number;
  reminderMinutes: number;
  hosts: Host[];
};
export type Options = {
  canManage: boolean;
  students: Named[];
  hosts: (Named & { role: string; teachers: Named[] })[];
  classes: Named[];
  rooms: Named[];
};
export type MeetingEvent = {
  id: string;
  version: number;
  status: string;
  definition: Definition;
  hosts: Host[];
  registrationOpen: boolean;
  slotCount: number | null;
};
export type EventItem = {
  id: string;
  title: string;
  status: string;
  startsAt: string;
  endsAt: string;
};
export type Booking = {
  id: string;
  version: number;
  eventTitle: string;
  status: string;
  parentName: string;
  studentName: string;
  hostName: string;
  note: string;
  reason: string;
  startsAt: string;
  endsAt: string;
  canCancel: boolean;
  canMark: boolean;
  canReschedule?: boolean;
  rescheduleReason?: string;
  location: string;
  roomName: string;
  parentAccessCurrent?: boolean;
};
export type Slot = {
  id: string;
  version: number;
  hostId: string;
  hostName: string;
  startsAt: string;
  endsAt: string;
  status: string;
  roomName: string;
  location: string;
  booking?: Booking;
  conflict?: string;
  reason?: string;
};
export type Preview = {
  affectedSlots: number;
  bookingsToCancel: number;
  offersToRevoke?: number;
  skippedPast?: number;
  conflictCount: number;
  previewHash: string;
  conflicts: { host: string; startsAt: string; reason: string }[];
};
export const labels: Record<string, string> = {
  draft: "Черновик",
  open: "Запись открыта",
  closed: "Запись закрыта",
  cancelled: "Отменено",
  archived: "В архиве",
  available: "Свободно",
  occupied: "Занято",
  held: "Ожидает ответа семьи",
  blocked: "Недоступно",
  conflict: "Пересечение",
  past: "Время прошло",
  scheduled: "Запланировано",
  attended: "Встреча состоялась",
  no_show: "Не пришли",
  booked: "Запись создана",
  rescheduled: "Встреча перенесена",
};
// Inputs and displays use school time regardless of the parent's device zone.
export function schoolInput(iso: string) {
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Qyzylorda",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (name: string) => parts.find((p) => p.type === name)?.value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}
export function schoolISO(input: string) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(input))
    throw new Error("Укажите дату и время.");
  const result = new Date(input + ":00+05:00").toISOString();
  if (schoolInput(result) !== input) throw new Error("Проверьте дату и время.");
  return result;
}
export const displayTime = (iso: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Asia/Qyzylorda",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
export const clockTime = (iso: string) => schoolInput(iso).slice(11);
// Groups items by school day, keeping the order of the input (the server sorts by start).
export function groupByDay<T extends { startsAt: string }>(
  items: T[],
): [string, T[]][] {
  const groups: [string, T[]][] = [];
  for (const item of items) {
    const day = schoolInput(item.startsAt).slice(0, 10);
    const last = groups[groups.length - 1];
    if (last && last[0] === day) last[1].push(item);
    else groups.push([day, [item]]);
  }
  return groups;
}
export function blankDefinition(): Definition {
  const day = schoolInput(
    new Date(Date.now() + 7 * 86400000).toISOString(),
  ).slice(0, 10);
  return {
    title: "Родительские встречи",
    description: "",
    startsAt: schoolISO(day + "T17:00"),
    endsAt: schoolISO(day + "T19:00"),
    bookingOpensAt: null,
    bookingClosesAt: null,
    slotMinutes: 10,
    gapMinutes: 0,
    cancelMinutes: 60,
    reminderMinutes: 60,
    hosts: [],
  };
}

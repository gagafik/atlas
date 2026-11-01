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
export type Pattern = {
  startDate: string;
  endDate: string;
  weekdays: number[];
  startTime: string;
  endTime: string;
};
export type Definition = {
  kind?: "consultation";
  pattern?: Pattern;
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
  pupilBooking: boolean;
  settingsVersion?: number;
  students: Named[];
  hosts: (Named & { role: string; teachers: Named[] })[];
  classes: Named[];
  rooms: Named[];
  ownTeachers: Named[];
  classMeeting: {
    canCreate: boolean;
    classes: Named[];
    parallels: { grade: string; classIds: string[] }[];
  };
};
export type MeetingEvent = {
  id: string;
  version: number;
  status: string;
  definition: Definition;
  hosts: Host[];
  registrationOpen: boolean;
  slotCount: number | null;
  canManage: boolean;
};
export type EventItem = {
  id: string;
  title: string;
  status: string;
  startsAt: string;
  endsAt: string;
  hostName?: string;
  pattern?: Pattern | null;
};
// A consultation booking of the family's children; the note is only the author's.
export type MineBooking = {
  id: string;
  version: number;
  eventTitle: string;
  hostName: string;
  studentName: string;
  bookedBy: string;
  byPupil: boolean;
  startsAt: string;
  endsAt: string;
  status: string;
  location: string;
  roomName: string;
  reason: string;
  ownBooking: boolean;
  canCancel: boolean;
  note?: string;
};
export type SeriesPreview = {
  slotCount: number;
  days: number;
  skipped: { date: string; startsAt?: string; reason: string; count: number }[];
  skippedCount: number;
  skippedPast: number;
  previewHash: string;
};
export type Section = "events" | "consultations" | "class" | "day";
export type ClassPlan = {
  classes: Named[];
  pupils: number;
  guardians: number;
  withoutGuardian: string[];
  withoutGuardianCount: number;
  conflicts: string[];
  closedDay: string | null;
};
export type ClassItem = {
  id: string;
  version: number;
  status: string;
  title: string;
  description?: string;
  startsAt: string;
  endsAt: string;
  place: string;
  reason: string;
  // Staff: class names, counts and flags. Family: only their own children.
  classNames?: string[];
  pupils?: number;
  marked?: number;
  canEdit?: boolean;
  canCancel?: boolean;
  canMark?: boolean;
  students?: Named[];
};
export type RosterRow = {
  studentId: string;
  name: string;
  classId: string;
  className: string;
  mark: string;
  guardians: string[];
};
export type ClassDetail = ClassItem & {
  description: string;
  roomId: string | null;
  location: string;
  grade: string | null;
  classIds: string[];
  organizerName: string;
  roster: RosterRow[];
  withoutGuardian: string[];
  history: { action: string; created: string; reason?: string }[];
};
export type DayView = {
  date: string;
  hostName: string;
  lessons: {
    start: string;
    end: string;
    subject: string;
    group: string;
    // Rooms come from the timetable as one text, "301, 302".
    rooms: string;
    status: string;
    activity: string;
  }[];
  slots: {
    id: string;
    eventTitle: string;
    kind: "consultation" | "meeting";
    startsAt: string;
    endsAt: string;
    status: string;
    location: string;
    roomName: string;
    booking?: Booking;
    conflict?: string | null;
  }[];
  classMeetings: {
    id: string;
    title: string;
    startsAt: string;
    endsAt: string;
    place: string;
    classNames: string[];
    pupils: number;
  }[];
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
  present: "Пришли",
  absent: "Не пришли",
};
export const weekdayNames = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
export const shortDate = (day: string) => day.split("-").reverse().join(".");
export const patternText = (p: Pattern) =>
  `${p.weekdays.map((d) => weekdayNames[d]).join(", ")} · ${p.startTime}–${p.endTime} · ${shortDate(p.startDate)}–${shortDate(p.endDate)}`;
export const todaySchool = () =>
  schoolInput(new Date().toISOString()).slice(0, 10);
export const addDays = (day: string, n: number) =>
  new Date(Date.parse(day + "T12:00:00Z") + n * 86400000)
    .toISOString()
    .slice(0, 10);
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
export type DayRow = {
  key: string;
  start: string;
  end: string;
  kind: string;
  title: string;
  detail: string;
  place: string;
  warning: string;
};
// One chronological list for the date: timetable lessons, consultation and parent-meeting slots, class meetings.
export function dayRows(view: DayView): DayRow[] {
  const rows: DayRow[] = [
    ...view.lessons.map((l, i) => ({
      key: "lesson" + i,
      start: l.start,
      end: l.end,
      kind: l.activity === "lesson" ? "Урок" : "Занятость",
      title: [l.subject, l.group].filter(Boolean).join(" · "),
      detail: l.status === "cancelled" ? "Отменён" : "",
      place: l.rooms,
      warning: "",
    })),
    ...view.slots.map((s) => ({
      key: "slot" + s.id,
      start: clockTime(s.startsAt),
      end: clockTime(s.endsAt),
      kind: s.kind === "consultation" ? "Консультация" : "Родительская встреча",
      title: s.eventTitle,
      detail: s.booking
        ? `${s.booking.studentName} · ${s.booking.parentName}`
        : labels[s.status] || s.status,
      place: s.roomName || s.location,
      warning: s.conflict || "",
    })),
    ...view.classMeetings.map((m) => ({
      key: "class" + m.id,
      start: clockTime(m.startsAt),
      end: clockTime(m.endsAt),
      kind: "Собрание класса",
      title: m.title,
      detail: `${m.classNames.join(", ")} · приглашено: ${m.pupils}`,
      place: m.place,
      warning: "",
    })),
  ];
  return rows.sort(
    (a, b) => a.start.localeCompare(b.start) || a.end.localeCompare(b.end),
  );
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

/** Hall TV screen: public payload types and pure helpers (no personal data ever reaches this file). */
export interface HallItem {
  kind: string;
  period: string;
  start: string;
  end: string;
  classes: string;
  subject: string;
  previousSubject?: string;
  absent?: string;
  teachers?: string;
  rooms?: string;
  previousRooms?: string;
  movedTo?: string;
  movedFrom?: string;
}
export interface HallDay {
  date: string;
  label: string;
  items: HallItem[];
  more: number;
}
export interface HallPayload {
  school: string;
  screen: string;
  serverTime: string;
  utcOffsetMinutes: number;
  pageSeconds: number;
  refreshSeconds: number;
  days: HallDay[];
  announcements: { title: string; text: string }[];
}
export const KIND_LABEL: Record<string, string> = {
  substitution: "Замена",
  cancelled: "Отмена",
  closed: "Отмена",
  room: "Кабинет",
  subject: "Предмет",
  time: "Время",
  moved_out: "Перенос",
  moved_in: "Перенос",
  added: "Доп. урок",
};
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
const dayMonth = (iso = "") => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
export const dayHeading = (day: HallDay, first: boolean) =>
  first ? "Изменения сегодня" : `${day.label}, ${dayMonth(day.date)}`;

/** The change in words. Names are absent when the screen hides teachers. */
export function describe(item: HallItem): string {
  let text: string;
  switch (item.kind) {
    case "substitution":
      if (!item.absent && !item.teachers) text = "Замена учителя";
      else if (!item.absent) text = `Дополнительный учитель: ${item.teachers}`;
      else if (!item.teachers) text = `${item.absent} не ведёт урок`;
      else text = `Замена: ${item.absent} → ${item.teachers}`;
      break;
    case "cancelled":
      text = "Урок отменён";
      break;
    case "closed":
      text = "Уроки отменены";
      break;
    case "room":
      text = item.previousRooms
        ? `Кабинет ${item.previousRooms} → ${item.rooms || "не назначен"}`
        : item.rooms
          ? `Кабинет ${item.rooms}`
          : "Кабинет изменён";
      break;
    case "subject":
      text = item.previousSubject
        ? `Вместо «${item.previousSubject}»`
        : "Другой предмет";
      break;
    case "time":
      text = `Новое время ${item.start}–${item.end}`;
      break;
    case "moved_out":
      text = `Перенесён на ${dayMonth(item.movedTo)}`;
      break;
    case "moved_in":
      text = `Перенесён с ${dayMonth(item.movedFrom)}`;
      break;
    case "added":
      text = "Дополнительный урок";
      break;
    default:
      text = "Изменение";
  }
  if (item.previousRooms !== undefined && item.kind !== "room" && item.rooms)
    text += ` · каб. ${item.rooms}`;
  return text;
}

/** Rows that fit one page. Sizes are designed for a 40 px root font at 1080 p; scale is the actual root font over 40. */
export const pageSize = (height: number, scale = 1) =>
  Math.max(3, Math.floor((height / scale - 300) / 76));

export function paginate<T>(items: T[], size: number): T[][] {
  const pages: T[][] = [];
  for (let i = 0; i < items.length; i += size)
    pages.push(items.slice(i, i + size));
  return pages;
}

/** Time and date of the school from a server-based instant, without Intl time zones (old TV browsers may lack them). */
export function schoolClock(ms: number, offsetMinutes: number) {
  const d = new Date(ms + offsetMinutes * 60000);
  const two = (n: number) => String(n).padStart(2, "0");
  return {
    time: `${two(d.getUTCHours())}:${two(d.getUTCMinutes())}`,
    date: `${WEEKDAYS[d.getUTCDay()]}, ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`,
  };
}

export const stale = (lastOk: number, now: number) => now - lastOk > 150000;

/** The secret lives only in the URL fragment, so page requests and Referer never carry it. */
export function tokenFromHash(hash: string) {
  let value: string;
  try {
    value = decodeURIComponent(hash.startsWith("#") ? hash.slice(1) : hash);
  } catch {
    return "";
  }
  return /^[A-Za-z0-9_-]{32,100}$/.test(value) ? value : "";
}

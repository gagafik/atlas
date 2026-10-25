import type { Account } from "./auth";

export type Capability = "visitors" | "incidents" | "inventory";
// What the shell hands to every tab: the loaded options and the guarded request helpers.
export type Ctx = {
  user: Account;
  workspace: string;
  options: Options;
  // background: a reload after a filter change; it must not disable the form the person is typing in.
  run: (task: () => Promise<void>, background?: boolean) => Promise<void>;
  mutate: (path: string, body: object, method?: string) => Promise<any>;
  dirty: (value: boolean) => void;
  say: (text: string) => void;
  refresh: () => Promise<void>;
};
export type Assignment = {
  accountId: string;
  name: string;
  capabilities: Capability[];
  current?: boolean;
};
export type Options = {
  admin: boolean;
  capabilities: Capability[];
  today: string;
  now: string;
  familySummaries: boolean;
  family: boolean;
  rooms?: { id: string; name: string }[];
  staff?: { id: string; name: string }[];
  students?: { id: string; name: string; classId: string; className: string }[];
  categories?: string[];
  config?: {
    version: number;
    familySummaries: boolean;
    assignments: Assignment[];
  };
  eligible?: { id: string; name: string; role: string }[];
};
export type VisitorData = {
  name: string;
  purpose: string;
  hostId: string;
  hostText: string;
  documentType: string;
  note: string;
};
export type Visitor = {
  id: string;
  version: number;
  status: string;
  day: string;
  arrived: string;
  departed: string | null;
  badge: string;
  author: string;
  overdue: boolean;
  hostName: string;
  data: VisitorData;
};
export type Notice = {
  studentId: string;
  by: string;
  at: string;
  method: string;
  note: string;
};
export type IncidentData = {
  kind: string;
  severity: string;
  occurredAt: string;
  place: string;
  roomId: string;
  studentIds: string[];
  staffIds: string[];
  otherPersons: string;
  description: string;
  firstAid: string;
  parentNotices: Notice[];
  followUp: string;
  familySummary: string;
};
export type IncidentRow = {
  id: string;
  number: string;
  status: string;
  kind: string;
  severity: string;
  occurredAt: string;
  place: string;
  studentCount: number;
  summaryPublished: boolean;
};
export type Incident = {
  id: string;
  number: string;
  version: number;
  status: string;
  author: string;
  closedAt: string;
  closedBy: string;
  publishedAt: string;
  summaryPublished: boolean;
  canReopen: boolean;
  canPublish: boolean;
  data: IncidentData;
  students: { id: string; name: string; className: string; current: boolean }[];
  staff: { id: string; name: string; current: boolean }[];
  roomName: string;
  roomCurrent: boolean;
};
export type ItemData = {
  number: string;
  title: string;
  category: string;
  roomId: string;
  location: string;
  responsibleId: string;
  condition: string;
  acquiredOn: string | null;
  serial: string;
  note: string;
};
export type ItemRow = {
  id: string;
  version: number;
  status: string;
  number: string;
  title: string;
  category: string;
  roomId: string;
  roomName: string;
  location: string;
  responsibleName: string;
  condition: string;
};
export type Item = {
  id: string;
  version: number;
  status: string;
  number: string;
  roomName: string;
  responsibleName: string;
  roomCurrent: boolean;
  responsibleCurrent: boolean;
  writtenOff: { on: string; reason: string; by: string };
  data: ItemData;
};
export type Place = { roomId: string; roomName: string; location: string };
export type Move = {
  seq: number;
  movedOn: string;
  actor: string;
  reason: string;
  source: Place;
  target: Place;
};
export type HistoryEntry = {
  seq: number;
  actor: string;
  created: string;
  action: string;
  reason: string;
  data: any;
};
export type History = { items: HistoryEntry[]; nextBefore: number | null };
export type ImportPreview = {
  items: (ItemData & {
    line: number;
    roomName: string;
    responsibleName: string;
  })[];
  errors: { line: number; message: string }[];
  warnings: string[];
  previewHash: string;
};

export const documentTypes: Record<string, string> = {
  "": "Не указан",
  passport: "Паспорт",
  id_card: "Удостоверение личности",
  driver_license: "Водительское удостоверение",
  service_id: "Служебное удостоверение",
  other: "Другой документ",
};
export const incidentKinds: Record<string, string> = {
  accident: "Несчастный случай",
  incident: "Происшествие",
};
export const severities: Record<string, string> = {
  minor: "Лёгкое",
  medical: "Нужна медицинская помощь",
  serious: "Тяжёлое",
};
export const noticeMethods: Record<string, string> = {
  phone: "По телефону",
  in_person: "Лично",
  message: "Сообщением",
  other: "Иначе",
};
export const conditions: Record<string, string> = {
  new: "Новое",
  good: "Хорошее",
  fair: "Удовлетворительное",
  poor: "Плохое",
  broken: "Неисправно",
};
export const statuses: Record<string, string> = {
  inside: "В здании",
  left: "Ушёл",
  voided: "Аннулирована",
  open: "Открыта",
  closed: "Закрыта",
  active: "На учёте",
  written_off: "Списан",
};
export const historyActions: Record<string, string> = {
  arrived: "Приход записан",
  departed: "Уход отмечен",
  voided: "Запись аннулирована",
  edited: "Запись исправлена",
  created: "Запись создана",
  close: "Запись закрыта",
  reopen: "Запись открыта повторно",
  publish_summary: "Сводка для семей опубликована",
  withdraw_summary: "Сводка для семей снята",
  moved: "Предмет перемещён",
  written_off: "Предмет списан",
  restored: "Предмет восстановлен",
  imported: "Загружен из файла",
  configured: "Права журналов изменены",
};
export const capabilityNames: Record<Capability, string> = {
  visitors: "Посетители",
  incidents: "Происшествия",
  inventory: "Инвентарь",
};
// Names of the fields shown when a history entry lists what an edit changed.
export const fieldNames: Record<string, string> = {
  name: "Посетитель",
  purpose: "Цель визита",
  hostId: "Сотрудник",
  hostName: "Сотрудник",
  hostText: "К кому",
  badge: "Пропуск",
  documentType: "Тип документа",
  note: "Примечание",
  kind: "Вид",
  severity: "Тяжесть",
  occurredAt: "Время",
  place: "Место",
  roomId: "Кабинет",
  roomName: "Кабинет",
  studentIds: "Ученики",
  staffIds: "Сотрудники",
  otherPersons: "Другие лица",
  description: "Описание",
  firstAid: "Первая помощь",
  parentNotices: "Сообщения родителям",
  followUp: "Дальнейшие меры",
  familySummary: "Сводка для семей",
  number: "Инвентарный номер",
  title: "Наименование",
  category: "Категория",
  location: "Место хранения",
  responsibleId: "Ответственный",
  responsibleName: "Ответственный",
  condition: "Состояние",
  acquiredOn: "Дата поступления",
  serial: "Серийный номер",
};

export const can = (options: Options | null | undefined, cap: Capability) =>
  Boolean(options?.capabilities.includes(cap));

// Builds a download link; empty filters are left out so the server applies its defaults.
export function exportHref(
  path: string,
  workspace: string,
  params: Record<string, string | undefined> = {},
) {
  const query = [["workspace", workspace], ...Object.entries(params)]
    .filter(([, value]) => value !== undefined && value !== "")
    .map(([key, value]) => `${key}=${encodeURIComponent(value as string)}`);
  return `/api/registers/${path}?${query.join("&")}`;
}

// How long a visitor has been in the building: "12 мин", "1 ч 05 мин", "2 сут 3 ч".
export function staySince(arrivedIso: string, nowMs: number) {
  const minutes = Math.max(
    0,
    Math.floor((nowMs - new Date(arrivedIso).getTime()) / 60000),
  );
  const hours = Math.floor(minutes / 60);
  if (hours >= 24) return `${Math.floor(hours / 24)} сут ${hours % 24} ч`;
  if (hours) return `${hours} ч ${String(minutes % 60).padStart(2, "0")} мин`;
  return `${minutes} мин`;
}

// Names the top-level fields that differ between two versions of a record.
export function changedFields(before: any, after: any) {
  if (!before || !after) return [];
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  const names = new Set<string>();
  for (const key of keys)
    if (
      JSON.stringify(before[key]) !== JSON.stringify(after[key]) &&
      fieldNames[key]
    )
      names.add(fieldNames[key]);
  return [...names];
}

// Follows `nextBefore` until a list is complete, so a printout never silently stops at the first page.
export async function allPages<
  T extends { items: any[]; nextBefore: number | null },
>(first: T, more: (before: number) => Promise<T>, maxPages = 100): Promise<T> {
  let all = first;
  for (let n = 0; all.nextBefore && n < maxPages; n++) {
    const page = await more(all.nextBefore);
    all = { ...page, items: [...all.items, ...page.items] };
  }
  if (all.nextBefore)
    throw new Error("Список слишком длинный для печати. Уточните фильтры.");
  return all;
}

export const MAX_UPLOAD = 2_000_000;
export function readBase64(file: File): Promise<string> {
  if (file.size > MAX_UPLOAD)
    return Promise.reject(new Error("Файл больше 2 МБ."));
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл."));
    reader.readAsDataURL(file);
  });
}

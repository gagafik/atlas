export const codes: Record<string, string> = {
  work: "Работа",
  remote: "Удалённо",
  training: "Обучение",
  trip: "Командировка",
  leave: "Отпуск",
  sick: "Больничный",
  absence: "Отсутствие",
  day_off: "Отгул",
};
export const absenceCodes = ["leave", "sick", "absence", "day_off"];
export const statuses: Record<string, string> = {
  draft: "Черновик",
  submitted: "На проверке",
  approved: "Подтверждено",
  returned: "На исправлении",
  void: "Аннулировано",
  rejected: "Отклонено",
  withdrawn: "Отозвано",
  cancelled: "Отменено",
};
export const actions: Record<string, string> = {
  saved: "Сохранено",
  submit: "Отправлено",
  withdraw: "Отозвано",
  approve: "Подтверждено",
  return: "Возвращено",
  reopen: "Открыто для исправлений",
  void: "Аннулировано",
  submitted: "Подана заявка",
  reject: "Отклонено",
  cancel: "Отменено",
  close: "Месяц закрыт",
  changed: "Изменены настройки",
};
export const duration = (minutes: number) =>
  `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`;
export type Segment = { start: string; end: string; code: string };
export type DayDocument = {
  fullDay: string | null;
  segments: Segment[];
  note: string;
  employeeName?: string;
};
export type Day = {
  id: string;
  employeeId: string;
  onDate: string;
  version: number;
  status: string;
  document: DayDocument;
  totals: {
    workMinutes: number;
    absenceMinutes: number;
    fullDay: string | null;
  };
};
export type Leave = {
  id: string;
  employeeId: string;
  employeeName: string;
  version: number;
  status: string;
  startDate: string;
  endDate: string;
  startTime: string | null;
  endTime: string | null;
  code: string;
  note: string;
};
export type Summary = {
  employeeId: string;
  name: string;
  workMinutes: number;
  absenceMinutes: number;
  recordedDays: number;
  fullAbsenceDays: number;
  pendingDays: number;
  approvedLeaveDays: number;
  pendingRequests: number;
};
export type Report = {
  days: Day[];
  requests: Leave[];
  summary: Summary[];
  archives: number[];
  period: { status: string; version: number; last_closed: number | null };
};
export type Options = {
  canManage: boolean;
  userId: string;
  today: string;
  settings: { version: number; selfReport: boolean };
  employees: { id: string; name: string }[];
};
export const blankDay = (): DayDocument => ({
  fullDay: null,
  segments: [{ start: "09:00", end: "17:00", code: "work" }],
  note: "",
});

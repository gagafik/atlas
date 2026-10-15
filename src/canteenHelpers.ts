export const mealNames: Record<string, string> = {
  breakfast: "Завтрак",
  lunch: "Обед",
  snack: "Полдник",
  dinner: "Ужин",
};
export const mealStatuses: Record<string, string> = {
  draft: "Черновик",
  published: "Заказы открыты",
  closed: "Приём заказов закрыт",
  cancelled: "Отменено",
  archived: "В архиве",
  ordered: "Ожидает выдачи",
  served: "Выдано",
  missed: "Не пришёл",
};
export const canteenActions: Record<string, string> = {
  configured: "Настройки столовой",
  menu_created: "Меню создано",
  menu_edited: "Черновик изменён",
  menu_publish: "Меню опубликовано",
  menu_close: "Приём заказов закрыт",
  menu_reopen: "Приём заказов открыт",
  menu_cancel: "Меню отменено",
  menu_archive: "Меню в архиве",
  ordered: "Заказ оформлен",
  cancel: "Заказ отменён",
  serve: "Питание выдано",
  missed: "Отмечена неявка",
  "undo-service": "Отметка выдачи исправлена",
};
export function schoolDay() {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Qyzylorda",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}
export function addDays(day: string, count: number) {
  const value = new Date(day + "T12:00:00Z");
  value.setUTCDate(value.getUTCDate() + count);
  return value.toISOString().slice(0, 10);
}
export function schoolMoment(value: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Asia/Qyzylorda",
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}
export function schoolInput(value: string) {
  const p = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Qyzylorda",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(value));
  const get = (key: string) => p.find((x) => x.type === key)?.value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}
export function schoolISO(value: string) {
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(value))
    throw new Error("Заполните дату и время по часовому поясу школы.");
  return value + ":00+05:00";
}
export type CanteenRun = (
  part: string,
  data: unknown,
  method?: string,
) => Promise<any>;

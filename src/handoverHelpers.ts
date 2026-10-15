export { schoolDay, schoolMoment, addDays } from "./canteenHelpers";

export const handoverStatuses: Record<string, string> = {
  proposed: "На проверке школы",
  approved: "Разрешено школой",
  rejected: "Не одобрено",
  revoked: "Разрешение отозвано",
  requested: "Ожидаем взрослого",
  arrived: "Взрослый прибыл",
  ready: "Ребёнок готов",
  completed: "Ребёнок передан",
  cancelled: "Заявка отменена",
  returned: "Вернулся в школу",
  expired: "Дата прошла — выдача не подтверждена",
};
export const handoverActions: Record<string, string> = {
  configured: "Настройки ухода детей изменены",
  adult_proposed: "Взрослый предложен на проверку",
  adult_approve: "Взрослый одобрен школой",
  adult_reject: "Взрослый не одобрен",
  adult_revoke: "Разрешение взрослого отозвано",
  requested: "Заявка оформлена",
  arrive: "Взрослый прибыл",
  ready: "Ребёнок готов",
  cancel: "Заявка отменена",
  completed: "Ребёнок передан взрослому",
  returned: "Ребёнок вернулся в школу",
  note: "Добавлено примечание школы",
};
export type HandoverRun = (
  part: string,
  body: unknown,
  method?: string,
) => Promise<any>;
export const pendingPickup = (row: any) =>
  ["requested", "arrived", "ready"].includes(row.status);

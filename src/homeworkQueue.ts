export interface QueueFilters {
  start: string;
  end: string;
  filter: string;
  history: boolean;
  search: string;
}
// Without dates the server opens pupils and parents on its default window, so empty fields are never sent.
export function queueParams(
  applied: QueueFilters,
  cursor: string,
): URLSearchParams {
  const params = new URLSearchParams({
    history: String(applied.history),
    search: applied.search,
  });
  for (const name of ["start", "end", "filter"] as const)
    if (applied[name]) params.set(name, applied[name]);
  if (cursor) params.set("cursor", cursor);
  return params;
}

export interface QueueItem {
  dueDate: string | null;
  lessonDate: string;
  status: string;
  assignmentChanged: boolean;
  submission: "online" | "offline";
}
// The date the server orders by: the effective deadline, else the day of the lesson.
export const isPast = (item: QueueItem, today: string) =>
  (item.dueDate || item.lessonDate) < today;
export interface QueueSection<T> {
  key: "past" | "next" | "all";
  items: T[];
}
// The default window shows past deadlines first and the next two weeks after them; explicit dates stay one list.
export function queueSections<T extends QueueItem>(
  items: T[],
  today: string,
  window: "upcoming" | "range",
): QueueSection<T>[] {
  if (window === "range") return [{ key: "all", items }];
  const past = items.filter((item) => isPast(item, today));
  const next = items.filter((item) => !isPast(item, today));
  const result: QueueSection<T>[] = [];
  if (past.length) result.push({ key: "past", items: past });
  if (next.length) result.push({ key: "next", items: next });
  return result;
}
// Work without online submission is shown to the teacher in the lesson: no word about sending it.
export function submissionTag(
  item: QueueItem,
  today: string,
  labels: Record<string, string>,
): string {
  if (item.submission === "offline")
    return item.status === "not_submitted" || item.assignmentChanged
      ? isPast(item, today)
        ? "Выполнялось на уроке"
        : "Показать на уроке"
      : labels[item.status];
  return item.assignmentChanged
    ? "Условия изменены — нужна новая отправка"
    : labels[item.status];
}

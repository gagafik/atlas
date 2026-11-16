export interface QueueFilters {
  start: string;
  end: string;
  filter: string;
  history: boolean;
  search: string;
}
// Without dates the server opens pupils and parents on its default window, so empty fields are never sent.
// `student` is the child a family portal looks at: the server narrows the queue to them before it pages.
export function queueParams(
  applied: QueueFilters,
  cursor: string,
  student?: string,
): URLSearchParams {
  const params = new URLSearchParams({
    history: String(applied.history),
    search: applied.search,
  });
  for (const name of ["start", "end", "filter"] as const)
    if (applied[name]) params.set(name, applied[name]);
  if (cursor) params.set("cursor", cursor);
  if (student) params.set("studentId", student);
  return params;
}

export interface QueueItem {
  dueDate: string | null;
  lessonDate: string;
  status: string;
  assignmentChanged: boolean;
  submission: "online" | "offline";
}
// One row of the answer of GET journal/homework-queue: one pupil's work on one task.
export interface QueueRow extends QueueItem {
  lessonKey: string;
  studentId: string;
  student: string;
  subject: string;
  group: string;
  preview: string;
  baseDueDate: string | null;
  extended: boolean;
  submittedAt: string | null;
  late: boolean;
  overdue: boolean;
  deadlineClosed: boolean;
  readOnly: boolean;
  canReview: boolean;
  canSubmit: boolean;
}
export interface QueueResult {
  items: QueueRow[];
  nextCursor: string | null;
  staff: boolean;
  // The filter the server applied: staff open on "submitted", the family on "todo".
  filter: string;
  // "upcoming" is the default window of pupils and parents; explicit dates give "range".
  window: "upcoming" | "range";
  start: string;
  end: string;
  yearStart: string;
  yearEnd: string;
  schoolToday: string;
}
export type QueueMeta = Omit<QueueResult, "items" | "nextCursor">;
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

export type QueueFilter =
  "all" | "todo" | "submitted" | "returned" | "accepted" | "overdue";
// The status tabs of the toolbar; the first one is the filter the server opens that role on.
export function queueTabs(
  staff: boolean,
): { filter: QueueFilter; label: string }[] {
  return staff
    ? [
        { filter: "submitted", label: "На проверке" },
        { filter: "returned", label: "На доработке" },
        { filter: "accepted", label: "Принято" },
        { filter: "todo", label: "Не сдано" },
        { filter: "overdue", label: "Просрочено" },
        { filter: "all", label: "Все" },
      ]
    : [
        { filter: "todo", label: "Нужно сдать" },
        { filter: "submitted", label: "На проверке" },
        { filter: "returned", label: "На доработке" },
        { filter: "accepted", label: "Принято" },
        { filter: "overdue", label: "Просрочено" },
        { filter: "all", label: "Все" },
      ];
}
// ISO date as day.month.year, and as day.month.
export const dateLabel = (iso: string) => iso.split("-").reverse().join(".");
export const shortDate = (iso: string) =>
  iso.split("-").slice(1).reverse().join(".");
// What the period button says: the default window, the whole school year, or the two dates.
export function periodLabel(
  meta: Pick<QueueMeta, "window" | "start" | "end" | "yearStart" | "yearEnd">,
): string {
  if (meta.window === "upcoming") return "ближайшие";
  if (meta.start === meta.yearStart && meta.end === meta.yearEnd)
    return "учебный год";
  return `${dateLabel(meta.start)}–${dateLabel(meta.end)}`;
}
// The server sorts so that the pupils of one task come together: only neighbours with one key are one group.
export function taskGroups<T extends { lessonKey: string }>(
  items: T[],
): { lessonKey: string; items: T[] }[] {
  const groups: { lessonKey: string; items: T[] }[] = [];
  for (const item of items) {
    const last = groups[groups.length - 1];
    if (last && last.lessonKey === item.lessonKey) last.items.push(item);
    else groups.push({ lessonKey: item.lessonKey, items: [item] });
  }
  return groups;
}
export type ChipKey = "offline" | "overdue" | "closed" | "late" | "readOnly";
// The small marks after the main state, always in this order.
export function itemChips(
  item: Pick<
    QueueRow,
    "submission" | "overdue" | "deadlineClosed" | "late" | "readOnly"
  >,
): ChipKey[] {
  const chips: ChipKey[] = [];
  if (item.submission === "offline") chips.push("offline");
  if (item.overdue) chips.push("overdue");
  if (item.deadlineClosed) chips.push("closed");
  if (item.late) chips.push("late");
  if (item.readOnly) chips.push("readOnly");
  return chips;
}
// What the row's button does: the teacher reviews, the pupil does the task, work without online submission is read as a task.
export function itemAction(
  item: Pick<QueueRow, "canReview" | "canSubmit" | "submission">,
): "review" | "submit" | "task" | "work" {
  if (item.canReview) return "review";
  if (item.canSubmit) return "submit";
  return item.submission === "offline" ? "task" : "work";
}
// A parent without a chosen child sees several pupils on one page and needs a column for them.
export const manyPupils = (items: { studentId: string }[]) =>
  new Set(items.map((item) => item.studentId)).size > 1;

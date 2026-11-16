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
// A pupil with an individual deadline is sorted by that date, so a task can come back later as another group:
// `part` counts the groups of the same task before this one (0 for the first).
export function taskGroups<T extends { lessonKey: string }>(
  items: T[],
): { lessonKey: string; part: number; items: T[] }[] {
  const groups: { lessonKey: string; part: number; items: T[] }[] = [];
  const seen = new Map<string, number>();
  for (const item of items) {
    const last = groups[groups.length - 1];
    if (last && last.lessonKey === item.lessonKey) last.items.push(item);
    else {
      const part = seen.get(item.lessonKey) ?? 0;
      seen.set(item.lessonKey, part + 1);
      groups.push({ lessonKey: item.lessonKey, part, items: [item] });
    }
  }
  return groups;
}
// The deadline a group of a task sits at: the shared one, or the pupils' own date when all of them have an extension.
export function groupDeadline(
  items: Pick<QueueRow, "dueDate" | "baseDueDate" | "extended">[],
): { date: string | null; individual: boolean } {
  if (!items.length || items.some((item) => !item.extended))
    return { date: items[0]?.baseDueDate ?? null, individual: false };
  return { date: items[0].dueDate, individual: true };
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
// The name of an open button: one pupil's work, or the whole task (no pupil) of the staff list.
export const workKey = (lessonKey: string, studentId?: string) =>
  lessonKey + ":" + (studentId ?? "*");
// The name of the button of one group of a task: a task that comes back as a second group gets a name of its own.
export const wholeTaskKey = (lessonKey: string, part = 0) =>
  workKey(lessonKey) + (part ? "#" + part : "");
// The moment a work was sent, in the school's time zone.
export const sentLabel = (iso: string) =>
  new Date(iso).toLocaleString("ru-RU", { timeZone: "Asia/Qyzylorda" });

// What is committed to the queue: the tabs, the search and the period.
export type QueueFields = QueueFilters;
// A change of one condition applies everything that is on the screen, the typed search included.
export const nextConditions = (
  fields: QueueFields,
  typedSearch: string,
  patch: Partial<QueueFields>,
): QueueFields => ({ ...fields, search: typedSearch, ...patch });
// The fields after the first answer: the tab the server chose; the dates only when the server used explicit ones.
export const fieldsFromAnswer = (
  fields: QueueFields,
  answer: Pick<QueueResult, "window" | "start" | "end" | "filter">,
): QueueFields => ({
  ...fields,
  ...(answer.window === "range"
    ? { start: answer.start, end: answer.end }
    : {}),
  filter: answer.filter,
});
// The three ways to set the period from its panel.
export function periodPatch(
  kind: "dates" | "year" | "default",
  draft: Pick<QueueFields, "start" | "end" | "history">,
  meta: Pick<QueueMeta, "yearStart" | "yearEnd"> | null,
): Partial<QueueFields> {
  if (kind === "year" && meta)
    return {
      start: meta.yearStart,
      end: meta.yearEnd,
      history: draft.history,
    };
  if (kind === "default") return { start: "", end: "", history: draft.history };
  return draft;
}
// The label of the period button. When the server refused the dates, the button says which dates were asked for.
export function periodButtonLabel(
  meta: QueueMeta | null,
  asked: Pick<QueueFields, "start" | "end">,
  refused: boolean,
): string {
  if (refused && (asked.start || asked.end))
    return `${asked.start ? dateLabel(asked.start) : "…"}–${asked.end ? dateLabel(asked.end) : "…"}`;
  return meta ? periodLabel(meta) : "";
}
// What the list on the screen stands for: another key means another list, the same key is the same page read again.
export const listKey = (
  workspace: string,
  child: string | undefined,
  applied: QueueFilters,
  cursor: string,
) => JSON.stringify([workspace, child ?? "", applied, cursor]);
// A work with unsaved changes asks before it is left; one that is being sent cannot be left at all.
export function mayLeave(
  activity: { dirty: boolean; busy: boolean },
  confirmLeave: () => boolean,
): boolean {
  if (activity.busy) return false;
  return !activity.dirty || confirmLeave();
}

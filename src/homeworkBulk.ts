/** Pure helpers of the teacher's homework tools: bulk acceptance, the next lesson's date and publishing to other classes. */
export interface RosterStudent {
  studentId: string;
  name: string;
  status: string;
  late: boolean;
  assignmentChanged: boolean;
  version: number;
}
export interface BulkResult {
  accepted: string[];
  skipped: { studentId: string; name: string; reason: string }[];
}

export function rosterCounts(students: RosterStudent[]) {
  const count = (status: string) =>
    students.filter((s) => s.status === status).length;
  return {
    submitted: count("submitted"),
    accepted: count("accepted"),
    returned: count("returned"),
    notSubmitted: count("not_submitted"),
  };
}
/** Works that can be accepted without a look at the conditions: sent, and sent to the current ones. */
export function acceptable(students: RosterStudent[]) {
  return students.filter(
    (s) => s.status === "submitted" && !s.assignmentChanged,
  );
}
/** The request items for the ticked works, in roster order; a tick that went stale after a reload is dropped. */
export function bulkItems(students: RosterStudent[], selected: Set<string>) {
  return acceptable(students)
    .filter((s) => selected.has(s.studentId))
    .map((s) => ({ studentId: s.studentId, version: s.version }));
}
export function bulkSummary(result: BulkResult) {
  const done = `Принято работ: ${result.accepted.length}.`;
  if (!result.skipped.length) return done;
  // Reasons are sentences; the list reads better without their full stops.
  return `${done} Не приняты: ${result.skipped.map((s) => `${s.name} — ${s.reason.replace(/\.$/, "")}`).join("; ")}.`;
}

/** A reply belongs to the lesson that was open when the request went out: it is applied, or its error shown, only while that still holds. */
export async function whileCurrent<T>(
  request: () => Promise<T>,
  current: () => boolean,
  apply: (reply: T) => void,
  fail: (error: Error) => void,
) {
  let reply: T;
  try {
    reply = await request();
  } catch (e) {
    if (current()) fail(e as Error);
    return;
  }
  if (current()) apply(reply);
}

export interface NextLesson {
  date: string | null;
  start?: string | null;
  searchedUntil: string;
}
export function nextLessonText(next: NextLesson) {
  if (!next.date)
    return `Следующий урок этого класса не найден до ${next.searchedUntil}. Укажите срок вручную.`;
  return `Следующий урок: ${next.date}${next.start ? `, ${next.start}` : ""}. Срок можно изменить.`;
}

export interface ParallelTarget {
  groupId: string;
  group: string;
  classes: string[];
  parallel: boolean;
  lessonKey: string | null;
  day: string | null;
  start: string | null;
  end: string | null;
  teachers: string;
  version: number;
  nextLessonDate: string | null;
  available: boolean;
  reason: string;
}
export interface ParallelChoice {
  lessonKey: string;
  day: string;
  version: number;
  dueDate: string | null;
}
/** Classes of the same grade first, then by name in Russian order. */
export function orderTargets(targets: ParallelTarget[]) {
  return [...targets].sort(
    (a, b) =>
      Number(b.parallel) - Number(a.parallel) ||
      a.group.localeCompare(b.group, "ru"),
  );
}
/** «same»: the date of the lesson the task was given in; «next»: the class's own next lesson. */
export function targetDue(
  mode: "same" | "next",
  sourceDue: string | null,
  target: ParallelTarget,
) {
  return mode === "same" ? sourceDue : target.nextLessonDate;
}
/** «в 1 классе», «в 2 классах»: the count with the right case of the noun. */
export function inClasses(count: number) {
  const one = count % 10 === 1 && count % 100 !== 11;
  return `${count} ${one ? "классе" : "классах"}`;
}
export function parallelBody(
  source: { lessonKey: string; day: string; version: number },
  revision: number,
  chosen: ParallelChoice[],
) {
  return {
    lessonKey: source.lessonKey,
    day: source.day,
    revision,
    sourceVersion: source.version,
    targets: chosen.map((c) => ({
      lessonKey: c.lessonKey,
      day: c.day,
      version: c.version,
      dueDate: c.dueDate,
    })),
  };
}

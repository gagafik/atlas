// Transfer of a course test's results into a lesson of the course: the shapes the server answers with, and the pure rules the panel applies.
import type { Attempt } from "./quiz";

export type Outcome = "write" | "update" | "conflict" | "same" | "skip";
export interface TransferCounts {
  written: number;
  updated: number;
  overwritten: number;
  same: number;
  kept: number;
  skipped: number;
}
export interface TransferMark {
  studentId: string;
  lessonKey: string;
  day: string;
  assessmentId: string;
  work: string;
  value: number;
  sourceScore: number;
  actor: string;
  created: string;
  stale: boolean;
}
export interface TransferRun {
  created: string;
  actor: string;
  lessonKey: string;
  day: string;
  work: string;
  publish: boolean;
  counts: TransferCounts;
}
export interface TransferSummary {
  canTransfer: boolean;
  quizMax: number;
  defaultCategory: string;
  defaultDay: string;
  defaultLessonKey: string;
  marks: TransferMark[];
  runs: TransferRun[];
}
export interface TransferWork {
  id: string;
  name: string;
  kind: "points" | "label";
  maxScore: number;
  weight: number;
  category: string;
}
export interface TransferLesson {
  lessonKey: string;
  day: string;
  start: string;
  end: string;
  version: number;
  editable: boolean;
  reason: string;
  rosterFrozen: boolean;
  withdrawn: boolean;
  hasPublished: boolean;
  hasUnpublished: boolean;
  works: TransferWork[];
}
export interface Mark {
  score: number | null;
  label: string;
}
export interface PreviewRow {
  studentId: string;
  name: string;
  enrolled: boolean;
  attemptId: string | null;
  attemptNumber: number | null;
  score: number | null;
  maxScore: number | null;
  value: number | null;
  scaled: boolean;
  draft: Mark | null;
  published: Mark | null;
  outcome: Outcome;
  reason: string;
}
export interface Preview {
  allowed: boolean;
  reason: string;
  work: (Omit<TransferWork, "kind"> & { exists: boolean }) | null;
  workExists: boolean;
  rows: PreviewRow[];
  counts: Partial<Record<Outcome, number>>;
  token: string;
  otherPending: boolean;
  hasPublished: boolean;
}
export interface TransferResult {
  status: "saved" | "unchanged";
  lessonKey: string;
  day: string;
  assessmentId: string;
  version: number;
  publishedVersion: number | null;
  published: boolean;
  counts: TransferCounts;
}

/** What the teacher picks: a lesson, an existing work or a new one, which attempt counts, and whether families see it at once. */
export interface TransferForm {
  day: string;
  lessonKey: string;
  // "" until chosen, NEW_WORK for a work made by the transfer, else the id of a work of the lesson.
  workId: string;
  name: string;
  category: string;
  weight: number;
  maxScore: number;
  pick: "last" | "best";
  publish: boolean;
}
export const NEW_WORK = "new";

export const outcomeLabels: Record<Outcome, string> = {
  write: "Будет записано",
  update: "Обновится прежний перенос",
  conflict: "Другая оценка учителя",
  same: "Уже совпадает",
  skip: "Пропущен",
};

const countLabels: [keyof TransferCounts, string][] = [
  ["written", "записано"],
  ["updated", "обновлено"],
  ["overwritten", "заменено"],
  ["same", "уже совпадало"],
  ["kept", "оставлено без замены"],
  ["skipped", "пропущено"],
];
export function countsLine(counts: TransferCounts): string {
  const parts = countLabels
    .filter(([key]) => counts[key])
    .map(([key, label]) => `${label}: ${counts[key]}`);
  return parts.length ? parts.join(" · ") : "изменений нет";
}

/** A lesson of the select: one that cannot take marks stays listed but cannot be chosen. */
export function lessonOption(lesson: TransferLesson) {
  return {
    value: lesson.lessonKey,
    disabled: !lesson.editable,
    label: `${lesson.start}–${lesson.end}${lesson.editable ? "" : " · недоступен"}${lesson.withdrawn ? " · скрыт от семьи" : ""}`,
  };
}

/** The lesson the form holds once a day's lessons are loaded: the chosen one while it takes marks, else the first that does, else the first (the panel then says that no lesson of the day takes marks). */
export function pickLesson(
  lessons: TransferLesson[],
  kept: string,
): TransferLesson | undefined {
  return (
    lessons.find((l) => l.editable && l.lessonKey === kept) ??
    lessons.find((l) => l.editable) ??
    lessons.find((l) => l.lessonKey === kept) ??
    lessons[0]
  );
}

/** Why each disabled lesson is closed: a disabled option cannot be opened to show it. */
export function closedLessons(lessons: TransferLesson[]) {
  return lessons
    .filter((l) => !l.editable)
    .map((l) => ({
      key: l.lessonKey,
      time: `${l.start}–${l.end}`,
      reason: l.reason,
    }));
}

/** Whether the form names everything the server needs to preview. */
export function formComplete(form: TransferForm): boolean {
  if (!form.lessonKey || !form.day || !form.workId) return false;
  if (form.workId !== NEW_WORK) return true;
  return !!form.name.trim() && form.maxScore > 0 && form.weight >= 0;
}

/** The target of a preview: exactly one of an existing work and a new one. */
export function targetBody(form: TransferForm, revision: number) {
  const work =
    form.workId === NEW_WORK
      ? {
          newWork: {
            name: form.name.trim(),
            category: form.category,
            weight: form.weight,
            maxScore: form.maxScore,
          },
        }
      : { assessmentId: form.workId };
  return {
    lessonKey: form.lessonKey,
    day: form.day,
    revision,
    pick: form.pick,
    publish: form.publish,
    ...work,
  };
}

/** The pupils whose different mark the teacher chose to replace; a choice for a row that is no longer a conflict is dropped. */
export function overwriteIds(
  rows: PreviewRow[],
  chosen: Iterable<string>,
): string[] {
  const picked = new Set(chosen);
  return rows
    .filter((r) => r.outcome === "conflict" && picked.has(r.studentId))
    .map((r) => r.studentId);
}

export function transferBody(
  preview: Preview,
  form: TransferForm,
  requestId: string,
  chosen: Iterable<string>,
  revision: number,
) {
  return {
    ...targetBody(form, revision),
    requestId,
    token: preview.token,
    overwrite: overwriteIds(preview.rows, chosen),
  };
}

/** Whether the final button does anything: marks to write, replacements chosen, or a publication of what earlier transfers left as drafts. */
export function canSend(
  preview: Preview | null,
  publish: boolean,
  chosen: Iterable<string>,
): boolean {
  if (!preview || !preview.allowed || !preview.token) return false;
  const changes =
    (preview.counts.write ?? 0) +
    (preview.counts.update ?? 0) +
    overwriteIds(preview.rows, chosen).length;
  return changes > 0 || (publish && preview.otherPending);
}

/** The score of the newest reviewed attempt, else null. */
export function reviewedScore(attempts: Attempt[]): Attempt | null {
  return [...attempts].reverse().find((a) => a.status === "reviewed") ?? null;
}

/** The mark kept by a preview row, as the pupil's journal holds it now. */
export function markText(mark: Mark | null): string {
  if (!mark) return "—";
  return mark.score !== null ? String(mark.score) : mark.label || "—";
}

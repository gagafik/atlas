import { codeName } from "./absenceForm";

// Pure rules of the absence notes screen: labels, tones and the choice of notes for a shared decision.

export type NoteFile = { id: string; filename: string; size: number };
export type AbsenceNote = {
  id: string;
  studentId: string;
  studentName: string;
  version: number;
  status: string;
  start: string;
  end: string;
  created: string;
  reason?: string;
  files?: NoteFile[];
  canReview?: boolean;
  canWithdraw?: boolean;
  periodLocks?: string[];
  origin?: string;
  codeId?: string | null;
  subjectId?: string | null;
  mine?: boolean;
  authorName?: string;
  summary?: boolean;
};
export type ClassSetting = {
  id: string;
  name: string;
  classTeacher: string | null;
  enabled: boolean;
  version: number;
  updated: string | null;
  actor: string | null;
  canChange: boolean;
};
export type NoteViewer = { parent: boolean; reviewer: boolean };
export type Tone = "neutral" | "positive" | "danger" | "muted";

/** How many notes one shared decision takes. */
export const BULK_LIMIT = 50;

export const ACTIONS: Record<string, string> = {
  approve: "Принять",
  reject: "Отклонить",
  revoke: "Отменить принятое решение",
};
export const STATUS_LABELS: Record<string, string> = {
  pending: "На рассмотрении",
  approved: "Принято",
  rejected: "Отклонено",
  withdrawn: "Отозвано родителем",
  revoked: "Решение отменено школой",
  submitted: "Отправлено",
  recorded: "Внесено школой",
};

export const statusLabel = (status: string) => STATUS_LABELS[status] ?? status;

// The colour is never the only sign: the badge always carries the status text.
export const statusTone = (status: string): Tone =>
  status === "approved"
    ? "positive"
    : status === "rejected" || status === "revoked"
      ? "danger"
      : status === "withdrawn"
        ? "muted"
        : "neutral";

// "2026-10-19T08:00" in the school's own time; no Date objects, so no time zone can move the day.
const split = (value: string) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(value);
  return m
    ? {
        date: `${m[3]}.${m[2]}.${m[1]}`,
        day: `${m[1]}-${m[2]}-${m[3]}`,
        time: m[4] ?? "",
      }
    : null;
};

export const periodLabel = (start: string, end: string) => {
  const a = split(start),
    b = split(end);
  if (!a || !b) return `${start.replace("T", " ")} – ${end.replace("T", " ")}`;
  const whole = a.time === "00:00" && b.time === "23:59";
  if (a.day === b.day) return whole ? a.date : `${a.date} ${a.time}–${b.time}`;
  return whole
    ? `${a.date} – ${b.date}`
    : `${a.date} ${a.time} – ${b.date} ${b.time}`;
};

export const subjectName = (
  subjects: { id: string; name: string }[],
  id?: string | null,
) => (id ? (subjects.find((s) => s.id === id)?.name ?? id) : "Все");

// Who filed a note: the school, the parent (maybe another guardian), or the family as the reviewer sees it.
export const filedBy = (
  note: { origin?: string; mine?: boolean },
  viewer: NoteViewer,
) =>
  note.origin === "school"
    ? "Школа"
    : viewer.parent
      ? (note.mine ?? true)
        ? "Вы"
        : "Другой представитель"
      : "Семья";

// A note is taken into a shared decision only when that decision applies to its status.
export const bulkEligible = (status: string, action: string) =>
  status === (action === "revoke" ? "approved" : "pending");

// The first notes of the loaded list that the action applies to, at most the limit.
export const pickEligible = (
  notes: { id: string; status: string }[],
  action: string,
  limit = BULK_LIMIT,
) =>
  notes
    .filter((n) => bulkEligible(n.status, action))
    .slice(0, limit)
    .map((n) => n.id);

// The header checkbox is checked when everything that could be picked is picked.
export const allPicked = (
  notes: { id: string; status: string }[],
  picked: string[],
  action: string,
) => {
  const ids = pickEligible(notes, action);
  return ids.length > 0 && ids.every((id) => picked.includes(id));
};

/** The note-level line under the heading: period, type, subject, who filed it and the author when known. */
export const factsLine = (
  note: AbsenceNote,
  names: {
    codes: { id: string; name: string }[];
    subjects: { id: string; name: string }[];
    filedBy: string;
  },
) =>
  [
    periodLabel(note.start, note.end),
    `Тип: ${codeName(names.codes, note.codeId)}`,
    `Предмет: ${subjectName(names.subjects, note.subjectId)}`,
    `Кем подано: ${names.filedBy}`,
    note.authorName ? `Автор: ${note.authorName}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

// When the switch of a class last changed and by whom; empty when it never did.
export const changedLabel = (c: Pick<ClassSetting, "updated" | "actor">) =>
  c.updated
    ? `${new Date(c.updated).toLocaleString("ru-RU")}${c.actor ? `, ${c.actor}` : ""}`
    : "";

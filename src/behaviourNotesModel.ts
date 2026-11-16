// Pure rules of the «Официальные письма» dialog: the types of its records and the small decisions the list and the form make.

export type Kind = "praise" | "remark";
export type Level = "teacher" | "class_teacher" | "director";
export type Category = {
  id: string;
  kind: Kind;
  title: string;
  active: boolean;
  level: Level;
  approval: boolean;
  quick: boolean;
  letter: boolean;
  // Printed in the pupil's report card by default.
  report: boolean;
};
export type Student = {
  id: string;
  name: string;
  classId: string;
  className: string;
};
export type Options = {
  admin: boolean;
  canWrite: boolean;
  family: boolean;
  canAcknowledge: boolean;
  today: string;
  levels: Record<Level, string>;
  myLevels: Level[];
  // The classes the account leads as class teacher: the measures of that level hold for their pupils only.
  myClassIds: string[];
  remarkAck: boolean;
  categories: Category[];
  pending: number;
  // The commission exams screen: the server says who may open it (administrator, commission member, family of a decided exam).
  commission?: { manage: boolean; member: boolean; family: boolean } | null;
  configVersion: number | null;
  classes: { id: string; name: string }[];
  students: Student[];
};
export type Status = "pending" | "published" | "rejected" | "withdrawn";
export type Note = {
  id: string;
  version: number;
  status: Status;
  kind: Kind;
  day: string;
  categoryId: string;
  category: string;
  text: string;
  author: string;
  level: Level;
  levelTitle: string;
  studentId: string;
  student: string;
  className?: string;
  requireAck: boolean;
  acknowledged: boolean;
  acknowledgedAt: string | null;
  acknowledgedBy: string | null;
  letter: boolean;
  letterNumber: string;
  lesson: string;
  visible?: boolean;
  printed?: boolean;
  approval?: boolean;
  decision?: string;
  approvedBy?: string;
  withdrawReason?: string;
  canChange?: boolean;
  canDecide?: boolean;
};
export type History = {
  seq: number;
  actor: string;
  created: string;
  action: string;
  reason: string;
}[];
// The form of a new record.
export type NoteForm = {
  categoryId: string;
  day: string;
  text: string;
  classId: string;
  visible: boolean;
  printed: boolean;
  students: string[];
};

// One record may be written about at most this many pupils.
export const PUPIL_LIMIT = 40;

export const kindLabels: Record<Kind, string> = {
  praise: "Похвала",
  remark: "Замечание",
};
export const statusLabels: Record<Status, string> = {
  pending: "На согласовании",
  published: "Действует",
  rejected: "Отклонена",
  withdrawn: "Отозвана",
};
export const actionLabels: Record<string, string> = {
  created: "Создана",
  edited: "Изменена",
  withdrawn: "Отозвана",
  acknowledged: "Родитель ознакомился",
  approved: "Утверждена",
  rejected: "Отклонена",
};

// The moment of an event as the school reads it.
export const stamp = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("ru-RU", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : "";

// «2026-11-16» becomes «16.11.2026»; the string is split, so no time zone can move the day.
export function dayLabel(day: string) {
  const [year, month, date] = day.split("-");
  return `${date}.${month}.${year}`;
}

// The pupils the picker offers: those of the chosen class, narrowed by a part of the name. With nothing chosen and
// more pupils than the limit the picker shows a hint instead of everybody.
export function pickerPupils(
  students: Student[],
  classId: string,
  search: string,
  limit: number,
) {
  const needle = search.trim().toLocaleLowerCase("ru");
  if (!classId && !needle && students.length > limit)
    return { shown: [] as Student[], hint: true };
  const shown = students.filter(
    (s) =>
      (!classId || s.classId === classId) &&
      (!needle || s.name.toLocaleLowerCase("ru").includes(needle)),
  );
  return { shown, hint: false };
}

// A pupil is taken off the chosen ones, or put on them while there is room.
export function togglePupil(chosen: string[], id: string, limit: number) {
  if (chosen.includes(id)) return chosen.filter((x) => x !== id);
  return chosen.length < limit ? [...chosen, id] : chosen;
}

// Several pupils at once, in order and without repeats; null when they would not fit.
export function addPupils(chosen: string[], ids: string[], limit: number) {
  const all = [...new Set([...chosen, ...ids])];
  return all.length > limit ? null : all;
}

export type NoteChip = { key: string; title?: string };

// The facts of a record that are shown as small marks. The family sees no status, hidden or printed marks.
export function noteChips(note: Note, staff: boolean): NoteChip[] {
  const chips: NoteChip[] = [];
  if (staff && note.status !== "published") {
    chips.push(
      note.status === "rejected"
        ? { key: "rejected", title: note.decision }
        : note.status === "withdrawn"
          ? { key: "withdrawn", title: note.withdrawReason }
          : { key: "pending" },
    );
  }
  if (note.approvedBy) chips.push({ key: "approved", title: note.approvedBy });
  if (staff && note.visible === false) chips.push({ key: "hidden" });
  if (staff && note.printed) chips.push({ key: "printed" });
  if (note.letterNumber) chips.push({ key: "letter" });
  if (note.requireAck && note.status === "published")
    chips.push(
      note.acknowledged
        ? {
            key: "ackDone",
            title: `${stamp(note.acknowledgedAt)} (${note.acknowledgedBy ?? ""})`,
          }
        : { key: "ackWait" },
    );
  return chips;
}

// What a record offers to the person looking at it.
export function noteActions(
  note: Note,
  who: { staff: boolean; canAcknowledge: boolean },
) {
  const change = who.staff && Boolean(note.canChange);
  return {
    decide: Boolean(note.canDecide),
    acknowledge: who.canAcknowledge && note.requireAck && !note.acknowledged,
    letter: note.letter && note.status === "published",
    edit: change
      ? note.acknowledged
        ? ("off" as const)
        : ("on" as const)
      : null,
    withdraw: change,
    history: who.staff,
  };
}

// The form holds something that would be lost on leaving.
export const formDirty = (form: { text: string; students: string[] }) =>
  Boolean(form.text.trim() || form.students.length);

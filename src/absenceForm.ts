type Child = { id: string; electronicNotes?: boolean };

export const notesOff = (child?: Child) => child?.electronicNotes === false;

// A class with electronic notes off refuses only new notices: the parent still
// switches to a sibling in an open class and removes files not yet sent.
export const formLocks = (busy: boolean, child?: Child) => ({
  pick: busy,
  write: busy || notesOff(child),
  remove: busy,
});

// Keeps the chosen child; otherwise the first one whose class still takes notices.
export const defaultChild = (students: Child[], current: string) =>
  current || (students.find((s) => !notesOff(s)) ?? students[0])?.id || "";

export type AbsenceCode = {
  id: string;
  name: string;
  parents: boolean;
  active: boolean;
};

// The first lesson of a note starts the period at the lesson's start, the last one ends it at the lesson's end.
export const lessonEdge = (
  lesson: { date: string; start: string; end: string },
  edge: "start" | "end",
) => ({
  date: lesson.date,
  time: edge === "start" ? lesson.start : lesson.end,
});

// Names are live: a renamed type shows its new name on old notes. Notes without a type are "Без типа".
export const codeName = (
  codes: { id: string; name: string }[],
  id?: string | null,
) => (id ? (codes.find((c) => c.id === id)?.name ?? id) : "Без типа");

// Query string of the notice list; empty values are omitted.
export const noticeQuery = (
  values: Record<string, string | number | null | undefined>,
) => {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value !== undefined && value !== null && value !== "")
      query.set(key, String(value));
  return query.toString();
};

// Mirrors the server rules of the type list: names are unique ignoring case, none is empty, one stays active.
export const codeSetError = (items: { name: string; active: boolean }[]) => {
  const names = items.map((c) => c.name.trim().toLowerCase());
  if (names.some((n) => !n)) return "У каждого типа должно быть название.";
  if (new Set(names).size !== names.length)
    return "Названия типов не должны повторяться.";
  if (!items.some((c) => c.active))
    return "Оставьте хотя бы один действующий тип.";
  return "";
};

export const schoolSubmitLabel = (count: number) =>
  count === 1 ? "Внести записку" : `Внести записки (${count})`;

// A 409 means the checked preview is stale (expired, changed by someone else, request id reused): drop it and use a new request id.
export const staleSchoolPreview = (error: unknown) =>
  (error as { status?: number } | null)?.status === 409;

// Only decisions made together are "общее решение"; a note entered by the school is also stored under a batch id but decides nothing jointly.
export const sharedDecision = (entry: {
  action: string;
  batchId?: string | null;
}) => Boolean(entry.batchId) && entry.action !== "recorded";

// "Болезнь 2, Представляет школу 1": the list order first, then notes without a type; zeros are skipped.
export const codeBreakdown = (
  counts: Record<string, number> | undefined,
  codes: { id: string; name: string }[],
) => {
  const known = new Set(codes.map((c) => c.id));
  const ids = [
    ...codes.map((c) => c.id),
    ...Object.keys(counts ?? {}).filter((id) => id && !known.has(id)),
    "",
  ];
  return ids
    .filter((id) => (counts?.[id] ?? 0) > 0)
    .map((id) => `${codeName(codes, id)} ${counts![id]}`)
    .join(", ");
};

export type Subject = { id: string; name: string };
export type SchoolPupil = { id: string; name: string; classId: string };
export type PreviewLesson = {
  date: string;
  start: string;
  end: string;
  subject: string;
};

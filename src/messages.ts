/** Inbox rows of the account: the freshly loaded first page leads, older rows the user paged to stay below it. */
export function mergeInbox<T extends { id: string; latestId: number }>(
  old: T[],
  fresh: T[],
  more: boolean,
): T[] {
  // A first page that is not full is the whole inbox; nothing paged earlier can still belong to it.
  if (!more) return fresh;
  const edge = fresh[fresh.length - 1]?.latestId ?? Infinity,
    seen = new Set(fresh.map((t) => t.id));
  return [...fresh, ...old.filter((t) => !seen.has(t.id) && t.latestId < edge)];
}

export type RecipientFields = {
  kind: string;
  recipient: string;
  student: string;
  groups: string[];
  audience: string[];
  preset: string;
  savedGroup: string;
  groupMode: string;
  people: string[];
};

/** Everything that decides who receives a message. Title, text, files and options do not, so editing them keeps a checked list valid. */
export function recipientKey(f: RecipientFields) {
  return JSON.stringify([
    f.kind,
    f.recipient,
    f.student,
    [...f.groups].sort(),
    [...f.audience].sort(),
    f.preset,
    f.savedGroup,
    f.groupMode,
    [...f.people].sort(),
  ]);
}

/** Pupils matching the search, at most `cap`; the chosen pupil stays selectable even when the search excludes them. */
export function filterStudents<T extends { id: string; name: string }>(
  students: T[],
  query: string,
  selected: string,
  cap = 200,
) {
  const q = query.trim().toLocaleLowerCase("ru"),
    hits = q
      ? students.filter((s) => s.name.toLocaleLowerCase("ru").includes(q))
      : students,
    items = hits.slice(0, cap),
    chosen = students.find((s) => s.id === selected);
  if (chosen && !items.some((s) => s.id === chosen.id)) items.unshift(chosen);
  return { items, cut: hits.length > cap };
}

/** Browsers show these in place; the server sends them inline only after checking the content against the type. */
export function inlineImage(filename: string) {
  return /\.(jpe?g|png|heic|heif)$/i.test(filename);
}

/** Counter for the tab: the server counts at most a hundred threads, so a long backlog reads «99+». */
export function unreadLabel(count: number, more: boolean) {
  return count > 99 ? "99+" : more ? `${count}+` : String(count);
}

/** A failed load of the home feed: a 403 closes it for this account; any other failure shows its message while nothing is on screen yet. */
export function feedFailure(
  status: number | undefined,
  message: string,
  loaded: boolean,
) {
  return {
    closed: status === 403,
    error:
      status === 403 || loaded
        ? ""
        : message || "Не удалось загрузить объявления.",
  };
}

export const noTitle = "Без темы";

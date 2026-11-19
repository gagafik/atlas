import { byClassName, byText } from "./teachingLoad";

// The rules of the directory lists (classes, groups, teachers, rooms, subjects): what a search matches, how the rows are
// ordered. Paging and word forms are shared with «Учебная нагрузка» (teachingLoad.ts). The screen only draws the result.

export type Cell = string | number | null | undefined;
export type SortSpec = { key: string; dir: "asc" | "desc" };

/** What the rules need to know about a column. */
export type ColumnRule<T> = {
  key: string;
  /** The text the row shows in this column: the search looks at it, never at ids. */
  text: (row: T) => string;
  /** The value the column sorts by (numbers sort as numbers); the shown text when omitted. */
  sort?: (row: T) => Cell;
  /** The column sorts by the default order of the list (names in school order). */
  byOrder?: boolean;
};

export type Entry<T> = {
  row: T;
  texts: string[];
  values: Cell[];
  /** All shown texts of the row, normalised for the search; every word of a query must occur in it. */
  haystack: string;
};

/** Lower case, «ё» as «е», single spaces. */
export function normalise(text: string) {
  return text.toLowerCase().replaceAll("ё", "е").replace(/\s+/g, " ").trim();
}

export function prepare<T>(rows: T[], columns: ColumnRule<T>[]): Entry<T>[] {
  return rows.map((row) => {
    const texts = columns.map((c) => c.text(row));
    return {
      row,
      texts,
      values: columns.map((c, i) => (c.sort ? c.sort(row) : texts[i])),
      haystack: normalise(texts.join(" ")),
    };
  });
}

export function filterEntries<T>(entries: Entry<T>[], search: string) {
  const words = normalise(search).split(" ").filter(Boolean);
  return words.length
    ? entries.filter((e) => words.every((w) => e.haystack.includes(w)))
    : entries;
}

const isEmpty = (v: Cell) =>
  v == null || v === "" || (typeof v === "number" && Number.isNaN(v));

function compareCells(a: Cell, b: Cell) {
  return typeof a === "number" && typeof b === "number"
    ? a - b
    : byText(String(a), String(b));
}

/**
 * Sorts by one column; empty values stay last in both directions, equal values keep the default order.
 * A column that sorts by order uses the default order itself, reversed for «desc».
 */
export function sortEntries<T>(
  entries: Entry<T>[],
  columns: ColumnRule<T>[],
  sort: SortSpec,
  order: (a: T, b: T) => number,
): Entry<T>[] {
  const index = columns.findIndex((c) => c.key === sort.key);
  const sign = sort.dir === "desc" ? -1 : 1;
  if (index < 0 || columns[index].byOrder)
    return [...entries].sort((a, b) =>
      index < 0 ? order(a.row, b.row) : sign * order(a.row, b.row),
    );
  return [...entries].sort((x, y) => {
    const a = x.values[index],
      b = y.values[index];
    const ea = isEmpty(a),
      eb = isEmpty(b);
    if (ea || eb) return ea && eb ? order(x.row, y.row) : ea ? 1 : -1;
    return sign * compareCells(a, b) || order(x.row, y.row);
  });
}

/** A click on a heading: another column starts ascending, the same column reverses. */
export function nextSort(current: SortSpec, key: string): SortSpec {
  if (current.key !== key) return { key, dir: "asc" };
  return { key, dir: current.dir === "asc" ? "desc" : "asc" };
}

/** Names with Russian locale and numbers as numbers: room «2» before «10». */
export const byName = (a: { name: string }, b: { name: string }) =>
  byText(a.name, b.name);

/** Classes in school order. */
export const classOrder = byClassName;

/** Groups in school order: by their first class, then by name. */
export function groupOrder(
  classes: { id: string; name: string }[],
): (a: GroupLike, b: GroupLike) => number {
  const rank = new Map([...classes].sort(byClassName).map((c, i) => [c.id, i]));
  const first = (g: GroupLike) =>
    g.classIds.reduce(
      (m, id) => Math.min(m, rank.get(id) ?? Infinity),
      Infinity,
    );
  return (a, b) => {
    const x = first(a),
      y = first(b);
    return x === y ? byText(a.name, b.name) : x < y ? -1 : 1;
  };
}
type GroupLike = { name: string; classIds: string[] };

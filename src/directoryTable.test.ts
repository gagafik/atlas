import { describe, expect, it } from "vitest";
import {
  byName,
  classOrder,
  filterEntries,
  groupOrder,
  nextSort,
  prepare,
  sortEntries,
  type ColumnRule,
  type SortSpec,
} from "./directoryTable";

type Room = {
  id: string;
  name: string;
  capacity: number | null;
  place: string;
};
const columns: ColumnRule<Room>[] = [
  { key: "name", text: (r) => r.name, byOrder: true },
  {
    key: "capacity",
    text: (r) => (r.capacity ?? "Не указана").toString(),
    sort: (r) => r.capacity,
  },
  { key: "place", text: (r) => r.place },
];
const rooms: Room[] = [
  { id: "r-10", name: "10", capacity: 30, place: "Главное" },
  { id: "r-2", name: "2", capacity: null, place: "" },
  { id: "r-9", name: "Спортзал", capacity: 100, place: "Новое" },
  { id: "r-3", name: "3", capacity: 8, place: "Главное" },
];
const names = (entries: { row: Room }[]) => entries.map((e) => e.row.name);
const run = (sort: SortSpec, list = rooms) =>
  names(sortEntries(prepare(list, columns), columns, sort, byName));

describe("search", () => {
  it("matches the shown text, ignoring case and ё", () => {
    const entries = prepare(rooms, columns);
    expect(names(filterEntries(entries, "СПОРТ"))).toEqual(["Спортзал"]);
    expect(names(filterEntries(entries, "не указана"))).toEqual(["2"]);
    expect(
      names(
        filterEntries(
          prepare([{ ...rooms[0], place: "Ёлка" }], columns),
          "елка",
        ),
      ),
    ).toEqual(["10"]);
  });
  it("never matches ids", () => {
    expect(filterEntries(prepare(rooms, columns), "r-9")).toEqual([]);
  });
  it("matches words of different columns together", () => {
    expect(names(filterEntries(prepare(rooms, columns), "8 главное"))).toEqual([
      "3",
    ]);
  });
  it("keeps everything for an empty search", () => {
    expect(filterEntries(prepare(rooms, columns), "  ")).toHaveLength(4);
  });
});

describe("sorting", () => {
  it("orders names with numbers as numbers", () => {
    expect(run({ key: "name", dir: "asc" })).toEqual([
      "2",
      "3",
      "10",
      "Спортзал",
    ]);
    expect(run({ key: "name", dir: "desc" })).toEqual([
      "Спортзал",
      "10",
      "3",
      "2",
    ]);
  });
  it("sorts numbers as numbers and keeps empty values last in both directions", () => {
    expect(run({ key: "capacity", dir: "asc" })).toEqual([
      "3",
      "10",
      "Спортзал",
      "2",
    ]);
    expect(run({ key: "capacity", dir: "desc" })).toEqual([
      "Спортзал",
      "10",
      "3",
      "2",
    ]);
  });
  it("keeps empty text last in both directions and equal values in the default order", () => {
    expect(run({ key: "place", dir: "asc" })).toEqual([
      "3",
      "10",
      "Спортзал",
      "2",
    ]);
    expect(run({ key: "place", dir: "desc" })).toEqual([
      "Спортзал",
      "3",
      "10",
      "2",
    ]);
  });
  it("does not change the list it sorts", () => {
    const entries = prepare(rooms, columns);
    sortEntries(entries, columns, { key: "capacity", dir: "asc" }, byName);
    expect(names(entries)).toEqual(["10", "2", "Спортзал", "3"]);
  });
  it("a click starts another column ascending and reverses the same one", () => {
    const first = nextSort({ key: "name", dir: "asc" }, "capacity");
    expect(first).toEqual({ key: "capacity", dir: "asc" });
    expect(nextSort(first, "capacity").dir).toBe("desc");
    expect(nextSort(nextSort(first, "capacity"), "capacity").dir).toBe("asc");
  });
});

describe("school order", () => {
  const classes = ["10А", "2Б", "1А", "2А", "11Б", "5А"].map((name, i) => ({
    id: `c${i}`,
    name,
  }));
  it("puts 1А before 2А before 10А", () => {
    expect([...classes].sort(classOrder).map((c) => c.name)).toEqual([
      "1А",
      "2А",
      "2Б",
      "5А",
      "10А",
      "11Б",
    ]);
  });
  it("sorts a name column in school order through the list rules, in both directions", () => {
    const columnsOfClasses: ColumnRule<{ id: string; name: string }>[] = [
      { key: "name", text: (c) => c.name, byOrder: true },
    ];
    const shown = (dir: "asc" | "desc") =>
      sortEntries(
        prepare([...classes, { id: "c9", name: "9А" }], columnsOfClasses),
        columnsOfClasses,
        { key: "name", dir },
        classOrder,
      ).map((e) => e.row.name);
    expect(shown("asc")).toEqual(["1А", "2А", "2Б", "5А", "9А", "10А", "11Б"]);
    expect(shown("desc")).toEqual(["11Б", "10А", "9А", "5А", "2Б", "2А", "1А"]);
  });
  it("orders groups by their first class, then by name", () => {
    const groups = [
      { name: "Английский 10", classIds: ["c0"] },
      { name: "Музыка", classIds: ["c3", "c1"] },
      { name: "Без класса", classIds: [] },
      { name: "Английский 1", classIds: ["c2"] },
      { name: "Лабораторная", classIds: ["c2"] },
    ];
    expect([...groups].sort(groupOrder(classes)).map((g) => g.name)).toEqual([
      "Английский 1",
      "Лабораторная",
      "Музыка",
      "Английский 10",
      "Без класса",
    ]);
  });
});

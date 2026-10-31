import { expect, it } from "vitest";
import {
  feedFailure,
  filterStudents,
  inlineImage,
  mergeInbox,
  recipientKey,
  unreadLabel,
  type RecipientFields,
} from "./messages";

const row = (id: string, latestId: number, mark = "") => ({
  id,
  latestId,
  mark,
});

it("keeps older paged rows below a refreshed first page without duplicates", () => {
  const old = [row("a", 9), row("b", 8), row("c", 5), row("d", 3)],
    fresh = [row("e", 12), row("a", 10, "new"), row("b", 8, "read")];
  const merged = mergeInbox(old, fresh, true);
  expect(merged.map((t) => t.id)).toEqual(["e", "a", "b", "c", "d"]);
  expect(merged.find((t) => t.id === "a")?.mark).toBe("new");
  expect(merged.find((t) => t.id === "b")?.mark).toBe("read");
});

it("drops a row the first page should have carried but did not, and everything when the page was the whole inbox", () => {
  const old = [row("a", 9), row("gone", 9), row("c", 2)];
  expect(
    mergeInbox(old, [row("a", 9), row("b", 8)], true).map((t) => t.id),
  ).toEqual(["a", "b", "c"]);
  expect(mergeInbox(old, [row("a", 9)], false).map((t) => t.id)).toEqual(["a"]);
});

const fields: RecipientFields = {
  kind: "announcement",
  recipient: "",
  student: "",
  groups: ["g-a", "g-b"],
  audience: ["parent", "student"],
  preset: "",
  savedGroup: "",
  groupMode: "contacts",
  people: ["p1"],
};

it("changes the recipient key with who receives the message and nothing else", () => {
  const key = recipientKey(fields);
  expect(recipientKey({ ...fields, groups: ["g-b", "g-a"] })).toBe(key);
  expect(recipientKey({ ...fields, audience: ["student", "parent"] })).toBe(
    key,
  );
  for (const change of [
    { groups: ["g-a"] },
    { audience: ["parent"] },
    { preset: "school" },
    { savedGroup: "x" },
    { people: ["p2"] },
    { recipient: "u1" },
    { student: "s1" },
    { kind: "group" },
    { groupMode: "audience" },
  ])
    expect(recipientKey({ ...fields, ...change })).not.toBe(key);
});

const pupils = [
  { id: "1", name: "Ёлкин Артём" },
  { id: "2", name: "Иванов Иван" },
  { id: "3", name: "Петров Артём" },
];

it("finds pupils by part of the name in any case and keeps the chosen one", () => {
  expect(filterStudents(pupils, "АРТЁМ", "").items.map((s) => s.id)).toEqual([
    "1",
    "3",
  ]);
  expect(filterStudents(pupils, "ёлк", "").items.map((s) => s.id)).toEqual([
    "1",
  ]);
  expect(filterStudents(pupils, "иван", "3").items.map((s) => s.id)).toEqual([
    "3",
    "2",
  ]);
});

it("caps the list and says so", () => {
  const many = Array.from({ length: 12 }, (_, i) => ({
    id: String(i),
    name: `Ученик ${i}`,
  }));
  const capped = filterStudents(many, "", "", 5);
  expect(capped.items).toHaveLength(5);
  expect(capped.cut).toBe(true);
  expect(filterStudents(many, "", "9", 5).items[0].id).toBe("9");
  expect(filterStudents(many, "", "", 12).cut).toBe(false);
});

it("shows only validated picture types inline", () => {
  for (const name of ["a.jpg", "b.JPEG", "c.Png", "d.HEIC", "e.heif"])
    expect(inlineImage(name)).toBe(true);
  for (const name of ["a.pdf", "b.doc", "c.xls", "d.jpg.txt", "e"])
    expect(inlineImage(name)).toBe(false);
});

it("labels a long backlog of unread threads", () => {
  expect(unreadLabel(3, false)).toBe("3");
  expect(unreadLabel(40, true)).toBe("40+");
  expect(unreadLabel(120, true)).toBe("99+");
});

it("tells why the home feed is empty when its first load fails", () => {
  expect(feedFailure(500, "Сервер ответил ошибкой.", false)).toEqual({
    closed: false,
    error: "Сервер ответил ошибкой.",
  });
  expect(feedFailure(undefined, "", false).error).not.toBe("");
  // Once announcements are on screen a failed refresh keeps them and stays quiet; a 403 closes the feed.
  expect(feedFailure(500, "Сервер ответил ошибкой.", true).error).toBe("");
  expect(feedFailure(403, "Нет доступа.", false)).toEqual({
    closed: true,
    error: "",
  });
});

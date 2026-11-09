import { expect, it } from "vitest";
import {
  codeBreakdown,
  codeSetError,
  defaultChild,
  formLocks,
  lessonEdge,
  noticeQuery,
  sharedDecision,
  staleSchoolPreview,
} from "./absenceForm";

const closed = { id: "s1", electronicNotes: false };
const open = { id: "s3", electronicNotes: true };

it("keeps the child picker and unsent file removal usable when the class is closed", () => {
  expect(formLocks(false, closed)).toEqual({
    pick: false,
    write: true,
    remove: false,
  });
  expect(formLocks(false, open)).toEqual({
    pick: false,
    write: false,
    remove: false,
  });
  // Servers older than the switch send no flag: notes are on.
  expect(formLocks(false, { id: "s2" }).write).toBe(false);
  expect(formLocks(false, undefined).write).toBe(false);
});

it("locks everything while a request is running", () => {
  expect(formLocks(true, open)).toEqual({
    pick: true,
    write: true,
    remove: true,
  });
});

it("starts on the first child whose class takes notices", () => {
  expect(defaultChild([closed, open], "")).toBe("s3");
  expect(defaultChild([open, closed], "")).toBe("s3");
  // Every class closed: still select someone, the form shows why it is locked.
  expect(defaultChild([closed], "")).toBe("s1");
  expect(defaultChild([], "")).toBe("");
});

it("never replaces a child the parent already chose", () => {
  expect(defaultChild([closed, open], "s1")).toBe("s1");
});

const lesson = { date: "2026-08-17", start: "08:30", end: "09:10" };
const codes = [
  { id: "illness", name: "Болезнь" },
  { id: "school", name: "Представляет школу" },
];

it("sets the first lesson from its start and the last lesson to its end", () => {
  expect(lessonEdge(lesson, "start")).toEqual({
    date: "2026-08-17",
    time: "08:30",
  });
  expect(lessonEdge(lesson, "end")).toEqual({
    date: "2026-08-17",
    time: "09:10",
  });
});

it("builds the list query without empty values", () => {
  const pairs = (query: string) =>
    Object.fromEntries(new URLSearchParams(query));
  expect(pairs(noticeQuery({ status: "all", classId: "c a" }))).toEqual({
    status: "all",
    classId: "c a",
  });
  expect(
    pairs(
      noticeQuery({ status: "pending", classId: "", start: "", before: 7 }),
    ),
  ).toEqual({ status: "pending", before: "7" });
});

it("refuses a type list the server would refuse", () => {
  const ok = { name: "Болезнь", active: true };
  expect(codeSetError([ok, { name: "Поездка", active: false }])).toBe("");
  expect(codeSetError([ok, { name: " болезнь ", active: true }])).not.toBe("");
  expect(codeSetError([ok, { name: "  ", active: true }])).not.toBe("");
  expect(codeSetError([{ ...ok, active: false }])).not.toBe("");
});

it("drops a school preview only when the server reports it as stale", () => {
  const conflict = Object.assign(new Error("Предпросмотр истёк."), {
    status: 409,
  });
  expect(staleSchoolPreview(conflict)).toBe(true);
  expect(
    staleSchoolPreview(Object.assign(new Error("x"), { status: 403 })),
  ).toBe(false);
  expect(staleSchoolPreview(new Error("Сервер недоступен."))).toBe(false);
  expect(staleSchoolPreview(null)).toBe(false);
});

it("calls only joint approvals and rejections a shared decision", () => {
  expect(sharedDecision({ action: "approve", batchId: "b1" })).toBe(true);
  expect(sharedDecision({ action: "revoke", batchId: "b1" })).toBe(true);
  expect(sharedDecision({ action: "recorded", batchId: "b1" })).toBe(false);
  expect(sharedDecision({ action: "approve", batchId: null })).toBe(false);
  expect(sharedDecision({ action: "submitted" })).toBe(false);
});

it("lists the excused lessons per type and skips zeros", () => {
  const text = codeBreakdown({ illness: 2, school: 0 }, codes);
  expect(text).toContain("Болезнь");
  expect(text).toContain("2");
  expect(text).not.toContain("Представляет школу");
});

it("counts the lessons without a type apart from the typed ones", () => {
  const text = codeBreakdown({ illness: 0, "": 3 }, codes);
  expect(text).toContain("3");
  expect(text).not.toContain("Болезнь");
  expect(codeBreakdown({}, codes)).toBe("");
  expect(codeBreakdown(undefined, codes)).toBe("");
});

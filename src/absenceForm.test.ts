import { expect, it } from "vitest";
import {
  codeBreakdown,
  codeName,
  codeSetError,
  defaultChild,
  formLocks,
  lessonEdge,
  noticeQuery,
  schoolSubmitLabel,
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

it("names a type by its id and falls back to no type", () => {
  expect(codeName(codes, "illness")).toBe("Болезнь");
  expect(codeName(codes, null)).toBe("Без типа");
  expect(codeName(codes, "")).toBe("Без типа");
  expect(codeName(codes, undefined)).toBe("Без типа");
});

it("builds the list query without empty values", () => {
  expect(noticeQuery({ status: "all", classId: "c a" })).toBe(
    "status=all&classId=c+a",
  );
  expect(
    noticeQuery({ status: "pending", classId: "", start: "", before: 7 }),
  ).toBe("status=pending&before=7");
});

it("refuses a type list the server would refuse", () => {
  const ok = { name: "Болезнь", active: true };
  expect(codeSetError([ok, { name: "Поездка", active: false }])).toBe("");
  expect(codeSetError([ok, { name: " болезнь ", active: true }])).not.toBe("");
  expect(codeSetError([ok, { name: "  ", active: true }])).not.toBe("");
  expect(codeSetError([{ ...ok, active: false }])).not.toBe("");
});

it("labels the school note button by the number of pupils", () => {
  expect(schoolSubmitLabel(1)).toBe("Внести записку");
  expect(schoolSubmitLabel(3)).toBe("Внести записки (3)");
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
  expect(codeBreakdown({ illness: 2, school: 1 }, codes)).toBe(
    "Болезнь 2, Представляет школу 1",
  );
  expect(codeBreakdown({ illness: 0, "": 3 }, codes)).toBe("Без типа 3");
  expect(codeBreakdown({}, codes)).toBe("");
  expect(codeBreakdown(undefined, codes)).toBe("");
});

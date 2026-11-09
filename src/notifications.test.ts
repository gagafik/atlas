import { describe, it, expect } from "vitest";
import {
  groupNotices,
  openEvent,
  pushLabel,
  PUSH_LABELS,
  type Notice,
} from "./notifications";

const base = {
  workspace: "demo",
  revision: 1,
  created: "2026-08-17T10:00:00+00:00",
  action: "journal.grade",
  read: false,
  category: "grades",
};
const notice = (id: number, ...children: string[]): Notice => ({
  ...base,
  id,
  students: children.map((c) => ({ id: c, name: "Имя " + c })),
});

describe("groupNotices", () => {
  it("groups by child in first-appearance order when several children appear", () => {
    const groups = groupNotices([
      notice(5, "s1"),
      notice(4, "s2"),
      notice(3, "s1", "s2"),
      notice(2),
      notice(1, "s2"),
    ]);
    expect(groups.map((g) => g.key)).toEqual(["s1", "s2", "many", "general"]);
    expect(groups.map((g) => g.items.map((n) => n.id))).toEqual([
      [5],
      [4, 1],
      [3],
      [2],
    ]);
    // A group of one child is named by the name the data gives that child.
    expect(groups[0].label).toBe("Имя s1");
    expect(groups[1].label).toBe("Имя s2");
  });
  it("keeps one plain list for a single child, with or without general entries", () => {
    const items = [notice(3, "s1"), notice(2), notice(1, "s1")];
    const groups = groupNotices(items);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("");
    expect(groups[0].items).toEqual(items);
    expect(groupNotices([])).toEqual([{ key: "all", label: "", items: [] }]);
  });
});

describe("openEvent", () => {
  const result = (module: string) => ({
    module,
    workspace: "demo",
    studentId: "s1",
    day: "2026-08-17",
    recordId: "r",
  });
  it.each([
    ["quizzes", "atlas-open-quizzes"],
    ["duty", "atlas-open-duties"],
    ["behaviour", "atlas-open-behaviour"],
    ["assessments", "atlas-open-assessments"],
    ["meetings", "atlas-open-meetings"],
    ["forms", "atlas-open-forms"],
    ["clubs", "atlas-open-clubs"],
    ["meeting_events", "atlas-open-meetings"],
    ["meeting_waitlist", "atlas-open-meetings"],
    ["form_items", "atlas-open-forms"],
    ["homework", "atlas-open-homework"],
    ["absences", "atlas-open-absences"],
    ["behaviour_staff", "atlas-open-behaviour"],
    ["message_reports", "atlas-open-messages"],
  ])("opens the %s notice on the screen that listens for %s", (module, name) => {
    expect(openEvent(result(module))).toEqual([name, { workspace: "demo" }]);
  });
  it("tells the journal the pupil, the day and the lesson", () => {
    expect(openEvent(result("journal"))).toEqual([
      "atlas-open-journal",
      { workspace: "demo", studentId: "s1", day: "2026-08-17", lessonKey: "r" },
    ]);
  });
  it("tells the finals the pupil and the period", () => {
    expect(openEvent(result("finals"))).toEqual([
      "atlas-open-journal",
      { workspace: "demo", studentId: "s1", finalsPeriodId: "r" },
    ]);
  });
  it("tells the timetable the pupil", () => {
    expect(openEvent(result("timetable"))).toEqual([
      "atlas-open-schedule",
      { workspace: "demo", studentId: "s1" },
    ]);
  });
  it("tells the messages the thread", () => {
    expect(openEvent(result("messages"))).toEqual([
      "atlas-open-messages",
      { workspace: "demo", threadId: "r" },
    ]);
  });
  it("opens the class meetings and the consultations as sections of the meetings", () => {
    expect(openEvent(result("class_meetings"))).toEqual([
      "atlas-open-meetings",
      { workspace: "demo", section: "class" },
    ]);
    // The server names the section of a consultation booking; a parent meeting has none.
    expect(
      openEvent({ ...result("meetings"), section: "consultations" }),
    ).toEqual([
      "atlas-open-meetings",
      { workspace: "demo", section: "consultations" },
    ]);
  });
  it("takes the child of an entry about one child, whatever its module, and none for several", () => {
    expect(
      openEvent(result("homework"), [{ id: "s2", name: "Имя" }])?.[1],
    ).toEqual({ workspace: "demo", studentId: "s2" });
    expect(
      openEvent(result("homework"), [
        { id: "s2", name: "Имя" },
        { id: "s3", name: "Имя" },
      ])?.[1],
    ).toEqual({ workspace: "demo" });
  });
  it("prefers the pupil the server named to the one of the entry", () => {
    expect(
      openEvent(result("journal"), [{ id: "s2", name: "Имя" }])?.[1].studentId,
    ).toBe("s1");
  });
  it("answers null for an unknown module, including prototype names", () => {
    expect(openEvent(result("unknown"))).toBeNull();
    expect(openEvent(result("constructor"))).toBeNull();
  });
});

describe("push labels", () => {
  it("falls back to the generic label for an unknown category", () => {
    for (const category of ["nothing-like-it", "constructor", undefined])
      expect(pushLabel(category)).toBe(PUSH_LABELS.other);
  });
  it("labels a known category with its own entry", () => {
    expect(pushLabel("grades")).toBe(PUSH_LABELS.grades);
    expect(PUSH_LABELS.grades).not.toBe(PUSH_LABELS.other);
  });
});

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
    expect(groups.map((g) => g.label)).toEqual([
      "Имя s1",
      "Имя s2",
      "Несколько детей",
      "Общее",
    ]);
    expect(groups.map((g) => g.items.map((n) => n.id))).toEqual([
      [5],
      [4, 1],
      [3],
      [2],
    ]);
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
  it("maps every module to the screen that already listens for it", () => {
    expect(openEvent(result("journal"))).toEqual([
      "atlas-open-journal",
      { workspace: "demo", studentId: "s1", day: "2026-08-17" },
    ]);
    expect(openEvent(result("timetable"))).toEqual([
      "atlas-open-schedule",
      { workspace: "demo", studentId: "s1" },
    ]);
    expect(openEvent(result("finals"))).toEqual([
      "atlas-open-journal",
      { workspace: "demo", studentId: "s1", finalsPeriodId: "r" },
    ]);
    expect(openEvent(result("messages"))).toEqual([
      "atlas-open-messages",
      { workspace: "demo", threadId: "r" },
    ]);
    const plain: [string, string][] = [
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
    ];
    for (const [module, name] of plain)
      expect(openEvent(result(module))).toEqual([name, { workspace: "demo" }]);
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
  it("answers null for an unknown module, including prototype names", () => {
    expect(openEvent(result("unknown"))).toBeNull();
    expect(openEvent(result("constructor"))).toBeNull();
  });
});

describe("push labels", () => {
  it("falls back to the generic label for an unknown category", () => {
    expect(pushLabel("grades")).toBe(PUSH_LABELS.grades);
    expect(pushLabel("constructor")).toBe("Обновление в Атласе");
    expect(pushLabel(undefined)).toBe("Обновление в Атласе");
  });
});

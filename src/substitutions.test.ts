import { describe, expect, it } from "vitest";
import {
  effectiveType,
  lessonProblem,
  reportCsv,
  type SubstitutionReportData,
} from "./substitutions";
import type { DatedLesson } from "./types";

const lesson = (extra: Record<string, unknown>) =>
  ({
    kind: "change",
    changeType: null,
    onDate: "2026-08-17",
    sourceDate: "2026-08-17",
    ...extra,
  }) as Parameters<typeof effectiveType>[0];

describe("substitution types", () => {
  it("derives the type of records made before types existed", () => {
    expect(
      effectiveType(lesson({ onDate: "2026-08-18", sourceDate: "2026-08-17" })),
    ).toBe("move");
    expect(effectiveType(lesson({}))).toBe("substitution");
    expect(effectiveType(lesson({ kind: "cancel" }))).toBe("cancel");
    expect(effectiveType(lesson({ kind: "add", sourceDate: null }))).toBe(
      "extra",
    );
    expect(effectiveType(lesson({ changeType: "self_study" }))).toBe(
      "self_study",
    );
  });
});

const totals = (extra = {}) => ({
  planned: 6,
  held: 7,
  own: 6,
  substitutions: 1,
  extra: 0,
  missed: 0,
  missedAbsent: 0,
  cancelled: 0,
  moved: 0,
  byType: { substitution: 1, merge: 0, self_study: 0, move: 0 },
  ...extra,
});
const report = (name: string): SubstitutionReportData => ({
  revision: 1,
  month: "2026-08",
  first: "2026-08-17",
  last: "2026-08-31",
  teachers: [{ teacherId: "t", name, ...totals() }],
  totals: totals(),
});

describe("monthly report CSV", () => {
  it("starts with a BOM, quotes every cell and gives each teacher's figures under their column, with a totals row", () => {
    const csv = reportCsv(report("Иванова"));
    expect(csv.startsWith("\uFEFF")).toBe(true);
    const lines = csv.slice(1).split("\r\n");
    expect(lines).toHaveLength(3);
    const [header, line, total] = lines.map((row) =>
      row.split(";").map((cell) => cell.replace(/^"|"$/g, "")),
    );
    for (const row of lines)
      for (const cell of row.split(";")) expect(cell).toMatch(/^".*"$/);
    expect(line).toHaveLength(header.length);
    const at = (name: string) => line[header.indexOf(name)];
    expect(at("Учитель")).toBe("Иванова");
    expect(at("По расписанию")).toBe("6");
    expect(at("Проведено")).toBe("7");
    expect(at("Свои уроки")).toBe("6");
    expect(at("Замены за других")).toBe("1");
    expect(total[0]).not.toBe("Иванова");
    expect(total[header.indexOf("Проведено")]).toBe("7");
  });
  it("keeps merged groups out of the replacements they are not part of", () => {
    const data = report("Иванова");
    data.teachers[0] = { ...data.teachers[0], ...totals({ merged: 2 }) };
    const [header, line] = reportCsv(data)
      .slice(1)
      .split("\r\n")
      .map((row) => row.split(";").map((cell) => cell.replace(/^"|"$/g, "")));
    const at = (name: string) => Number(line[header.indexOf(name)]);
    expect(at("Присоединено при объединении")).toBe(2);
    const parts = ["Замена", "Самостоятельная работа", "Перенос (с заменой)"];
    expect(parts.reduce((sum, name) => sum + at(name), 0)).toBe(
      at("Замены за других"),
    );
  });
  it("protects names that Excel would evaluate as formulas", () => {
    expect(reportCsv(report("=HYPERLINK(1)"))).toContain('"\'=HYPERLINK(1)"');
  });
});

const record = (extra: Partial<DatedLesson> = {}): DatedLesson => ({
  id: "c",
  name: "Перенос",
  kind: "change",
  onDate: "2026-08-17",
  sourceDate: "2026-08-17",
  placementId: "eng",
  groupId: null,
  subjectId: "sub2",
  startTime: "09:00",
  endTime: "09:40",
  teacherIds: ["t2"],
  roomIds: ["r0"],
  changeType: "move",
  mergeWith: null,
  ...extra,
});
const slot = { start: "09:00", end: "09:40" };

describe("lesson form checks", () => {
  it("rejects a new same-date move that keeps the lesson time", () => {
    expect(lessonProblem(record(), slot)).not.toBe("");
    expect(
      lessonProblem(record(), slot, record({ changeType: "substitution" })),
    ).not.toBe("");
    expect(
      lessonProblem(record({ startTime: "15:00", endTime: "15:40" }), slot),
    ).toBe("");
    expect(lessonProblem(record({ onDate: "2026-08-18" }), slot)).toBe("");
  });
  it("lets a stored same-date move be saved again at its shifted time", () => {
    const stored = record({ startTime: "15:00", endTime: "15:40" });
    const shown = { start: "15:00", end: "15:40" };
    expect(
      lessonProblem(
        record({ startTime: "15:00", endTime: "15:40", roomIds: ["r1"] }),
        shown,
        stored,
      ),
    ).toBe("");
    // Without the stored shift the same time is a move that moves nothing.
    expect(
      lessonProblem(record({ startTime: "15:00", endTime: "15:40" }), shown),
    ).not.toBe("");
  });
  it("still requires the merge host and the self-study owner", () => {
    expect(lessonProblem(record({ changeType: "merge" }), slot)).not.toBe("");
    expect(
      lessonProblem(record({ changeType: "self_study", teacherIds: [] }), slot),
    ).not.toBe("");
    expect(
      lessonProblem(record({ kind: "cancel", changeType: "cancel" }), slot),
    ).toBe("");
  });
});

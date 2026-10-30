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
  it("starts with a BOM and quotes every cell with semicolon separators", () => {
    const csv = reportCsv(report("Иванова"));
    expect(csv.startsWith('\uFEFF"Учитель"')).toBe(true);
    const lines = csv.slice(1).split("\r\n");
    expect(lines).toHaveLength(3);
    expect(lines[1]).toBe(
      '"Иванова";"6";"7";"6";"1";"1";"0";"0";"0";"0";"0";"0";"0";"0"',
    );
    expect(lines[2].startsWith('"Итого";')).toBe(true);
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
    expect(lessonProblem(record(), slot)).toContain("менять дату или время");
    expect(
      lessonProblem(record(), slot, record({ changeType: "substitution" })),
    ).toContain("менять дату или время");
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
    ).toContain("менять дату или время");
  });
  it("still requires the merge host and the self-study owner", () => {
    expect(lessonProblem(record({ changeType: "merge" }), slot)).toContain(
      "присоединяется группа",
    );
    expect(
      lessonProblem(record({ changeType: "self_study", teacherIds: [] }), slot),
    ).toContain("ответственного");
    expect(
      lessonProblem(record({ kind: "cancel", changeType: "cancel" }), slot),
    ).toBe("");
  });
});

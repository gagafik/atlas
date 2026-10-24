import { describe, expect, it } from "vitest";
import {
  CHANGE_TYPES,
  DEFAULT_WEIGHTS,
  effectiveType,
  reportCsv,
  type SubstitutionReportData,
} from "./substitutions";
import type { ChangeType } from "./types";

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
  it("labels every type in Russian", () => {
    const all: ChangeType[] = [
      "substitution",
      "merge",
      "move",
      "self_study",
      "cancel",
      "extra",
    ];
    expect(Object.keys(CHANGE_TYPES).sort()).toEqual([...all].sort());
    for (const type of all) expect(CHANGE_TYPES[type]).toMatch(/[А-Яа-я]/);
  });
  it("keeps the documented default weights", () => {
    expect(DEFAULT_WEIGHTS).toEqual({
      subject: 40,
      group: 30,
      presence: 20,
      fairness: 20,
      building: 10,
    });
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

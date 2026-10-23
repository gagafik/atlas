import { describe, it, expect, vi } from "vitest";
import {
  journalCsv,
  documentRows,
  gradeLabel,
  numberField,
  numberValue,
  contentProblem,
  type JournalDocument,
} from "./journal";
import { download } from "./utils";
vi.mock("./utils", async (original) => ({
  ...(await original<typeof import("./utils")>()),
  download: vi.fn(),
}));
const document: JournalDocument = {
  lesson: {
    date: "2026-10-13",
    start: "09:00",
    end: "09:40",
    subjectId: "math",
    subject: "Математика",
    group: "10A",
    teachers: "Учитель",
    rooms: "301",
    status: "scheduled",
    rosterComplete: true,
    students: [{ id: "s1", name: "Ученик", className: "10A", enrolled: true }],
  },
  content: {
    topic: "=1+1",
    homework: "Страница 2; упражнение 1",
    dueDate: "2026-10-14",
    assessments: [
      { id: "a", name: "Тест", kind: "points", maxScore: 10, weight: 0 },
      { id: "b", name: "Участие", kind: "label", maxScore: 10, weight: 1 },
    ],
    records: [
      {
        studentId: "s1",
        attendance: "late",
        lateMinutes: 5,
        comment: "Комментарий",
        grades: [
          { assessmentId: "a", score: 0, label: "", comment: "Ноль — оценка" },
          { assessmentId: "b", score: null, label: "A*", comment: "" },
        ],
      },
    ],
  },
};
describe("journal export", () => {
  it("exports an approved excuse without overwriting the teacher's raw observation", () => {
    const d = structuredClone(document);
    d.content.records[0].attendance = "absent";
    d.content.records[0].lateMinutes = 0;
    const approvals = { s1: { id: "notice", version: 2 } };
    const rows = documentRows(d, "key", 1, approvals);
    expect(rows[0].record.attendance).toBe("excused");
    expect(d.content.records[0].attendance).toBe("absent");
    journalCsv(rows);
    expect(vi.mocked(download).mock.lastCall![1]).toContain(
      "Уважительная причина",
    );
    expect(documentRows(d, "key", 1)[0].record.attendance).toBe("absent");
    d.content.records[0].attendance = "present";
    expect(documentRows(d, "key", 1, approvals)[0].record.attendance).toBe(
      "present",
    );
  });
  it("retains zero scores, text labels, absence marks and CSV escaping", () => {
    journalCsv(documentRows(document, "key", 2));
    const csv = vi.mocked(download).mock.lastCall![1];
    expect(csv).toContain('"Тест";"0";"10";"0"');
    expect(csv).toContain('"Участие";"A*";"";""');
    expect(csv).toContain('"\'=1+1"');
    expect(csv).toContain('"Опоздание";"5"');
    expect(csv).toContain('"Страница 2; упражнение 1"');
    expect(csv.split("\r\n")).toHaveLength(3);
    expect(
      gradeLabel(
        document.content.records[0].grades[0],
        document.content.assessments[0],
      ),
    ).toBe("0 / 10");
  });
  it("exports attendance and homework when no assessment exists", () => {
    const d = structuredClone(document);
    d.content.assessments = [];
    d.content.records[0].grades = [];
    journalCsv(documentRows(d, "key", 1));
    const csv = vi.mocked(download).mock.lastCall![1];
    expect(csv.split("\r\n")).toHaveLength(2);
    expect(csv).toContain('"Опоздание";"5"');
    expect(csv).toContain('"2026-10-14"');
  });
});
describe("number fields", () => {
  it("keeps an erased field empty instead of turning it into zero", () => {
    expect(numberField("")).toBeNaN();
    expect(numberField("  ")).toBeNaN();
    expect(numberField("2.5")).toBe(2.5);
    expect(numberField("0")).toBe(0);
    expect(numberValue(Number.NaN)).toBe("");
    expect(numberValue(0)).toBe(0);
  });
  it("names the first invalid assessment number or topic hours", () => {
    const content = () => structuredClone(document.content);
    expect(contentProblem(content())).toBe("");
    for (const maxScore of [Number.NaN, 0, -1, 10001]) {
      const c = content();
      c.assessments[0].maxScore = maxScore;
      expect(contentProblem(c)).toContain("максимум баллов");
    }
    for (const weight of [Number.NaN, 101, -1]) {
      const c = content();
      c.assessments[0].weight = weight;
      expect(contentProblem(c)).toContain("вес работы");
    }
    const labelOnly = content();
    labelOnly.assessments[1].maxScore = Number.NaN;
    labelOnly.assessments[1].weight = Number.NaN;
    expect(contentProblem(labelOnly)).toBe("");
    const topic = content();
    topic.curriculum = {
      planId: "p",
      version: 1,
      topicId: "t",
      units: 1,
      planTitle: "План",
      title: "Тема",
      standard: "",
      plannedHours: 2,
    };
    expect(contentProblem(topic)).toBe("");
    for (const units of [Number.NaN, 0, 13, 1.5]) {
      topic.curriculum.units = units;
      expect(contentProblem(topic)).toContain("часы по теме");
    }
  });
});

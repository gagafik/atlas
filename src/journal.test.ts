import { describe, it, expect, vi } from "vitest";
import {
  journalCsv,
  documentRows,
  gradeLabel,
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

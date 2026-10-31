import { describe, it, expect, vi } from "vitest";
import {
  journalCsv,
  documentRows,
  gradeLabel,
  numberField,
  numberValue,
  contentProblem,
  withKind,
  hasEarlierRevisions,
  defaultReportPeriod,
  mergeReportPage,
  loadRemainingReport,
  journalPanels,
  defaultAttendancePeriod,
  attendanceProblem,
  attendanceText,
  changeAttendance,
  initials,
  lateShare,
  lessonsText,
  noAbsentees,
  withNoAbsentees,
  type JournalContent,
  type JournalDocument,
  type PupilRecord,
  type JournalReport,
  type JournalReportPage,
  type JournalRow,
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
    expect(csv).toContain('"Тест";"0";"10";"0";"ФО"');
    expect(csv).toContain('"Участие";"A*";"";"";"ФО"');
    expect(csv.split("\r\n")[0]).toContain(
      '"Вес";"Категория";"Комментарий к оценке"',
    );
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
  it("exports the category name from the school's catalog", () => {
    const d = structuredClone(document);
    d.content.assessments[0].category = "sor";
    journalCsv(documentRows(d, "key", 2), "j.csv", {
      version: 1,
      defaultCategory: "fo",
      categories: [
        {
          id: "sor",
          name: "СОР",
          title: "Суммативное оценивание за раздел",
          color: "#d97706",
          calendar: "summative_unit",
          active: true,
        },
      ],
    });
    const csv = vi.mocked(download).mock.lastCall![1];
    expect(csv).toContain('"Тест";"0";"10";"0";"СОР"');
    expect(csv).toContain('"Участие";"A*";"";"";"ФО"');
  });
  it("exports the leaving time and the absence type by the school's name", () => {
    const d = structuredClone(document);
    d.content.assessments = [];
    d.content.records[0].grades = [];
    const lastCells = (names?: Record<string, string>) => {
      journalCsv(documentRows(d, "key", 1), "j.csv", null, names);
      const [, row] = vi.mocked(download).mock.lastCall![1].split("\r\n");
      return row.split(";").slice(-2);
    };
    Object.assign(d.content.records[0], {
      attendance: "absent",
      lateMinutes: 0,
      absenceCode: "family",
    });
    expect(lastCells({ family: "Семейные обстоятельства" })).toEqual([
      '""',
      '"Семейные обстоятельства"',
    ]);
    expect(lastCells()).toEqual(['""', '"family"']);
    Object.assign(d.content.records[0], {
      attendance: "left",
      leftAt: "09:20",
      absenceCode: "",
    });
    expect(lastCells()).toEqual(['"09:20"', '""']);
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
  it("does not hide an invalid maximum or weight behind a text grade", () => {
    const points = {
      id: "w",
      name: "Работа",
      kind: "points" as const,
      maxScore: Number.NaN,
      weight: Number.NaN,
    };
    const label = withKind(points, "label");
    expect(label).toMatchObject({ kind: "label", maxScore: 10, weight: 1 });
    for (const maxScore of [0, -1, 10001]) {
      expect(withKind({ ...points, maxScore }, "label").maxScore).toBe(10);
    }
    expect(withKind({ ...points, weight: 101 }, "label").weight).toBe(1);
    // Valid values survive both ways, and points keep whatever the user typed.
    const kept = withKind({ ...points, maxScore: 5, weight: 0 }, "label");
    expect(kept).toMatchObject({ maxScore: 5, weight: 0 });
    expect(withKind(label, "points")).toMatchObject({
      kind: "points",
      maxScore: 10,
    });
    expect(withKind(points, "points").maxScore).toBeNaN();
    for (const a of [label, kept]) {
      const c = structuredClone(document.content);
      c.assessments = [a];
      expect(contentProblem(c)).toBe("");
    }
  });
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
describe("history paging", () => {
  const page = (count: number, newest: number) =>
    Array.from({ length: count }, (_, i) => ({ version: newest - i }));
  it("offers earlier revisions only after a full page that does not reach version 1", () => {
    expect(hasEarlierRevisions([])).toBe(false);
    expect(hasEarlierRevisions(page(49, 49))).toBe(false);
    expect(hasEarlierRevisions(page(50, 50))).toBe(false);
    expect(hasEarlierRevisions(page(50, 100))).toBe(true);
  });
});
describe("report period and paging", () => {
  it("opens on the last 30 days and the next 14, inside the school year", () => {
    const year = ["2026-08-17", "2027-06-11"] as const;
    expect(defaultReportPeriod("2026-10-23", ...year)).toEqual({
      start: "2026-09-23",
      end: "2026-11-06",
    });
    expect(defaultReportPeriod("2026-08-01", ...year)).toEqual({
      start: "2026-08-17",
      end: "2026-08-31",
    });
    expect(defaultReportPeriod("2027-07-01", ...year)).toEqual({
      start: "2027-05-12",
      end: "2027-06-11",
    });
    expect(defaultReportPeriod("2026-12-31", ...year)).toEqual({
      start: "2026-12-01",
      end: "2027-01-14",
    });
  });
  const row = (lessonKey: string, student = "s1"): JournalRow => ({
    ...documentRows(document, lessonKey, 1)[0],
    student: { id: student, name: "Ученик" },
  });
  const summary = (present: number) => ({
    attendance: { present },
    lateMinutes: 0,
    subjects: [],
  });
  const first = (nextAfter: string | null): JournalReport => ({
    rows: [row("a"), row("b")],
    summary: summary(3),
    nextAfter,
  });
  it("replaces the report with a first page and appends later pages without duplicates", () => {
    const old = first("x");
    const replaced = mergeReportPage(old, {
      rows: [row("z")],
      summary: summary(1),
      nextAfter: null,
    });
    expect(replaced.rows.map((r) => r.lessonKey)).toEqual(["z"]);
    expect(replaced.summary.attendance.present).toBe(1);
    const next = mergeReportPage(old, {
      rows: [row("b"), row("c"), row("c", "s2")],
      summary: null,
      nextAfter: "y",
    });
    expect(next.rows.map((r) => r.lessonKey + r.student.id)).toEqual([
      "as1",
      "bs1",
      "cs1",
      "cs2",
    ]);
    expect(next.summary).toBe(old.summary);
    expect(next.nextAfter).toBe("y");
    expect(old.rows).toHaveLength(2);
  });
  it("loads every remaining page for export and stops at the end", async () => {
    const pages: Record<string, JournalReportPage> = {
      a: { rows: [row("c")], summary: null, nextAfter: "b" },
      b: { rows: [row("d")], summary: null, nextAfter: null },
    };
    const fetchPage = vi.fn(async (after: string) => pages[after]);
    const all = await loadRemainingReport(first("a"), fetchPage);
    expect(fetchPage.mock.calls.map((c) => c[0])).toEqual(["a", "b"]);
    expect(all.rows.map((r) => r.lessonKey)).toEqual(["a", "b", "c", "d"]);
    expect(all.nextAfter).toBeNull();
    const done = first(null);
    expect(await loadRemainingReport(done, fetchPage)).toBe(done);
    expect(fetchPage).toHaveBeenCalledTimes(2);
  });
  it("refuses to download an endless period", async () => {
    const endless = vi.fn(async (after: string) => ({
      rows: [row(after)],
      summary: null,
      nextAfter: after + "x",
    }));
    await expect(loadRemainingReport(first("a"), endless, 3)).rejects.toThrow(
      "Слишком много записей: сократите период.",
    );
    expect(endless).toHaveBeenCalledTimes(3);
  });
});
describe("management panels", () => {
  it("lists only the panels the server enabled", () => {
    const names = (panels?: Parameters<typeof journalPanels>[0]["panels"]) =>
      journalPanels({ panels }).sort();
    const all = {
      courseTable: true,
      finalGrades: true,
      absences: true,
      attendance: true,
      attendanceGrid: true,
      coverage: true,
      periods: true,
    };
    expect(names(all)).toEqual([
      "absences",
      "attendance",
      "attendanceGrid",
      "courseTable",
      "coverage",
      "finalGrades",
      "periods",
    ]);
    expect(names({ coverage: true, periods: true })).toEqual([
      "coverage",
      "periods",
    ]);
    expect(names({ absences: true, coverage: false })).toEqual(["absences"]);
    expect(names({ absences: false })).toEqual([]);
    expect(names()).toEqual([]);
  });
});
describe("attendance opening period", () => {
  const year = ["2026-08-17", "2027-06-11"] as const;
  it("uses the current journal period, clamped to the school year", () => {
    expect(
      defaultAttendancePeriod("2026-10-15", ...year, {
        start: "2026-09-01",
        end: "2026-10-31",
      }),
    ).toEqual({ start: "2026-09-01", end: "2026-10-31" });
    expect(
      defaultAttendancePeriod("2026-08-20", ...year, {
        start: "2026-08-01",
        end: "2026-12-31",
      }),
    ).toEqual({ start: "2026-08-17", end: "2026-12-31" });
  });
  it("falls back to the whole month of today", () => {
    expect(defaultAttendancePeriod("2026-10-15", ...year)).toEqual({
      start: "2026-10-01",
      end: "2026-10-31",
    });
    expect(defaultAttendancePeriod("2026-11-15", ...year, null)).toEqual({
      start: "2026-11-01",
      end: "2026-11-30",
    });
  });
  it("clamps a day outside the year and handles a leap February", () => {
    expect(defaultAttendancePeriod("2026-07-01", ...year)).toEqual({
      start: "2026-08-17",
      end: "2026-08-31",
    });
    expect(defaultAttendancePeriod("2028-03-01", ...year)).toEqual({
      start: "2027-06-01",
      end: "2027-06-11",
    });
    expect(
      defaultAttendancePeriod("2028-02-10", "2027-08-17", "2028-06-11"),
    ).toEqual({ start: "2028-02-01", end: "2028-02-29" });
  });
});
describe("marks of a held lesson", () => {
  const record = (studentId: string, attendance: string, extra = {}) =>
    ({
      studentId,
      attendance,
      lateMinutes: 0,
      comment: "",
      grades: [],
      ...extra,
    }) as PupilRecord;
  const students = [
    { id: "a", name: "Первый", className: "10A", enrolled: true },
    { id: "b", name: "Второй", className: "10A", enrolled: true },
    { id: "old", name: "Прежний", className: "10A", enrolled: false },
  ];
  const content = (...records: PupilRecord[]) =>
    ({ ...document.content, records }) as JournalContent;
  it("sees nobody absent only while every pupil of the roster is present, unmarked or excused by a note", () => {
    const approvals = { b: { id: "notice", version: 1 } };
    const open = content(
      record("a", "present"),
      record("b", "absent"),
      record("old", "absent"),
    );
    expect(noAbsentees(open, students)).toBe(false);
    expect(noAbsentees(open, students, approvals)).toBe(true);
    expect(
      noAbsentees(
        content(
          record("a", "unmarked"),
          record("b", "late", { lateMinutes: 3 }),
        ),
        students,
      ),
    ).toBe(false);
    expect(
      noAbsentees(
        content(record("a", "unmarked"), record("b", "left")),
        students,
      ),
    ).toBe(false);
  });
  it("marks the unmarked present, or absent where a note covers the lesson, and leaves the rest alone", () => {
    const before = content(
      record("a", "unmarked"),
      record("b", "late", { lateMinutes: 7, comment: "Автобус" }),
      record("old", "unmarked"),
    );
    const marked = withNoAbsentees(before, students);
    expect(marked.records.map((r) => r.attendance)).toEqual([
      "present",
      "late",
      "unmarked",
    ]);
    expect(marked.records[1]).toBe(before.records[1]);
    const covered = withNoAbsentees(
      content(record("a", "unmarked")),
      students,
      {
        a: { id: "notice", version: 1 },
      },
    );
    expect(covered.records[0].attendance).toBe("absent");
    expect(before.records[0].attendance).toBe("unmarked");
  });
  it("drops what belongs to another status and keeps grades and comment", () => {
    const grades = [
      { assessmentId: "a", score: 4, label: "", comment: "Хорошо" },
    ];
    const late = record("a", "late", {
      lateMinutes: 9,
      comment: "Заметка",
      grades,
    });
    const left = changeAttendance(late, "left");
    expect(left).toMatchObject({
      attendance: "left",
      lateMinutes: 0,
      leftAt: null,
    });
    const gone = changeAttendance({ ...left, leftAt: "09:20" }, "absent");
    expect(gone.leftAt).toBeNull();
    const typed = changeAttendance(
      { ...gone, absenceCode: "illness" },
      "present",
    );
    expect(typed).toMatchObject({
      absenceCode: "",
      lateMinutes: 0,
      leftAt: null,
    });
    expect(typed.comment).toBe("Заметка");
    expect(typed.grades).toBe(grades);
    expect(changeAttendance(late, "late").lateMinutes).toBe(9);
    expect(
      changeAttendance({ ...gone, absenceCode: "illness" }, "absent")
        .absenceCode,
    ).toBe("illness");
  });
  it("counts lateness as a share of the lesson from the threshold and never more than the lesson", () => {
    expect(lateShare(10, "09:00", "09:40")).toBe(0.25);
    expect(lateShare(4, "09:00", "09:40")).toBe(0);
    expect(lateShare(5, "09:00", "09:40")).toBe(0.125);
    expect(lateShare(90, "09:00", "09:40")).toBe(1);
    expect(lateShare(10, "09:00", "09:00")).toBe(0);
    expect(lessonsText(0.25)).toBe("0,25 урока");
    expect(lessonsText(1)).toBe("1 урок");
    expect(lessonsText(3)).toBe("3 урока");
    expect(lessonsText(12)).toBe("12 уроков");
  });
  it("makes initials of the first two words and does not fail on an empty name", () => {
    expect(initials("Айдар Нурланов")).toBe("АН");
    expect(initials("айдар")).toBe("А");
    expect(initials("  Айдар   Нурланов Серикович")).toBe("АН");
    expect(initials("")).not.toBe("");
  });
  it("asks for a leaving time inside the lesson", () => {
    const withMark = (extra: Partial<PupilRecord>): JournalDocument => ({
      lesson: document.lesson,
      content: content(record("s1", "left", extra)),
    });
    expect(attendanceProblem(withMark({ leftAt: null }))).not.toBe("");
    expect(attendanceProblem(withMark({ leftAt: "09:00" }))).not.toBe("");
    expect(attendanceProblem(withMark({ leftAt: "09:40" }))).not.toBe("");
    expect(attendanceProblem(withMark({ leftAt: "09:20" }))).toBe("");
    expect(
      attendanceProblem({
        lesson: document.lesson,
        content: content(record("s1", "present")),
      }),
    ).toBe("");
  });
  it("describes a mark with its details and does not credit the teacher's type as an excuse", () => {
    const lesson = { start: "09:00", end: "09:40" };
    const names = { illness: "Болезнь" };
    const late = attendanceText(
      record("a", "late", { lateMinutes: 10 }),
      lesson,
    );
    expect(late).toContain("10");
    expect(late).toContain("25%");
    expect(
      attendanceText(record("a", "late", { lateMinutes: 3 }), lesson),
    ).not.toContain("%");
    expect(
      attendanceText(record("a", "left", { leftAt: "09:20" }), lesson),
    ).toContain("09:20");
    const typed = { absenceCode: "illness" };
    expect(
      attendanceText(record("a", "absent", typed), lesson, names),
    ).toContain(names.illness);
    expect(
      attendanceText(record("a", "excused", typed), lesson, names),
    ).not.toContain(names.illness);
  });
  it("exports the leaving time and the type of an absence", () => {
    const d = structuredClone(document);
    d.content.records[0] = record("s1", "absent", { absenceCode: "illness" });
    journalCsv(documentRows(d, "key", 1), "j.csv", null, {
      illness: "Болезнь",
    });
    expect(vi.mocked(download).mock.lastCall![1]).toContain('"Болезнь"');
    d.content.records[0] = record("s1", "left", { leftAt: "09:20" });
    journalCsv(documentRows(d, "key", 1));
    const row = vi.mocked(download).mock.lastCall![1].split("\r\n")[1];
    expect(row).toContain('"09:20"');
  });
  it("leaves the type column empty on a row a school note excused", () => {
    const d = structuredClone(document);
    d.content.records[0] = record("s1", "absent", { absenceCode: "family" });
    const approvals = { s1: { id: "notice", version: 2 } };
    journalCsv(documentRows(d, "key", 1, approvals), "j.csv", null, {
      family: "Семейные обстоятельства",
    });
    const csv = vi.mocked(download).mock.lastCall![1];
    expect(csv).toContain("Уважительная причина");
    expect(csv).not.toContain("Семейные обстоятельства");
    journalCsv(documentRows(d, "key", 1), "j.csv", null, {
      family: "Семейные обстоятельства",
    });
    expect(vi.mocked(download).mock.lastCall![1]).toContain(
      "Семейные обстоятельства",
    );
  });
  it("keeps the absence type names across later report pages", () => {
    const page = (extra: Partial<JournalReportPage>): JournalReportPage => ({
      rows: [],
      summary: null,
      nextAfter: null,
      ...extra,
    });
    const first = mergeReportPage(
      null,
      page({
        summary: {
          attendance: {},
          lateMinutes: 0,
          subjects: [],
        },
        nextAfter: "x",
        absenceCodes: { illness: "Болезнь" },
      }),
    );
    expect(mergeReportPage(first, page({})).absenceCodes).toEqual({
      illness: "Болезнь",
    });
    expect(
      mergeReportPage(first, page({ absenceCodes: { family: "Семья" } }))
        .absenceCodes,
    ).toEqual({ family: "Семья" });
  });
});

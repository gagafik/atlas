import { describe, it, expect, vi } from "vitest";
import {
  afterSave,
  applyPaste,
  cellText,
  chunks,
  commitCell,
  courseCsv,
  defaultCoursePeriod,
  editable,
  lessonBodies,
  nextCell,
  openCell,
  parseCell,
  parsePaste,
  type CoursePupil,
  type CourseTable,
  type Edit,
  type Edits,
  type SaveResponse,
} from "./courseTable";
import { download } from "./utils";
vi.mock("./utils", async (original) => ({
  ...(await original<typeof import("./utils")>()),
  download: vi.fn(),
}));

const lesson = (n: number, extra = {}) => ({
  lessonKey: `k${n}`,
  day: `2026-10-0${n}`,
  start: "09:00",
  end: "09:40",
  version: n,
  publishedVersion: null,
  editable: true,
  reason: "",
  rosterFrozen: false,
  hasUnpublished: false,
  withdrawn: false,
  openable: true,
  ...extra,
});
const pupil = (id: string, lessons: number[], extra = {}): CoursePupil => ({
  id,
  name: `Ученик ${id}`,
  classId: "c",
  className: "10A",
  from: null,
  to: null,
  lessons,
  marks: {},
  categories: [],
  sharePercentage: null,
  blocked: false,
  missing: [],
  excluded: [],
  rule: "",
  ...extra,
});
// Two lessons: the first has two works, the second (locked) one. p3 is off the roster of the first lesson.
const table = (): CourseTable => ({
  revision: 1,
  groupId: "g",
  group: "10A",
  subjectId: "s",
  subject: "Физика",
  scope: "course",
  canEdit: true,
  source: "draft",
  start: "2026-10-01",
  end: "2026-10-31",
  categories: [
    { id: "fo", name: "ФО", title: "", color: "#2563eb", active: true },
    { id: "sor", name: "СОР", title: "", color: "#d97706", active: true },
  ],
  lessons: [lesson(1), lesson(2, { editable: false, reason: "Закрыт" })],
  works: [
    {
      lesson: 0,
      id: "a",
      name: "Опрос",
      kind: "points",
      maxScore: 10,
      weight: 1,
      category: "fo",
    },
    {
      lesson: 0,
      id: "b",
      name: "Вывод",
      kind: "label",
      maxScore: 10,
      weight: 1,
      category: "fo",
    },
    {
      lesson: 1,
      id: "c",
      name: "Раздел",
      kind: "points",
      maxScore: 20,
      weight: 2,
      category: "sor",
    },
  ],
  pupils: [
    pupil("p1", [0, 1]),
    pupil("p2", [0, 1]),
    pupil("p3", [1]),
    pupil("p4", [0, 1]),
  ],
});

describe("parseCell", () => {
  const points = { kind: "points" as const, maxScore: 10 };
  it("accepts a comma decimal and rejects numbers the server would refuse", () => {
    expect(parseCell("8,5", points)).toEqual({ score: 8.5, label: "" });
    expect(parseCell(" 0 ", points)).toEqual({ score: 0, label: "" });
    for (const bad of ["8,5,1", "-1", "11", "abc", "1e1", "8.5.1"])
      expect(parseCell(bad, points)).toHaveProperty("error");
  });
  it("accepts the full score and refuses anything above it", () => {
    expect(parseCell("10", points)).toEqual({ score: 10, label: "" });
    expect(parseCell("10,0", points)).toEqual({ score: 10, label: "" });
    expect(parseCell("10,01", points)).toHaveProperty("error");
  });
  it("clears on empty input and keeps a text mark up to the limit", () => {
    expect(parseCell("  ", points)).toEqual({ score: null, label: "" });
    const label = { kind: "label" as const, maxScore: 10 };
    expect(parseCell(" зачёт ", label)).toEqual({
      score: null,
      label: "зачёт",
    });
    expect(parseCell("я".repeat(30), label)).toHaveProperty("label");
    expect(parseCell("я".repeat(31), label)).toHaveProperty("error");
  });
});

describe("pasting", () => {
  it("reads spreadsheet text: tabs, CRLF and the newline that ends it", () => {
    expect(parsePaste("5\t6\r\n7\t8\r\n")).toEqual([
      ["5", "6"],
      ["7", "8"],
    ]);
    expect(parsePaste("5\n6")).toEqual([["5"], ["6"]]);
    expect(parsePaste("")).toEqual([]);
    expect(parsePaste("5\t\t7\n")).toEqual([["5", "", "7"]]);
  });
  it("fills a column from the active cell and clips at the bottom edge", () => {
    const t = table();
    const { edits, skipped } = applyPaste(
      parsePaste("5\n6\n7\n8\n9\n"),
      { row: 1, col: 0 },
      t,
    );
    expect(edits.map((e) => [e.studentId, e.assessmentId, e.score])).toEqual([
      ["p2", "a", 5],
      ["p4", "a", 7],
    ]);
    // p3 is not on the roster of the first lesson, and two rows fall off the table.
    expect(skipped).toBe(3);
  });
  it("fills a matrix, never clears with a blank and skips the locked lesson", () => {
    const t = table();
    const { edits, skipped } = applyPaste(
      parsePaste("5\tхорошо\t12\n\t\t\n7\t\t3"),
      { row: 0, col: 0 },
      t,
    );
    expect(
      edits.map((e) => [e.studentId, e.assessmentId, e.score, e.label]),
    ).toEqual([
      ["p1", "a", 5, ""],
      ["p1", "b", null, "хорошо"],
    ]);
    // The locked lesson twice and p3, who is off the roster of the first lesson.
    expect(skipped).toBe(3);
  });
  it("flags a pasted value over the maximum instead of dropping it", () => {
    const { edits } = applyPaste([["11"]], { row: 0, col: 0 }, table());
    expect(edits).toHaveLength(1);
    expect(edits[0].error).toBeTruthy();
  });
});

describe("moving between cells", () => {
  const t = table();
  it("moves down on Enter and up on Shift+Enter", () => {
    expect(nextCell({ row: 0, col: 0 }, "Enter", t)).toEqual({
      row: 1,
      col: 0,
    });
    expect(nextCell({ row: 1, col: 0 }, "Shift+Enter", t)).toEqual({
      row: 0,
      col: 0,
    });
  });
  it("steps over cells off the roster and in locked lessons", () => {
    expect(nextCell({ row: 1, col: 0 }, "ArrowDown", t)).toEqual({
      row: 3,
      col: 0,
    });
    expect(nextCell({ row: 0, col: 0 }, "Tab", t)).toEqual({ row: 0, col: 1 });
    // The third column belongs to the locked lesson, so there is nowhere to go.
    expect(nextCell({ row: 0, col: 1 }, "Tab", t)).toEqual({ row: 0, col: 1 });
  });
  it("stays at the edges", () => {
    expect(nextCell({ row: 0, col: 0 }, "ArrowUp", t)).toEqual({
      row: 0,
      col: 0,
    });
    expect(nextCell({ row: 3, col: 0 }, "Enter", t)).toEqual({
      row: 3,
      col: 0,
    });
    expect(nextCell({ row: 0, col: 0 }, "Shift+Tab", t)).toEqual({
      row: 0,
      col: 0,
    });
  });
  it("offers nothing to a viewer", () => {
    const viewer = { ...t, canEdit: false };
    expect(editable(viewer.pupils[0], viewer.works[0], viewer)).toBe(false);
    expect(nextCell({ row: 0, col: 0 }, "Enter", viewer)).toEqual({
      row: 0,
      col: 0,
    });
  });
});

describe("typing into a cell", () => {
  // The saved mark of the first pupil in the first work.
  const marked = (score: number | null, rows = 0) => {
    const t = table();
    if (score !== null)
      t.pupils[0].marks = { "0": { score, label: "", comment: "" } };
    if (rows) t.pupils.unshift(pupil("p0", [0, 1]));
    return t;
  };
  const at = (t: CourseTable, row = 0, col = 0) => ({ row, col, table: t });
  it("makes an edit of text that differs from the saved mark and none of text that equals it", () => {
    const t = marked(8);
    const none: Edits = {};
    expect(commitCell(at(t), t, "8", none)).toBe(none);
    const typed = commitCell(at(t), t, "9", none);
    expect(Object.values(typed)).toMatchObject([
      { studentId: "p1", lessonKey: "k1", assessmentId: "a", score: 9 },
    ]);
    // Typing the saved value back withdraws the edit.
    expect(commitCell(at(t), t, "8", typed)).toEqual({});
    expect(cellText(t, { row: 0, col: 0 }, typed)).toBe("9");
  });
  it("does not turn what a refreshed table replaced into an edit", () => {
    // The mark was changed from 8 to 9 in the lesson editor and the table was refreshed while the cell still showed 8.
    const before = marked(8),
      after = marked(9);
    const none: Edits = {};
    expect(commitCell(at(before), after, "8", none)).toBe(none);
    // The same text on a cell opened after the refresh is what the teacher typed.
    expect(Object.keys(commitCell(at(after), after, "8", none))).toHaveLength(
      1,
    );
  });
  it("keeps the text of a cell off another pupil's row when the rows moved", () => {
    const before = marked(null),
      after = marked(null, 1);
    const none: Edits = {};
    expect(commitCell(at(before), after, "7", none)).toBe(none);
    expect(openCell(at(before), after)).toBeNull();
    expect(
      Object.values(commitCell(at(after), after, "7", none)),
    ).toMatchObject([{ studentId: "p0" }]);
  });
  it("keeps a cell open only on the table it was opened on", () => {
    const t = marked(null);
    expect(openCell(at(t, 1, 1), t)).toEqual({ row: 1, col: 1 });
    expect(openCell(at(t), marked(null))).toBeNull();
    expect(openCell(at(t), null)).toBeNull();
    expect(openCell(null, t)).toBeNull();
  });
  it("takes nothing for a cell off the roster, in a locked lesson or of a viewer", () => {
    const t = marked(null),
      none: Edits = {};
    // p3 is not on the roster of the first lesson; the third work belongs to the locked lesson.
    expect(commitCell(at(t, 2, 0), t, "5", none)).toBe(none);
    expect(commitCell(at(t, 0, 2), t, "5", none)).toBe(none);
    const viewer = { ...marked(null), canEdit: false };
    expect(commitCell(at(viewer), viewer, "5", none)).toBe(none);
  });
});

describe("save requests", () => {
  const edit = (
    studentId: string,
    lessonKey: string,
    assessmentId: string,
    extra = {},
  ): Edit => ({
    studentId,
    lessonKey,
    assessmentId,
    raw: "5",
    score: 5,
    label: "",
    ...extra,
  });
  it("groups valid edits by lesson with the version of the table", () => {
    const t = table();
    t.lessons[1].editable = true;
    const bodies = lessonBodies(
      t,
      [
        edit("p1", "k2", "c"),
        edit("p1", "k1", "a"),
        edit("p2", "k1", "a"),
        edit("p4", "k1", "a", { error: "x", score: null }),
      ],
      false,
    );
    expect(
      bodies.map((b) => [
        b.lessonKey,
        b.version,
        b.cells.map((c) => c.studentId),
      ]),
    ).toEqual([
      ["k1", 1, ["p1", "p2"]],
      ["k2", 2, ["p1"]],
    ]);
  });
  it("adds lessons with unpublished marks only when publishing", () => {
    const t = table();
    t.lessons[0].hasUnpublished = true;
    t.lessons[1].hasUnpublished = true;
    expect(lessonBodies(t, [], false)).toEqual([]);
    // The locked lesson cannot be published from here.
    expect(lessonBodies(t, [], true)).toEqual([
      { lessonKey: "k1", day: "2026-10-01", version: 1, cells: [] },
    ]);
  });
  it("leaves a lesson hidden from the families hidden unless it has an edit of its own", () => {
    const t = table();
    t.lessons[0].hasUnpublished = true;
    t.lessons[0].withdrawn = true;
    expect(lessonBodies(t, [], true)).toEqual([]);
    expect(lessonBodies(t, [edit("p1", "k1", "a")], true)).toEqual([
      {
        lessonKey: "k1",
        day: "2026-10-01",
        version: 1,
        cells: [{ studentId: "p1", assessmentId: "a", score: 5, label: "" }],
      },
    ]);
  });
  it("splits many lessons into requests of at most ten", () => {
    const parts = chunks(Array.from({ length: 25 }, (_, i) => i));
    expect(parts.map((p) => p.length)).toEqual([10, 10, 5]);
    expect(parts.flat()).toHaveLength(25);
  });
  it("drops the edits of saved and unchanged lessons and keeps the conflicting ones with the reason", () => {
    const response: SaveResponse = {
      saved: [{ lessonKey: "k1" }],
      unchanged: ["k2"],
      conflicts: [
        {
          lessonKey: "k3",
          day: "2026-10-03",
          status: 422,
          message: "Проверьте отмеченные ячейки.",
          cells: [
            {
              studentId: "p1",
              assessmentId: "a",
              message: "Баллы — от 0 до 10.",
            },
          ],
        },
        {
          lessonKey: "k4",
          day: "2026-10-04",
          status: 403,
          message: "Журнал закрыт",
          cells: [],
        },
      ],
    };
    const typed = [
      edit("p1", "k1", "a"),
      edit("p1", "k2", "a"),
      edit("p1", "k3", "a"),
      edit("p2", "k3", "a"),
      edit("p1", "k4", "a"),
      edit("p1", "k5", "a"),
      edit("p2", "k1", "a", { error: "x" }),
    ];
    const result = afterSave(typed, [response], typed);
    expect(result.saved).toBe(1);
    expect(result.unchanged).toBe(1);
    expect(
      result.edits.map((e) => [e.studentId, e.lessonKey, e.message]),
    ).toEqual([
      ["p1", "k3", "Баллы — от 0 до 10."],
      ["p2", "k3", "Проверьте отмеченные ячейки."],
      ["p1", "k4", "Журнал закрыт"],
      ["p1", "k5", undefined],
      ["p2", "k1", undefined],
    ]);
    expect(result.conflicts.map((c) => c.lessonKey)).toEqual(["k3", "k4"]);
  });
  it("keeps the edits of requests that never ran", () => {
    const typed = [edit("p1", "k1", "a"), edit("p1", "k9", "a")];
    const result = afterSave(
      typed,
      [{ saved: [{ lessonKey: "k1" }], unchanged: [], conflicts: [] }],
      typed,
    );
    expect(result.edits.map((e) => e.lessonKey)).toEqual(["k9"]);
  });
  it("keeps what was typed or changed while the requests were on their way", () => {
    const sent = [edit("p1", "k1", "a"), edit("p1", "k3", "a")];
    const now = [
      // Sent and unchanged, sent and typed over, and not sent at all, in a saved and in a conflicting lesson.
      edit("p1", "k1", "a"),
      edit("p2", "k1", "a"),
      edit("p3", "k1", "a", { raw: "9", score: 9 }),
      edit("p1", "k3", "a"),
      edit("p2", "k3", "a"),
    ];
    const result = afterSave(
      now,
      [
        {
          saved: [{ lessonKey: "k1" }],
          unchanged: [],
          conflicts: [
            {
              lessonKey: "k3",
              day: "2026-10-03",
              status: 409,
              message: "Журнал урока уже изменён.",
              cells: [],
            },
          ],
        },
      ],
      sent,
    );
    expect(
      result.edits.map((e) => [e.studentId, e.lessonKey, e.message]),
    ).toEqual([
      ["p2", "k1", undefined],
      ["p3", "k1", undefined],
      ["p1", "k3", "Журнал урока уже изменён."],
      ["p2", "k3", undefined],
    ]);
    const changed = afterSave(
      [edit("p1", "k1", "a", { raw: "7", score: 7 })],
      [{ saved: [{ lessonKey: "k1" }], unchanged: [], conflicts: [] }],
      [edit("p1", "k1", "a")],
    );
    expect(changed.edits.map((e) => e.raw)).toEqual(["7"]);
  });
});

describe("period", () => {
  const periods = [
    { id: "year", start: "2026-08-17", end: "2027-06-11" },
    { id: "q1", start: "2026-08-17", end: "2026-10-25" },
    { id: "q2", start: "2026-11-02", end: "2026-12-27" },
  ];
  it("takes the narrowest period that contains today", () => {
    expect(
      defaultCoursePeriod("2026-10-01", "2026-08-17", "2027-06-11", periods)
        .periodId,
    ).toBe("q1");
  });
  it("takes the latest period that has ended when today is between periods", () => {
    expect(
      defaultCoursePeriod(
        "2026-10-29",
        "2026-08-17",
        "2027-06-11",
        periods.slice(1),
      ),
    ).toEqual({ start: "2026-08-17", end: "2026-10-25", periodId: "q1" });
    // With several ended periods the latest one wins, whatever order they come in.
    for (const order of [periods.slice(1), periods.slice(1).reverse()])
      expect(
        defaultCoursePeriod("2026-12-30", "2026-08-17", "2027-06-11", order)
          .periodId,
      ).toBe("q2");
  });
  it("falls back to the year so far", () => {
    expect(
      defaultCoursePeriod("2026-09-10", "2026-08-17", "2027-06-11", []),
    ).toEqual({ start: "2026-08-17", end: "2026-09-10", periodId: null });
    expect(
      defaultCoursePeriod("2028-01-01", "2026-08-17", "2027-06-11", []),
    ).toEqual({ start: "2026-08-17", end: "2027-06-11", periodId: null });
  });
});

describe("CSV", () => {
  it("marks a column without a place for the pupil, escapes formulas and ends with the totals", () => {
    const t = table();
    t.pupils[0].marks = {
      0: { score: 0, label: "", comment: "" },
      1: { score: null, label: "=1+1", comment: "" },
    };
    t.pupils[0].categories = [
      { id: "fo", name: "ФО", color: "", numeric: 1, percentage: 0 },
    ];
    t.pupils[0].sharePercentage = 0;
    t.pupils[2].marks = {};
    courseCsv(t);
    const text = vi.mocked(download).mock.lastCall![1];
    // Excel reads the Cyrillic as UTF-8 only with the byte order mark.
    expect(text.startsWith("\uFEFF")).toBe(true);
    const lines = text.split("\r\n");
    expect(lines[0]).toContain('"01.10.2026 ФО Опрос (макс 10, вес 1)"');
    expect(lines[0].endsWith('"ФО %";"СОР %";"Итог по долям %"')).toBe(true);
    expect(lines[1]).toContain('"0";"\'=1+1";""');
    expect(lines[1].endsWith('"0";"";"0"')).toBe(true);
    // p3 has no place in the first lesson.
    expect(lines[3]).toContain('"—";"—";""');
    expect(lines).toHaveLength(5);
  });
});

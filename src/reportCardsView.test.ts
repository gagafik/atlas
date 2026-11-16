import { describe, expect, it } from "vitest";
import type {
  Book,
  ClassReport,
  Matrix,
  Period,
  Published,
  Row,
  Scale,
} from "./finalGradesTypes";
import {
  bookFor,
  cardCsvRows,
  cellLabel,
  childPeriods,
  classGrid,
  classRows,
  classState,
  courseRows,
  defaultPeriod,
  familyPeriod,
  finishedBook,
  gridGpa,
  hasPoints,
  pupilCardSource,
  rowActionAllowed,
  searchPupils,
  settingsLocked,
  statusMark,
  stateText,
  teacherPeriods,
  type Counts,
} from "./reportCardsView";

const period = (
  id: string,
  status = "closed",
  start = "2026-09-01",
): Period => ({
  id,
  name: id,
  version: 1,
  status,
  start,
  end: start,
});
const book = (id: string, p: Period): Book => ({
  id,
  period: p,
  closureVersion: 1,
  current: true,
});
const counts = (c: Partial<Counts> = {}): Counts => ({
  total: 10,
  draft: 0,
  submitted: 0,
  approved: 0,
  published: 0,
  proposed: 0,
  unassigned: 0,
  unavailable: 0,
  ...c,
});

describe("period and statement choice", () => {
  const q1 = period("q1"),
    q2 = period("q2"),
    q3 = period("q3", "open");
  it("opens at the newest period that has a statement", () => {
    expect(defaultPeriod([q3, q2, q1], [book("b1", q1)])).toBe("q1");
    expect(defaultPeriod([q3, q2, q1], [book("b2", q2), book("b1", q1)])).toBe(
      "q2",
    );
  });
  it("falls back to the newest closed period, then to the first, then to nothing", () => {
    expect(defaultPeriod([q3, q2, q1], [])).toBe("q2");
    expect(defaultPeriod([q3], [])).toBe("q3");
    expect(defaultPeriod([], [])).toBe("");
  });
  it("takes the newest statement of a period unless an older one of it is picked", () => {
    const books = [book("new", q1), book("other", q2), book("old", q1)];
    expect(bookFor("q1", books)?.id).toBe("new");
    expect(bookFor("q1", books, "old")?.id).toBe("old");
  });
  it("ignores a pick that belongs to another period", () => {
    const books = [book("a", q1), book("b", q2)];
    expect(bookFor("q1", books, "b")?.id).toBe("a");
    expect(bookFor("q3", books, "b")).toBeUndefined();
  });
  it("lists the periods of a teacher once each, newest first", () => {
    const early = period("early", "closed", "2026-01-01");
    const late = period("late", "closed", "2026-11-01");
    const middle = period("middle", "closed", "2026-09-01");
    const list = teacherPeriods(
      [book("b1", middle), book("b1b", middle)],
      [early, middle, late],
    );
    expect(list.map((p) => p.id)).toEqual(["late", "middle", "early"]);
  });
  it("reports only a statement that a finished job made and the list does not have yet", () => {
    const job = (periodId: string, status: string, bookId: string) => ({
      periodId,
      status,
      bookId,
    });
    expect(finishedBook([job("q1", "succeeded", "b9")], "q1", [])).toBe("b9");
    expect(
      finishedBook([job("q1", "succeeded", "b9")], "q1", [{ id: "b9" }]),
    ).toBe("");
    expect(finishedBook([job("q2", "succeeded", "b9")], "q1", [])).toBe("");
    expect(finishedBook([job("q1", "running", "")], "q1", [])).toBe("");
    expect(finishedBook([job("q1", "failed", "b9")], "q1", [])).toBe("");
  });
  it("reports the new statement of a period that closed again and already has an older one", () => {
    const jobs = [{ periodId: "q1", status: "succeeded", bookId: "new" }];
    expect(finishedBook(jobs, "q1", [{ id: "old" }])).toBe("new");
    expect(finishedBook(jobs, "q1", [{ id: "old" }, { id: "new" }])).toBe("");
  });
});

describe("periods of a family", () => {
  const newer = period("q2"),
    older = period("q1");
  const periods = [newer, older];
  const published = { a: ["q2", "q1"], b: ["q1"], c: [] as string[] };
  it("offers a child only the periods in which the child has a card", () => {
    expect(childPeriods(periods, published, "a").map((p) => p.id)).toEqual([
      "q2",
      "q1",
    ]);
    expect(childPeriods(periods, published, "b").map((p) => p.id)).toEqual([
      "q1",
    ]);
    expect(childPeriods(periods, published, "c")).toEqual([]);
  });
  it("offers every period when the server does not say which child has which", () => {
    expect(childPeriods(periods, undefined, "a")).toBe(periods);
  });
  it("opens each child at the own newest period, not at the newest of the family", () => {
    const of = (id: string) => childPeriods(periods, published, id);
    expect(familyPeriod(of("a"), null, "a")).toBe("q2");
    expect(familyPeriod(of("b"), null, "b")).toBe("q1");
    expect(familyPeriod(of("c"), null, "c")).toBe("");
  });
  it("keeps a period that was picked for this child and drops it for another child", () => {
    const picked = { pupilId: "a", periodId: "q1" };
    expect(
      familyPeriod(childPeriods(periods, published, "a"), picked, "a"),
    ).toBe("q1");
    expect(
      familyPeriod(childPeriods(periods, published, "b"), picked, "b"),
    ).toBe("q1");
    // A pick the child does not have is not kept.
    const other = { pupilId: "b", periodId: "q2" };
    expect(
      familyPeriod(childPeriods(periods, published, "b"), other, "b"),
    ).toBe("q1");
    expect(
      familyPeriod(childPeriods(periods, published, "a"), other, "a"),
    ).toBe("q2");
  });
});

describe("where a card of the print dialog comes from", () => {
  const report = (...ids: string[]) =>
    ({ pupils: ids.map((id) => ({ id })) }) as unknown as ClassReport;
  it("takes the card from the class report while the pupil is in the class", () => {
    expect(pupilCardSource(report("p1", "p2"), "p2")).toBe("class");
  });
  it("falls back to the pupil's own card when he has left the class", () => {
    expect(pupilCardSource(report("p1"), "gone")).toBe("personal");
  });
  it("has no card before the report arrives or before a pupil is chosen", () => {
    expect(pupilCardSource(undefined, "p1")).toBe("none");
    expect(pupilCardSource(report("p1"), "")).toBe("none");
  });
});

describe("settings of an archived year", () => {
  it("locks what changes the school and leaves the statements open to look through", () => {
    for (const tab of ["scales", "categories", "report"])
      expect(settingsLocked("2025", tab)).toBe(true);
    expect(settingsLocked("2025", "preparation")).toBe(false);
  });
  it("locks nothing in the current year", () => {
    for (const tab of ["scales", "categories", "report", "preparation"])
      expect(settingsLocked(undefined, tab)).toBe(false);
  });
});

describe("state of a class", () => {
  it("has no state without rows", () => {
    expect(classState(counts({ total: 0 })).kind).toBe("empty");
  });
  it("puts rows without a responsible person before everything else", () => {
    const s = classState(counts({ unassigned: 2, unavailable: 3, draft: 5 }));
    expect([s.kind, s.count]).toEqual(["unassigned", 5]);
  });
  it("counts what the teachers have handed in while drafts remain", () => {
    const s = classState(
      counts({ draft: 4, submitted: 3, approved: 2, published: 1 }),
    );
    expect([s.kind, s.done, s.total]).toEqual(["drafting", 6, 10]);
  });
  it("counts a saved draft as a result set, so a teacher who saved all grades sees them", () => {
    const s = classState(counts({ draft: 10, proposed: 7 }));
    expect([s.kind, s.done, s.total]).toEqual(["drafting", 7, 10]);
    // Never fewer than the rows already handed in.
    expect(
      classState(counts({ draft: 4, submitted: 6, proposed: 1 })).done,
    ).toBe(6);
  });
  it("then waits for approval, then for publication, then is published", () => {
    expect(classState(counts({ submitted: 2, approved: 8 })).kind).toBe(
      "waiting",
    );
    expect(classState(counts({ approved: 2, published: 8 })).kind).toBe(
      "ready",
    );
    expect(classState(counts({ published: 10 })).kind).toBe("published");
  });
  it("gives every state its own text", () => {
    const texts = [
      counts({ total: 0 }),
      counts({ unassigned: 1 }),
      counts({ draft: 1 }),
      counts({ submitted: 1 }),
      counts({ approved: 1 }),
      counts({ published: 10 }),
    ].map((c) => stateText(classState(c)));
    expect(new Set(texts).size).toBe(texts.length);
  });
});

describe("rows of the classes and courses tables", () => {
  const cell = (classId: string, subjectId: string, c: Partial<Counts>) => ({
    classId,
    subjectId,
    ...counts(c),
  });
  const matrix: Matrix = {
    book: book("b", period("q1")),
    classes: [
      { id: "c2", name: "2А", pupils: 4 },
      { id: "c10", name: "10А" },
    ],
    subjects: [
      { id: "s1", name: "Физика" },
      { id: "s2", name: "Алгебра" },
    ],
    cells: [
      cell("c10", "s1", { total: 3, published: 3 }),
      cell("c2", "s1", { total: 4, draft: 1, submitted: 3 }),
      cell("c2", "s2", { total: 4, approved: 4 }),
    ],
  };
  it("adds the subjects of each class up and keeps the order of the matrix", () => {
    const rows = classRows(matrix);
    expect(rows.map((r) => r.id)).toEqual(["c2", "c10"]);
    expect([rows[0].total, rows[0].draft, rows[0].approved]).toEqual([8, 1, 4]);
    expect(rows[0].pupils).toBe(4);
    expect(rows[0].state.kind).toBe("drafting");
    expect(rows[1].state.kind).toBe("published");
  });
  it("does not invent pupils when the server sends none", () => {
    expect(classRows(matrix)[1].pupils).toBe(0);
  });
  it("lists one course per cell by class and then by subject", () => {
    const rows = courseRows(matrix);
    expect(rows.map((r) => [r.className, r.subjectName])).toEqual([
      ["2А", "Алгебра"],
      ["2А", "Физика"],
      ["10А", "Физика"],
    ]);
  });
});

const scale = (withPoints: boolean): Scale => ({
  id: "sc",
  name: "Пятибалльная",
  version: 1,
  kind: "labels",
  rounding: "half_up",
  minCount: 1,
  active: true,
  bands: [
    { label: "5", minPercent: 85, points: withPoints ? "5" : null },
    { label: "4", minPercent: 65, points: withPoints ? 4 : null },
    { label: "3", minPercent: 40, points: withPoints ? "3.5" : null },
  ],
});
const row = (
  pupil: string,
  subject: string,
  over: Partial<Row> & {
    label?: string;
    outcome?: string;
    points?: boolean;
    pupilName?: string;
    subjectName?: string;
  } = {},
): Row => {
  const {
    label,
    outcome,
    points = true,
    pupilName,
    subjectName,
    ...rest
  } = over;
  return {
    id: `${pupil}-${subject}`,
    bookId: "b",
    studentId: pupil,
    subjectId: subject,
    classId: "c",
    assigned: "t",
    version: 1,
    status: "draft",
    proposal:
      label === undefined && outcome === undefined
        ? null
        : ({
            scale: scale(points),
            outcome: outcome || "graded",
            label: label || "",
            comment: "",
            explanation: "",
            calculation: {},
            author: "t",
          } as Row["proposal"]),
    published: null,
    source: {
      student: { id: pupil, name: pupilName || pupil, className: "c" },
      subject: subjectName || subject,
    } as Row["source"],
    ...rest,
  };
};

describe("class grid", () => {
  const rows = [
    row("p2", "algebra", { pupilName: "Примеров", subjectName: "Алгебра" }),
    row("p1", "algebra", { pupilName: "Образцов", subjectName: "Алгебра" }),
    row("p1", "physics", { pupilName: "Образцов", subjectName: "Физика" }),
    row("p1", "art", { pupilName: "Образцов", subjectName: "Музыка" }),
  ];
  it("puts pupils by name and subjects in the saved order, the rest by name", () => {
    const g = classGrid(rows, { order: ["physics"], hidden: [] });
    expect(g.pupils.map((p) => p.name)).toEqual(["Образцов", "Примеров"]);
    expect(g.subjects.map((s) => s.id)).toEqual(["physics", "algebra", "art"]);
  });
  it("puts hidden subjects last and flags them", () => {
    const g = classGrid(rows, {
      order: ["physics", "algebra"],
      hidden: ["physics"],
    });
    expect(g.subjects.map((s) => [s.id, s.hidden])).toEqual([
      ["algebra", false],
      ["art", false],
      ["physics", true],
    ]);
  });
  it("finds the row of a pupil and subject and says nothing for a missing one", () => {
    const g = classGrid(rows, { order: [], hidden: [] });
    expect(g.cell("p1", "physics")?.id).toBe("p1-physics");
    expect(g.cell("p2", "physics")).toBeUndefined();
  });
});

describe("cell text", () => {
  it("shows the proposal, else the published result, else a dash", () => {
    expect(cellLabel(row("p", "s", { label: "5" }))).toBe("5");
    const published = {
      outcome: "graded",
      label: "4",
    } as Published;
    expect(cellLabel(row("p", "s", { published }))).toBe("4");
    expect(cellLabel(row("p", "s", { label: "5", published }))).toBe("5");
    expect(cellLabel(row("p", "s"))).toBe("—");
  });
  it("shows an attestation outcome as its name", () => {
    const text = cellLabel(row("p", "s", { outcome: "exempt" }));
    expect(text).not.toBe("—");
    expect(text).not.toBe("");
  });
  it("tells every status apart in short and in full", () => {
    const marks = ["draft", "submitted", "approved", "published"].map(
      statusMark,
    );
    expect(new Set(marks.map((m) => m.short)).size).toBe(4);
    expect(new Set(marks.map((m) => m.full)).size).toBe(4);
  });
});

describe("average of the grid", () => {
  it("is the mean of the points of the graded proposals", () => {
    const rows = [row("p", "a", { label: "5" }), row("p", "b", { label: "4" })];
    expect(gridGpa(rows, [])).toBe("4,50");
  });
  it("leaves out rows without a grade, without points and in hidden subjects", () => {
    const rows = [
      row("p", "a", { label: "5" }),
      row("p", "b"),
      row("p", "c", { outcome: "exempt" }),
      row("p", "d", { label: "3", points: false }),
      row("p", "e", { label: "3" }),
    ];
    expect(gridGpa(rows, ["e"])).toBe("5,00");
  });
  it("rounds half up to hundredths", () => {
    // 5 + 5 + 4 = 14 over 3 is 4.666...
    expect(
      gridGpa(
        [
          row("p", "a", { label: "5" }),
          row("p", "b", { label: "5" }),
          row("p", "c", { label: "4" }),
        ],
        [],
      ),
    ).toBe("4,67");
    // 5 + 3.5 + 4 + 4 = 16.5 over 4 is 4.125, which rounds up.
    expect(
      gridGpa(
        [
          row("p", "a", { label: "5" }),
          row("p", "b", { label: "3" }),
          row("p", "c", { label: "4" }),
          row("p", "d", { label: "4" }),
        ],
        [],
      ),
    ).toBe("4,13");
  });
  it("is null when nothing is counted", () => {
    expect(gridGpa([], [])).toBeNull();
    expect(gridGpa([row("p", "a", { label: "5" })], ["a"])).toBeNull();
    expect(
      gridGpa([row("p", "a", { label: "5", points: false })], []),
    ).toBeNull();
  });
  it("knows whether any scale in use has points", () => {
    expect(hasPoints([row("p", "a", { label: "5" })])).toBe(true);
    expect(hasPoints([row("p", "a", { label: "5", points: false })])).toBe(
      false,
    );
    expect(hasPoints([row("p", "a")])).toBe(false);
  });
});

describe("search of a pupil", () => {
  const students = [
    { id: "3", name: "Макетов Данияр" },
    { id: "1", name: "образцов Данияр" },
    { id: "2", name: "Примерова Анна" },
  ];
  it("ignores case and gives nothing for an empty query", () => {
    expect(searchPupils(students, "ДАНИЯР").map((s) => s.id)).toEqual([
      "3",
      "1",
    ]);
    expect(searchPupils(students, "")).toEqual([]);
    expect(searchPupils(students, "   ")).toEqual([]);
  });
  it("stops at the limit", () => {
    const many = Array.from({ length: 20 }, (_, i) => ({
      id: String(i),
      name: `Ученик ${i}`,
    }));
    expect(searchPupils(many, "ученик")).toHaveLength(8);
    expect(searchPupils(many, "ученик", 3)).toHaveLength(3);
  });
});

describe("CSV of a personal report card", () => {
  it("has a header and one row per result of the same width", () => {
    const item = {
      student: { id: "p", name: "Образцов", className: "7А" },
      subject: "Алгебра",
      label: "5",
      outcome: "graded",
      comment: "",
      period: "q1",
      scale: { name: "Шкала", version: 2 },
      closureVersion: 3,
      publishedAt: "2026-10-01",
      current: true,
    } as Published;
    const table = cardCsvRows([item, { ...item, current: false }]);
    expect(table).toHaveLength(3);
    expect(table.every((r) => r.length === table[0].length)).toBe(true);
    expect(table[1]).toContain("Образцов");
    expect(cardCsvRows([])).toHaveLength(1);
  });
});

describe("actions on one row", () => {
  it("accepts an action only in the status the server expects", () => {
    expect(rowActionAllowed("approve", "submitted", true)).toBe(true);
    expect(rowActionAllowed("approve", "draft", true)).toBe(false);
    expect(rowActionAllowed("publish", "approved", true)).toBe(true);
    expect(rowActionAllowed("publish", "submitted", true)).toBe(false);
    expect(rowActionAllowed("reopen", "published", true)).toBe(true);
    expect(rowActionAllowed("return", "approved", true)).toBe(true);
    expect(rowActionAllowed("assign", "draft", true)).toBe(true);
    expect(rowActionAllowed("assign", "published", true)).toBe(false);
  });
  it("leaves only the withdrawal on a statement of an older closure", () => {
    expect(rowActionAllowed("approve", "submitted", false)).toBe(false);
    expect(rowActionAllowed("publish", "approved", false)).toBe(false);
    expect(rowActionAllowed("withdraw", "published", false)).toBe(true);
  });
});

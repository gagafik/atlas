import { describe, expect, it } from "vitest";
import {
  bandSegments,
  chartBlocks,
  defaultChoice,
  foldAttendance,
  groupNotes,
  marksByDate,
  mergeDay,
  monthCells,
  periodChoices,
  subjectRows,
  type BehaviourNote,
  type PupilAttendanceRow,
  type PupilMark,
  type PupilPeriod,
  type TimetableRow,
} from "./pupilJournal";
import type { JournalPeriodOption, JournalRow } from "./journal";

const period = (
  id: string,
  start: string,
  end: string,
  kind: JournalPeriodOption["kind"] = "quarter",
): JournalPeriodOption => ({ id, name: id, start, end, kind });

describe("periodChoices and defaultChoice", () => {
  const quarters = [
    period("q1", "2026-09-01", "2026-10-31"),
    period("q2", "2026-11-09", "2026-12-26"),
  ];
  it("adds the whole school year once, unless a year period already covers it", () => {
    const choices = periodChoices(quarters, "2026-09-01", "2027-05-25");
    expect(choices.map((c) => c.name)).toEqual([
      "q1",
      "q2",
      "Весь учебный год",
    ]);
    const covered = periodChoices(
      [...quarters, period("y", "2026-09-01", "2027-05-25", "year")],
      "2026-09-01",
      "2027-05-25",
    );
    expect(covered.some((c) => c.name === "Весь учебный год")).toBe(false);
  });
  it("keeps the whole-year choice inside the range the server answers", () => {
    const [year] = periodChoices([], "2026-01-01", "2028-01-01");
    expect(year.end).toBe("2027-01-02");
  });
  it("opens on the shortest period holding today, else the last ended one, else the year", () => {
    const withHalf = periodChoices(
      [period("s1", "2026-09-01", "2026-12-26", "semester"), ...quarters],
      "2026-09-01",
      "2027-05-25",
    );
    expect(defaultChoice(withHalf, "2026-10-05").id).toBe("q1");
    expect(defaultChoice(withHalf, "2026-11-02").id).toBe("s1");
    expect(defaultChoice(withHalf, "2027-02-01").id).toBe("q2");
    expect(
      defaultChoice(
        periodChoices(quarters, "2026-09-01", "2027-05-25"),
        "2027-02-01",
      ).id,
    ).toBe("q2");
    expect(
      defaultChoice(periodChoices([], "2026-09-01", "2027-05-25"), "2026-10-05")
        .name,
    ).toBe("Весь учебный год");
  });
});

const mark = (over: Partial<PupilMark> = {}): PupilMark => ({
  lessonKey: "k",
  date: "2026-10-05",
  start: "09:00",
  end: "09:45",
  subjectId: "math",
  subject: "Математика",
  teachers: "Иванова",
  assessmentId: "a",
  name: "Работа",
  kind: "points",
  category: "fo",
  maxScore: 10,
  weight: 1,
  score: 9,
  label: "",
  comment: "",
  unsigned: false,
  ...over,
});
const summaryOf = (subjectIds: string[]): PupilPeriod["summary"] => ({
  attendance: {},
  lateMinutes: 0,
  subjects: subjectIds.map((id) => ({
    subjectId: id,
    subject: id === "math" ? "Математика" : id === "art" ? "Изо" : "Английский",
    pointsCount: 1,
    labelCount: 0,
    percentage: 50,
    sharePercentage: id === "math" ? 88.5 : null,
    rule: "Доли",
  })),
});

describe("subjectRows", () => {
  const data = {
    summary: summaryOf(["math", "eng", "art"]),
    marks: [
      mark({ date: "2026-10-07", assessmentId: "b" }),
      mark({ date: "2026-10-05" }),
      mark({ subjectId: "eng", subject: "Английский" }),
    ],
  } as PupilPeriod;
  it("lists every subject of the period in the school's alphabet, with or without marks", () => {
    const rows = subjectRows(
      data,
      { math: { label: "5", outcome: "graded" } },
      [{ subjectId: "math", praise: 2, remark: 1 }],
    );
    expect(rows.map((r) => r.subject)).toEqual([
      "Английский",
      "Изо",
      "Математика",
    ]);
    const math = rows[2];
    expect(math.marks.map((m) => m.date)).toEqual(["2026-10-05", "2026-10-07"]);
    expect(math.final?.label).toBe("5");
    expect(math.notes).toEqual({ praise: 2, remark: 1 });
    expect(rows[1].marks).toEqual([]);
  });
  it("takes the school's share average as it is and leaves a blocked rule empty", () => {
    const rows = subjectRows(data, {}, []);
    expect(rows.find((r) => r.subjectId === "math")?.average).toBe(88.5);
    expect(rows.find((r) => r.subjectId === "eng")?.average).toBeNull();
  });
  it("orders the list by date, newest first", () => {
    const sorted = marksByDate(data.marks);
    expect(sorted.map((m) => m.date)).toEqual([
      "2026-10-07",
      "2026-10-05",
      "2026-10-05",
    ]);
  });
});

describe("chartBlocks", () => {
  it("gives each numeric work a share of the bar by its weight and maximum", () => {
    const { blocks, excluded } = chartBlocks([
      mark({ maxScore: 10, weight: 1, score: 9 }),
      mark({ assessmentId: "b", maxScore: 20, weight: 2, score: 10 }),
      mark({ assessmentId: "c", kind: "label", score: null, label: "зачёт" }),
      mark({ assessmentId: "d", weight: 0 }),
    ]);
    expect(blocks.map((b) => b.share)).toEqual([0.2, 0.8]);
    expect(blocks[0].text).toBe("9 / 10 = 90%");
    expect(blocks[1].percent).toBe(50);
    expect(excluded).toBe(2);
  });
  it("writes a fraction with a decimal comma", () => {
    expect(chartBlocks([mark({ score: 7.5 })]).blocks[0].text).toBe(
      "7,5 / 10 = 75%",
    );
  });
});

describe("bandSegments", () => {
  const scale = (kind: "thresholds" | "labels") => ({
    name: "Школьная",
    kind,
    bands: [
      { label: "5", minPercent: "80" },
      { label: "2", minPercent: "0" },
      { label: "4", minPercent: "60.0000" },
      { label: "3", minPercent: "40" },
    ],
  });
  it("orders the bands from the lowest and ends the last one at 100", () => {
    const segments = bandSegments(scale("thresholds"));
    expect(segments.map((s) => [s.label, s.from, s.to, s.tone])).toEqual([
      ["2", 0, 40, 0],
      ["3", 40, 60, 1],
      ["4", 60, 80, 2],
      ["5", 80, 100, 3],
    ]);
  });
  it("paints nothing for a labels scale or no scale", () => {
    expect(bandSegments(scale("labels"))).toEqual([]);
    expect(bandSegments(null)).toEqual([]);
  });
});

const record = (over: Partial<PupilAttendanceRow>): PupilAttendanceRow => ({
  lessonKey: "k",
  date: "2026-10-05",
  start: "09:30",
  end: "10:15",
  subjectId: "math",
  subject: "Математика",
  teachers: "",
  group: "",
  rooms: "",
  attendance: "present",
  lateMinutes: 0,
  leftAt: null,
  absenceCode: "",
  byNote: false,
  ...over,
});
const bells = () => [
  { label: "1", start: "09:30", end: "10:15" },
  { label: "2", start: "10:25", end: "11:10" },
];

describe("foldAttendance", () => {
  it("counts absence, excuse, lateness as a share of the lesson and early leave per day", () => {
    const { days } = foldAttendance(
      [
        record({ lessonKey: "a", attendance: "late", lateMinutes: 10 }),
        record({
          lessonKey: "b",
          start: "10:25",
          end: "11:10",
          attendance: "late",
          lateMinutes: 3,
        }),
        record({ lessonKey: "c", date: "2026-10-06", attendance: "absent" }),
        record({
          lessonKey: "d",
          date: "2026-10-06",
          start: "10:25",
          end: "11:10",
          attendance: "excused",
        }),
        record({ lessonKey: "e", date: "2026-10-07", attendance: "unmarked" }),
        record({
          lessonKey: "f",
          date: "2026-10-07",
          start: "10:25",
          end: "11:10",
          attendance: "left",
          leftAt: "10:50",
        }),
      ],
      bells,
    );
    expect(days.map((d) => d.date)).toEqual([
      "2026-10-05",
      "2026-10-06",
      "2026-10-07",
    ]);
    expect(days[0]).toMatchObject({ late: 2, lateShare: 0.22, absent: 0 });
    expect(days[1]).toMatchObject({ absent: 1, excused: 1, late: 0 });
    expect(days[2]).toMatchObject({ unmarked: 1, left: 1, absent: 0 });
  });
  it("puts each record in the column of its bell period and the strangers in one extra column", () => {
    const { columns, days } = foldAttendance(
      [
        record({ lessonKey: "a" }),
        record({ lessonKey: "b", start: "10:25", end: "11:10" }),
        record({ lessonKey: "c", start: "18:00", end: "18:45" }),
      ],
      bells,
    );
    expect(columns.map((c) => c.label)).toEqual(["1", "2", "Вне расписания"]);
    expect(days[0].cells.map((cell) => cell.map((r) => r.lessonKey))).toEqual([
      ["a"],
      ["b"],
      ["c"],
    ]);
  });
  it("keeps parallel lessons in one cell and needs no extra column without strangers", () => {
    const { columns, days } = foldAttendance(
      [
        record({ lessonKey: "a" }),
        record({ lessonKey: "b", subject: "Химия" }),
      ],
      bells,
    );
    expect(columns).toHaveLength(2);
    expect(days[0].cells[0]).toHaveLength(2);
  });
});

describe("monthCells", () => {
  it("is six Monday-first weeks around the month", () => {
    const cells = monthCells("2026-11-15");
    expect(cells).toHaveLength(42);
    expect(cells[0]).toEqual({ date: "2026-10-26", inMonth: false });
    expect(cells.findIndex((c) => c.date === "2026-11-01")).toBeLessThan(7);
    expect(cells.filter((c) => c.inMonth)).toHaveLength(30);
  });
});

const lesson = (over: Partial<TimetableRow>): TimetableRow => ({
  start: "08:30",
  end: "09:15",
  subject: "Математика",
  group: "",
  teachers: "",
  rooms: "",
  status: "",
  activity: "lesson",
  ...over,
});
const published = (key: string, subject: string, start: string, end: string) =>
  ({
    lessonKey: key,
    lesson: { subject, start, end, group: "", teachers: "", rooms: "" },
  }) as unknown as JournalRow;

describe("mergeDay", () => {
  it("joins a record to the timetable lesson with the same subject and start", () => {
    const record = published("k1", "Математика", "08:30", "09:15");
    const rows = mergeDay([lesson({})], [record]);
    expect(rows).toHaveLength(1);
    expect(rows[0].record).toBe(record);
  });
  it("shows a published lesson the timetable no longer has as a row of its own, in time order", () => {
    const rows = mergeDay(
      [lesson({})],
      [published("k2", "История", "10:30", "11:15")],
    );
    expect(rows.map((r) => [r.subject, r.record?.lessonKey ?? null])).toEqual([
      ["Математика", null],
      ["История", "k2"],
    ]);
  });
  it("matches a moved lesson of the same subject by overlap and keeps the cancelled lesson's record", () => {
    const record = published("k3", "Математика", "08:35", "09:20");
    const [row] = mergeDay([lesson({ status: "cancelled" })], [record]);
    expect(row.status).toBe("cancelled");
    expect(row.record).toBe(record);
  });
  it("lets each record match one lesson and ignores duties", () => {
    const rows = mergeDay(
      [
        lesson({}),
        lesson({ start: "09:25", end: "10:10" }),
        lesson({ activity: "duty", subject: "Дежурство" }),
      ],
      [published("k4", "Математика", "08:30", "09:15")],
    );
    expect(rows.map((r) => r.record?.lessonKey ?? null)).toEqual(["k4", null]);
  });
});

const note = (over: Partial<BehaviourNote>): BehaviourNote => ({
  id: "n",
  version: 1,
  kind: "remark",
  day: "2026-10-05",
  category: "Нарушение дисциплины",
  levelTitle: "Учитель",
  text: "",
  author: "Иванова",
  lesson: "",
  letter: false,
  letterNumber: "",
  requireAck: false,
  acknowledged: false,
  subjectId: "math",
  subject: "Математика",
  ...over,
});

describe("groupNotes", () => {
  const notes = [
    note({ id: "1" }),
    note({
      id: "2",
      subjectId: null,
      subject: null,
      kind: "praise",
      author: "Петров",
      day: "2026-10-06",
    }),
    note({ id: "3", subjectId: "art", subject: "Изо", author: "Петров" }),
  ];
  it("keeps notes without a subject in a group of their own, last", () => {
    const groups = groupNotes(notes, "subject");
    expect(groups.map((g) => g.title)).toEqual([
      "Изо",
      "Математика",
      "Без предмета",
    ]);
    expect(groups[2].items.map((n) => n.id)).toEqual(["2"]);
  });
  it("groups by teacher, date and type", () => {
    expect(
      groupNotes(notes, "teacher").map((g) => [g.title, g.items.length]),
    ).toEqual([
      ["Иванова", 1],
      ["Петров", 2],
    ]);
    expect(groupNotes(notes, "date").map((g) => g.title)).toEqual([
      "2026-10-06",
      "2026-10-05",
    ]);
    expect(groupNotes(notes, "type").map((g) => g.title)).toEqual([
      "Похвала",
      "Замечание",
    ]);
  });
});

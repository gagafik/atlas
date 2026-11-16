import { describe, expect, it } from "vitest";
import type { BellSlot, DayLesson } from "./journal";
import {
  NO_CLASS,
  SEARCH_LIMIT,
  classOfLesson,
  classSummaries,
  countFill,
  groupLabel,
  lessonAttendance,
  lessonRows,
  lessonsOfClass,
  searchLessons,
} from "./journalDay";

let counter = 0;
function lesson(
  classes: string[],
  over: Partial<DayLesson> & {
    start?: string;
    end?: string;
    subject?: string;
    group?: string;
    teachers?: string;
    status?: string;
  } = {},
): DayLesson {
  const { start, end, subject, group, teachers, status, ...rest } = over;
  const key = `lesson-${++counter}`;
  return {
    lessonKey: key,
    lesson: {
      date: "2026-11-16",
      start: start ?? "08:00",
      end: end ?? "08:40",
      subject: subject ?? "Математика",
      group: group ?? classes.join(", "),
      teachers: teachers ?? "Иванова А.",
      rooms: "101",
      status: status ?? "scheduled",
    },
    studentCount: 15,
    version: 0,
    publishedVersion: null,
    hasPublished: false,
    archived: false,
    profileId: null,
    classes,
    topic: "",
    homework: "",
    dueDate: null,
    present: null,
    marked: null,
    ...rest,
  } as DayLesson;
}
const published = { version: 2, publishedVersion: 2, hasPublished: true };

describe("countFill", () => {
  it("counts what the card shows as «Не заполнено» as unfilled and a draft or a published lesson as filled", () => {
    const count = countFill([
      lesson(["5А"]),
      lesson(["5А"], { version: 1 }),
      lesson(["5А"], published),
    ]);
    expect(count).toEqual({ lessons: 3, filled: 2, unfilled: 1 });
  });
  it("leaves a cancelled lesson out of every count", () => {
    const cancelled = lesson(["5А"], { status: "cancelled" });
    expect(countFill([cancelled, lesson(["5А"])])).toEqual({
      lessons: 1,
      filled: 0,
      unfilled: 1,
    });
  });
});

describe("classSummaries", () => {
  it("counts a lesson of a mixed group for every class it includes", () => {
    const mixed = lesson(["10А", "10Б"], { group: "Информатика 10" });
    const rows = classSummaries([mixed, lesson(["10А"], published)]);
    const byName = Object.fromEntries(rows.map((r) => [r.name, r]));
    expect(byName["10А"]).toMatchObject({ lessons: 2, filled: 1, unfilled: 1 });
    expect(byName["10Б"]).toMatchObject({ lessons: 1, filled: 0, unfilled: 1 });
  });
  it("puts 2А before 10А and lists the classes without lessons last", () => {
    const rows = classSummaries(
      [lesson(["10А"]), lesson(["2А"]), lesson(["9Б"])],
      ["10А", "2А", "9Б", "1А", "11Б"],
    );
    expect(rows.map((r) => r.name)).toEqual(["2А", "9Б", "10А", "1А", "11Б"]);
    expect(rows.find((r) => r.name === "1А")).toMatchObject({
      lessons: 0,
      state: "Уроков нет",
    });
  });
  it("names the state in words", () => {
    const rows = classSummaries([
      lesson(["5А"], published),
      lesson(["5Б"]),
      lesson(["5Б"]),
    ]);
    expect(rows.find((r) => r.name === "5А")?.state).toBe("Всё заполнено");
    expect(rows.find((r) => r.name === "5Б")?.state).toBe("Не заполнено: 2");
  });
  it("keeps a lesson that names no class in a row of its own, after the classes", () => {
    const rows = classSummaries([lesson([]), lesson(["5А"])], ["5А"]);
    expect(rows.map((r) => r.name)).toEqual(["5А", NO_CLASS]);
  });
  it("shows a class whose only lessons are cancelled as having no lessons", () => {
    const rows = classSummaries([lesson(["5А"], { status: "cancelled" })]);
    expect(rows[0]).toMatchObject({ name: "5А", state: "Уроков нет" });
  });
});

describe("lessonsOfClass and classOfLesson", () => {
  it("finds a mixed-group lesson in each of its classes", () => {
    const mixed = lesson(["10А", "10Б"]);
    const lessons = [mixed, lesson(["5А"])];
    expect(lessonsOfClass(lessons, "10Б")).toEqual([mixed]);
    expect(lessonsOfClass(lessons, "9А")).toEqual([]);
  });
  it("sends a deep link to the first class of the lesson in school order", () => {
    expect(classOfLesson(lesson(["10А", "2А"]))).toBe("2А");
    expect(classOfLesson(lesson([]))).toBe(NO_CLASS);
  });
});

describe("searchLessons", () => {
  it("matches the subject, the teacher and the class, ignoring case", () => {
    const math = lesson(["5А"], { subject: "Математика" });
    const art = lesson(["6А"], { subject: "Рисование", teachers: "Петров С." });
    expect(searchLessons([math, art], "рисов").rows).toEqual([art]);
    expect(searchLessons([math, art], "ПЕТРОВ").rows).toEqual([art]);
    expect(searchLessons([math, art], "5а").rows).toEqual([math]);
  });
  it("shows at most the limit and says how many more there are", () => {
    const many = Array.from({ length: SEARCH_LIMIT + 7 }, () =>
      lesson(["5А"], { subject: "Химия" }),
    );
    const result = searchLessons(many, "хим");
    expect(result.rows).toHaveLength(SEARCH_LIMIT);
    expect(result.more).toBe(7);
    expect(searchLessons(many.slice(0, 3), "хим").more).toBe(0);
  });
  it("lists the found lessons in time order", () => {
    const late = lesson(["5А"], { start: "11:00" });
    const early = lesson(["5А"], { start: "08:00" });
    expect(searchLessons([late, early], "мат").rows).toEqual([early, late]);
  });
});

describe("lessonRows", () => {
  const slots: BellSlot[] = [
    { label: "1", start: "08:00", end: "08:40" },
    { label: "2", start: "08:50", end: "09:30" },
  ];
  it("numbers the lessons by bell period in time order and leaves an off-bell lesson unnumbered", () => {
    const second = lesson(["5А"], { start: "08:50", end: "09:30" });
    const first = lesson(["5А"], { start: "08:00" });
    const odd = lesson(["5А"], { start: "15:00", end: "15:40" });
    const rows = lessonRows([second, odd, first], slots);
    expect(rows.map((r) => [r.lesson, r.number])).toEqual([
      [first, "1"],
      [second, "2"],
      [odd, ""],
    ]);
  });
});

describe("row text", () => {
  it("shows a group only when it is not the whole class", () => {
    expect(groupLabel(lesson(["5А"]))).toBe("");
    expect(groupLabel(lesson(["5А"], { group: "5А англ. 1" }))).toBe(
      "5А англ. 1",
    );
    expect(groupLabel(lesson([], { group: "Кружок" }))).toBe("");
  });
  it("says «не отмечено» until attendance is published", () => {
    expect(lessonAttendance(lesson(["5А"]))).toBe("не отмечено");
    expect(lessonAttendance(lesson(["5А"], { present: 14 }))).toBe(
      "присутствуют 14/15",
    );
  });
});

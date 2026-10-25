import { describe, it, expect, vi } from "vitest";
import {
  acceptable,
  inClasses,
  bulkItems,
  bulkSummary,
  nextLessonText,
  orderTargets,
  parallelBody,
  rosterCounts,
  targetDue,
  whileCurrent,
  type ParallelTarget,
  type RosterStudent,
} from "./homeworkBulk";

const pupil = (
  studentId: string,
  status: string,
  extra: Partial<RosterStudent> = {},
): RosterStudent => ({
  studentId,
  name: `Ученик ${studentId}`,
  status,
  late: false,
  assignmentChanged: false,
  version: 3,
  ...extra,
});
const roster = [
  pupil("a", "submitted", { version: 2 }),
  pupil("b", "accepted"),
  pupil("c", "returned"),
  pupil("d", "not_submitted", { version: 0 }),
  pupil("e", "submitted", { assignmentChanged: true }),
  pupil("f", "submitted", { version: 5 }),
];
const target = (
  group: string,
  extra: Partial<ParallelTarget> = {},
): ParallelTarget => ({
  groupId: `g-${group}`,
  group,
  classes: [group],
  parallel: false,
  lessonKey: `k-${group}`,
  day: "2026-08-17",
  start: "09:00",
  end: "09:45",
  teachers: "Учитель",
  version: 0,
  nextLessonDate: null,
  available: true,
  reason: "",
  ...extra,
});

describe("bulk acceptance", () => {
  it("counts every status, including works not submitted", () => {
    expect(rosterCounts(roster)).toEqual({
      submitted: 3,
      accepted: 1,
      returned: 1,
      notSubmitted: 1,
    });
    expect(rosterCounts([])).toEqual({
      submitted: 0,
      accepted: 0,
      returned: 0,
      notSubmitted: 0,
    });
  });
  it("offers only works sent to the current conditions", () => {
    expect(acceptable(roster).map((s) => s.studentId)).toEqual(["a", "f"]);
  });
  it("keeps roster order, versions and only ticked acceptable works", () => {
    expect(bulkItems(roster, new Set(["f", "a"]))).toEqual([
      { studentId: "a", version: 2 },
      { studentId: "f", version: 5 },
    ]);
    // Ticks for returned, accepted, changed or unknown works are stale.
    expect(bulkItems(roster, new Set(["b", "c", "d", "e", "zzz"]))).toEqual([]);
    expect(bulkItems(roster, new Set())).toEqual([]);
  });
  it("summarises accepted and skipped works", () => {
    expect(bulkSummary({ accepted: ["a", "b"], skipped: [] })).toBe(
      "Принято работ: 2.",
    );
    const text = bulkSummary({
      accepted: ["a"],
      skipped: [
        { studentId: "b", name: "Ученик b", reason: "Работа уже принята." },
        { studentId: "c", name: "Ученик c", reason: "Работа на доработке." },
      ],
    });
    expect(text).toBe(
      "Принято работ: 1. Не приняты: Ученик b — Работа уже принята; Ученик c — Работа на доработке.",
    );
  });
});

describe("next lesson", () => {
  it("formats the date and the start", () => {
    expect(
      nextLessonText({
        date: "2026-08-19",
        start: "09:00",
        searchedUntil: "2026-10-16",
      }),
    ).toBe("Следующий урок: 2026-08-19, 09:00. Срок можно изменить.");
    expect(
      nextLessonText({ date: "2026-08-19", searchedUntil: "2026-10-16" }),
    ).toBe("Следующий урок: 2026-08-19. Срок можно изменить.");
  });
  it("says where the search ended when there is no lesson", () => {
    expect(nextLessonText({ date: null, searchedUntil: "2026-10-16" })).toBe(
      "Следующий урок этого класса не найден до 2026-10-16. Укажите срок вручную.",
    );
  });
});

describe("parallel classes", () => {
  it("puts parallel classes first, even when their names sort later", () => {
    const ordered = orderTargets([
      target("10А"),
      target("10Я", { parallel: true }),
      target("10Б"),
    ]);
    expect(ordered.map((t) => t.group)).toEqual(["10Я", "10А", "10Б"]);
  });
  it("sorts by name in Russian collation order", () => {
    // Code points would put Ё before Е; Russian order puts it after.
    const ordered = orderTargets([target("Ж"), target("Ё"), target("Е")]);
    expect(ordered.map((t) => t.group)).toEqual(["Е", "Ё", "Ж"]);
    const classes = orderTargets([
      target("Б"),
      target("10Г"),
      target("А"),
      target("10Б", { parallel: true }),
      target("10А", { parallel: true }),
    ]);
    expect(classes.map((t) => t.group)).toEqual([
      "10А",
      "10Б",
      "10Г",
      "А",
      "Б",
    ]);
  });
  it("takes the due date from the lesson or from the class's next lesson", () => {
    const next = target("10D", { nextLessonDate: "2026-08-20" });
    expect(targetDue("same", "2026-08-19", next)).toBe("2026-08-19");
    expect(targetDue("same", null, next)).toBeNull();
    expect(targetDue("next", "2026-08-19", next)).toBe("2026-08-20");
    expect(targetDue("next", "2026-08-19", target("10E"))).toBeNull();
  });
  it("declines the noun after the count", () => {
    expect([1, 2, 5, 11, 21, 22].map(inClasses)).toEqual([
      "1 классе",
      "2 классах",
      "5 классах",
      "11 классах",
      "21 классе",
      "22 классах",
    ]);
  });
  it("builds the request from the chosen targets and the source version", () => {
    expect(
      parallelBody({ lessonKey: "src", day: "2026-08-17", version: 4 }, 7, [
        {
          lessonKey: "k1",
          day: "2026-08-17",
          version: 0,
          dueDate: "2026-08-20",
        },
        { lessonKey: "k2", day: "2026-08-18", version: 2, dueDate: null },
      ]),
    ).toEqual({
      lessonKey: "src",
      day: "2026-08-17",
      revision: 7,
      sourceVersion: 4,
      targets: [
        {
          lessonKey: "k1",
          day: "2026-08-17",
          version: 0,
          dueDate: "2026-08-20",
        },
        { lessonKey: "k2", day: "2026-08-18", version: 2, dueDate: null },
      ],
    });
  });
});

describe("late replies", () => {
  const pending = <T>() => {
    let resolve!: (value: T) => void, reject!: (error: Error) => void;
    const promise = new Promise<T>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    return { promise, resolve, reject };
  };
  it("applies a reply while the same lesson is still open", async () => {
    const apply = vi.fn(),
      fail = vi.fn();
    await whileCurrent(
      async () => "2026-08-19",
      () => true,
      apply,
      fail,
    );
    expect(apply).toHaveBeenCalledWith("2026-08-19");
    expect(fail).not.toHaveBeenCalled();
  });
  it("drops a reply that arrives after another lesson was opened", async () => {
    let open = 1;
    const made = open,
      apply = vi.fn(),
      fail = vi.fn(),
      request = pending<string>();
    const done = whileCurrent(
      () => request.promise,
      () => open === made,
      apply,
      fail,
    );
    open = 2;
    request.resolve("2026-08-19");
    await done;
    expect(apply).not.toHaveBeenCalled();
    expect(fail).not.toHaveBeenCalled();
  });
  it("shows an error only for the lesson it was made for", async () => {
    let open = 1;
    const made = open,
      apply = vi.fn(),
      fail = vi.fn();
    await whileCurrent(
      () => Promise.reject(new Error("нет связи")),
      () => open === made,
      apply,
      fail,
    );
    expect(fail).toHaveBeenCalledWith(new Error("нет связи"));
    open = 2;
    await whileCurrent(
      () => Promise.reject(new Error("поздно")),
      () => open === made,
      apply,
      fail,
    );
    expect(fail).toHaveBeenCalledTimes(1);
    expect(apply).not.toHaveBeenCalled();
  });
});

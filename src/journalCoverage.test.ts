import { describe, it, expect } from "vitest";
import {
  classCardLines,
  drillFilters,
  ruDate,
  runLine,
  schoolDays,
  summaryFilters,
  weekdays,
  type ClassSummary,
  type Query,
} from "./journalCoverage";

const card: ClassSummary = {
  classId: "c1",
  className: "10A",
  classTeacher: null,
  expected: 4,
  unfilled: 0,
  rosterUnknown: 0,
  lastRecord: null,
  unfilledSince: null,
  unbound: 0,
};

describe("schoolDays", () => {
  it("declines the Russian plural", () => {
    expect(schoolDays(1)).toBe("1 учебный день");
    expect(schoolDays(2)).toBe("2 учебных дня");
    expect(schoolDays(4)).toBe("4 учебных дня");
    expect(schoolDays(5)).toBe("5 учебных дней");
    expect(schoolDays(10)).toBe("10 учебных дней");
    expect(schoolDays(11)).toBe("11 учебных дней");
    expect(schoolDays(14)).toBe("14 учебных дней");
    expect(schoolDays(21)).toBe("21 учебный день");
    expect(schoolDays(22)).toBe("22 учебных дня");
  });
});

describe("weekdays", () => {
  it("lists the seven days from Monday, as the server counts them", () => {
    expect(weekdays).toHaveLength(7);
    expect(weekdays[0]).toBe("Понедельник");
    expect(weekdays[4]).toBe("Пятница");
    expect(weekdays[6]).toBe("Воскресенье");
  });
});

describe("ruDate", () => {
  it("writes dd.mm.yyyy and leaves anything else alone", () => {
    expect(ruDate("2026-08-17")).toBe("17.08.2026");
    expect(ruDate("")).toBe("");
  });
});

describe("classCardLines", () => {
  it("says what is empty in words", () => {
    expect(classCardLines(card)).toEqual([
      "Классный руководитель: не назначен",
      "Записей за период нет",
      "Незаполненных уроков нет",
      "Не заполнено уроков: 0 из 4",
    ]);
  });
  it("adds the warnings only when there is something to warn about", () => {
    const lines = classCardLines({
      ...card,
      classTeacher: "Иванова А.",
      unfilled: 2,
      lastRecord: "2026-08-14",
      unfilledSince: "2026-08-17",
      unbound: 1,
      rosterUnknown: 3,
    });
    expect(lines).toContain("Классный руководитель: Иванова А.");
    expect(lines).toContain("Последняя запись: 14.08.2026");
    expect(lines).toContain("Не заполнено с: 17.08.2026");
    expect(lines).toContain(
      "Некому заполнить (у учителя нет учётной записи): 1",
    );
    expect(lines).toContain("Численность класса не подтверждена: 3");
  });
});

describe("runLine", () => {
  it("describes sent, skipped and postponed runs", () => {
    expect(
      runLine({
        kind: "lessons",
        onDate: "2026-08-19",
        detail: { lessons: 2, sent: 1, skipped: 1 },
      }),
    ).toBe(
      "19.08.2026 · Напоминания по урокам: уроков: 2, уведомлений: 1, без учётной записи: 1",
    );
    expect(
      runLine({
        kind: "lessons",
        onDate: "2026-08-22",
        detail: { note: "not a school day" },
      }),
    ).toBe(
      "22.08.2026 · Напоминания по урокам: не учебный день, напоминания перенесены",
    );
    expect(
      runLine({
        kind: "digest",
        onDate: "2026-08-21",
        detail: { lessons: 3, sent: 2, withoutAccount: 1 },
      }),
    ).toBe(
      "21.08.2026 · Недельная сводка: уроков: 3, уведомлений: 2, учителей без учётной записи: 1",
    );
  });
});

describe("summaryFilters and drillFilters", () => {
  const form: Query = {
    start: "2026-08-17",
    end: "2026-08-23",
    mode: "teaching",
    classId: "c-demo-d",
    teacherId: "t2",
    subjectId: "sub-eng",
    status: "complete",
  };
  it("counts every teacher of every class, keeping the period and the subject", () => {
    expect(summaryFilters(form)).toEqual({
      ...form,
      mode: "classes",
      classId: "",
      teacherId: "",
      status: "all",
    });
  });
  it("lists the lessons a card counted: a teacher picked in the form is not applied", () => {
    const drill = drillFilters(form, "c-demo-a");
    expect(drill.teacherId).toBe("");
    expect(drill).toEqual({
      ...summaryFilters(form),
      classId: "c-demo-a",
      status: "issues",
    });
  });
  it("does not change the form it is built from", () => {
    const before = { ...form };
    drillFilters(form, "c-demo-a");
    expect(form).toEqual(before);
  });
});

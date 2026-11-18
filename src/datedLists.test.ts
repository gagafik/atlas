import { describe, expect, it } from "vitest";
import {
  REPORT_COLUMNS,
  absenceRows,
  absenceTime,
  breakdownLines,
  changeColumns,
  changePage,
  changeRow,
  changeRows,
  changeTypeOptions,
  dateShort,
  filterChanges,
  periodText,
  type ChangeNames,
} from "./datedLists";
import type { DatedLesson, TeacherAbsence } from "./types";
import type { ReportTeacher } from "./substitutions";

const names: ChangeNames = {
  group: (id) => ({ g1: "7А" })[id as "g1"] ?? "",
  subject: (id) => ({ s1: "Математика" })[id as "s1"] ?? "",
  teacher: (id) => ({ t1: "Ирина Ветрова" })[id as "t1"] ?? "",
  placement: (id) =>
    id === "p1" ? { groupId: "g1", subjectId: "s1" } : undefined,
};

const change = (over: Partial<DatedLesson> = {}): DatedLesson => ({
  id: "c1",
  name: "Замена",
  kind: "change",
  onDate: "2026-11-02",
  sourceDate: "2026-11-02",
  placementId: null,
  groupId: "g1",
  subjectId: "s1",
  startTime: null,
  endTime: null,
  teacherIds: null,
  roomIds: null,
  ...over,
});

describe("dates and times in words", () => {
  it("a day carries its weekday, a Sunday too", () => {
    expect(dateShort("2026-11-02")).toBe("пн, 02.11.2026");
    expect(dateShort("2026-11-08")).toBe("вс, 08.11.2026");
  });
  it("a period is two dates, equal ends are one day", () => {
    expect(periodText("2026-11-02", "2026-11-05")).toBe(
      "02.11.2026 — 05.11.2026",
    );
    expect(periodText("2026-11-02", "2026-11-02")).toBe("пн, 02.11.2026");
  });
  it("a text that is not a date is left as it was", () => {
    expect(dateShort("скоро")).toBe("скоро");
  });
  it("the whole day or the hours", () => {
    expect(absenceTime("00:00", "23:59")).toBe("весь день");
    expect(absenceTime("08:00", "12:00")).toBe("каждый день 08:00–12:00");
  });
});

describe("absence rows", () => {
  const absence = (id: string, start: string): TeacherAbsence => ({
    id,
    name: "Больничный",
    teacherId: "t1",
    start,
    end: start,
    startTime: "00:00",
    endTime: "23:59",
  });
  it("lists the newest first and names the teacher in every button", () => {
    const rows = absenceRows(
      [absence("a", "2026-11-02"), absence("b", "2026-11-09")],
      () => "Ирина Ветрова",
    );
    expect(rows.map((r) => r.view.id)).toEqual(["b", "a"]);
    expect(rows[1].view.period).toBe("пн, 02.11.2026");
    expect(rows[1].view.labels.remove).toBe(
      "Удалить отсутствие: Ирина Ветрова, 02.11.2026",
    );
  });
});

describe("change rows", () => {
  it("says which lesson it is: group, subject, time, substitute", () => {
    const v = changeRow(
      change({ startTime: "08:30", endTime: "09:15", teacherIds: ["t1"] }),
      names,
    );
    expect(v.resolved).toBe(true);
    expect(v.lesson).toBe(
      "7А · Математика · 08:30–09:15 · учитель: Ирина Ветрова",
    );
  });
  it("finds the lesson through the placement when the record has no group", () => {
    const v = changeRow(
      change({ groupId: null, subjectId: null, placementId: "p1" }),
      names,
    );
    expect(v.lesson).toBe("7А · Математика");
  });
  it("a lesson that cannot be found is said so", () => {
    const v = changeRow(change({ groupId: "gone", subjectId: null }), names);
    expect(v.resolved).toBe(false);
    expect(v.lesson).toBe("Занятие не определено");
  });
  it("a name equal to the type is hidden, another one stays", () => {
    expect(changeRow(change(), names).name).toBe("");
    expect(changeRow(change({ name: "Болезнь учителя" }), names).name).toBe(
      "Болезнь учителя",
    );
  });
  it("names the app generates itself are hidden, whatever the type", () => {
    for (const name of [
      "Замена / перенос",
      "Отмена урока",
      "Дополнительный урок",
    ])
      expect(changeRow(change({ name }), names).name).toBe("");
    const v = changeRow(
      change({ name: "Замена", changeType: "self_study" }),
      names,
    );
    expect(v.name).toBe("");
    expect(v.typeText).toBe("Самостоятельная работа");
  });
  it("a source date shows only when it differs, and the column follows", () => {
    const same = changeRow(change({ sourceDate: "2026-11-02" }), names);
    const moved = changeRow(
      change({ id: "c2", sourceDate: "2026-10-30", changeType: "move" }),
      names,
    );
    expect(same.sourceDate).toBeNull();
    expect(moved.sourceDate).toBe("2026-10-30");
    expect(changeColumns([same]).source).toBe(false);
    expect(changeColumns([same, moved]).source).toBe(true);
  });
});

describe("filters and pages of changes", () => {
  const list = [
    change({ id: "1", onDate: "2026-11-01", sourceDate: "2026-11-01" }),
    change({ id: "2", onDate: "2026-11-03", kind: "cancel", name: "Отмена" }),
    change({ id: "3", name: "Болезнь" }),
  ];
  const rows = changeRows(list, names);
  it("newest first", () => {
    expect(rows.map((r) => r.change.id)).toEqual(["2", "3", "1"]);
  });
  it("searches the lesson, the type and the name", () => {
    const ids = (search: string) =>
      filterChanges(rows, { search, type: "" }).map((r) => r.change.id);
    expect(ids("математ")).toHaveLength(3);
    expect(ids("отмена")).toEqual(["2"]);
    expect(ids("болезн")).toEqual(["3"]);
  });
  it("filters by type and offers only the types that occur", () => {
    expect(
      filterChanges(rows, { search: "", type: "cancel" }).map(
        (r) => r.change.id,
      ),
    ).toEqual(["2"]);
    expect(
      changeTypeOptions(rows.map((r) => r.view)).map((t) => t.type),
    ).toEqual(["substitution", "cancel"]);
  });
  it("cuts 30 rows to a page and keeps a wrong page in range", () => {
    const many = Array.from({ length: 65 }, (_, i) => i);
    expect(changePage(many, 0)).toHaveLength(30);
    expect(changePage(many, 2)).toHaveLength(5);
    expect(changePage(many, 9)).toHaveLength(5);
  });
});

describe("report columns", () => {
  const t: ReportTeacher = {
    planned: 20,
    held: 14,
    own: 12,
    substitutions: 2,
    extra: 0,
    missed: 6,
    missedAbsent: 4,
    cancelled: 2,
    moved: 1,
    byType: { substitution: 2 },
  };
  it("keep the order of the former cards and default missing figures to 0", () => {
    expect(REPORT_COLUMNS.map((c) => c.title)).toEqual([
      "По расписанию",
      "Проведено",
      "Замены за других",
      "Присоединено",
      "Пропущено",
      "Перенесено",
      "Предстоит",
    ]);
    expect(REPORT_COLUMNS.map((c) => c.value(t))).toEqual([
      20, 14, 2, 0, 6, 1, 0,
    ]);
  });
  it("keep the breakdowns of the cards", () => {
    const lines = breakdownLines(t);
    expect(lines).toContain(
      "Проведено: свои 12, за других 2, дополнительные 0",
    );
    expect(lines).toContain("Замены за других: Замена: 2");
    expect(lines).toContain(
      "Пропущено: при отсутствии 4, без замены 0, отменено 2",
    );
  });
  it("leaves out the substitution types line when there are none", () => {
    expect(
      breakdownLines({ ...t, byType: {} }).some((l) =>
        l.startsWith("Замены за других"),
      ),
    ).toBe(false);
  });
});

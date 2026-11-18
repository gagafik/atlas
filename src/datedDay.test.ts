import { describe, expect, it } from "vitest";
import {
  DAY_TABS,
  dateInWords,
  dayRowView,
  nextDayTab,
  rowActions,
  stateTone,
  type DayRowFacts,
} from "./datedDay";

const lesson = (over: Partial<DayRowFacts> = {}): DayRowFacts => ({
  changeId: null,
  status: "scheduled",
  teachers: "Ирина Ветрова",
  rooms: "201",
  start: "08:30",
  end: "09:15",
  subject: "Математика",
  group: "7А",
  ...over,
});

describe("what a lesson row offers", () => {
  it("a planned lesson can be changed or cancelled, nothing to remove", () => {
    expect(rowActions(lesson())).toEqual(["change", "cancel"]);
  });
  it("a changed lesson can also have its change removed", () => {
    expect(rowActions(lesson({ status: "changed", changeId: "c1" }))).toEqual([
      "change",
      "cancel",
      "remove",
    ]);
  });
  it("a cancelled lesson only offers to remove the cancellation", () => {
    expect(rowActions(lesson({ status: "cancelled", changeId: "c2" }))).toEqual(
      ["remove"],
    );
  });
  it("a cancelled lesson with no record of its own offers nothing", () => {
    expect(rowActions(lesson({ status: "cancelled" }))).toEqual([]);
  });
  it("a lesson with a commitment opens the event record and is not edited here", () => {
    expect(rowActions(lesson({ commitmentId: "k1" }))).toEqual(["commitment"]);
    expect(
      rowActions(
        lesson({ commitmentId: "k1", changeId: "c3", status: "changed" }),
      ),
    ).toEqual(["commitment", "remove"]);
  });
  it("every button name says which lesson it is about", () => {
    const view = dayRowView(lesson(), false);
    expect(view.actions.map((a) => a.label)).toEqual([
      "Заменить / перенести: Математика · 7А, 08:30",
      "Отменить урок: Математика · 7А, 08:30",
    ]);
    expect(view.actions.map((a) => a.text)).toEqual([
      "Заменить / перенести",
      "Отменить урок",
    ]);
  });
});

describe("the state of a row", () => {
  it("is quiet when the lesson is as planned", () => {
    const v = dayRowView(lesson(), false);
    expect(v.state).toBe("По расписанию");
    expect(v.tone).toBe("quiet");
    expect(v.struck).toBe(false);
  });
  it("is a change for a substitution, a move and a one-off lesson", () => {
    const sub = dayRowView(
      lesson({ status: "changed", changeType: "substitution" }),
      false,
    );
    expect([sub.state, sub.tone]).toEqual(["Замена", "change"]);
    const move = dayRowView(
      lesson({ status: "changed", changeType: "move" }),
      false,
    );
    expect([move.state, move.tone]).toEqual(["Перенос", "change"]);
    const added = dayRowView(lesson({ status: "added" }), false);
    expect([added.state, added.tone]).toEqual(["Разовый урок", "change"]);
  });
  it("is cancelled and struck for a cancelled lesson, even if it carries a change type", () => {
    const v = dayRowView(
      lesson({ status: "cancelled", changeType: "cancel", changeId: "c4" }),
      false,
    );
    expect(v.tone).toBe("cancelled");
    expect(v.struck).toBe(true);
    expect(v.state).toBe("Отмена");
  });
  it("shows a lesson the chosen class does not attend as cancelled", () => {
    const v = dayRowView(
      lesson({ status: "changed", changeType: "move" }),
      true,
    );
    expect([v.state, v.tone, v.struck]).toEqual(["Отменён", "cancelled", true]);
  });
  it("keeps bookings and duties quiet", () => {
    expect(stateTone(lesson({ activity: "booking" }), false)).toBe("quiet");
    expect(dayRowView(lesson({ activity: "duty" }), false).state).toBe(
      "Дежурство",
    );
  });
});

describe("a row's texts", () => {
  it("says plainly when there is no teacher or no room", () => {
    const v = dayRowView(lesson({ teachers: "", rooms: "" }), false);
    expect(v.teacher).toBe("Учитель не назначен");
    expect(v.teacherMissing).toBe(true);
    expect(v.room).toBe("Кабинет не назначен");
    expect(v.roomMissing).toBe(true);
  });
  it("shows the teacher, the room, the time and the lesson as they are", () => {
    const v = dayRowView(lesson(), false);
    expect(v.teacher).toBe("Ирина Ветрова");
    expect(v.teacherMissing).toBe(false);
    expect(v.room).toBe("201");
    expect(v.time).toBe("08:30–09:15");
    expect(v.lesson).toBe("Математика · 7А");
  });
});

describe("the date in words", () => {
  it("names the weekday", () => {
    expect(dateInWords("2026-11-18")).toBe("среда, 18.11.2026");
    expect(dateInWords("2026-11-16")).toBe("понедельник, 16.11.2026");
  });
  it("knows a Sunday and a Saturday", () => {
    expect(dateInWords("2026-11-22")).toBe("воскресенье, 22.11.2026");
    expect(dateInWords("2026-11-21")).toBe("суббота, 21.11.2026");
  });
  it("handles the turn of the year and a leap day", () => {
    expect(dateInWords("2027-01-01")).toBe("пятница, 01.01.2027");
    expect(dateInWords("2028-02-29")).toBe("вторник, 29.02.2028");
  });
  it("returns what is not a real date unchanged", () => {
    expect(dateInWords("")).toBe("");
    expect(dateInWords("2026-02-30")).toBe("2026-02-30");
    expect(dateInWords("вчера")).toBe("вчера");
  });
});

describe("the tabs", () => {
  it("keep the six views in their order", () => {
    expect(DAY_TABS.map((t) => t.label)).toEqual([
      "Занятия на дату",
      "Замена на день",
      "Отсутствия учителей",
      "Все изменения",
      "Дни в расписании",
      "Итоги замен",
    ]);
  });
  it("move with the arrows, wrapping round at the ends", () => {
    expect(nextDayTab(0, "ArrowRight")).toBe(1);
    expect(nextDayTab(5, "ArrowRight")).toBe(0);
    expect(nextDayTab(0, "ArrowLeft")).toBe(5);
    expect(nextDayTab(3, "ArrowLeft")).toBe(2);
  });
  it("jump to the ends with Home and End", () => {
    expect(nextDayTab(3, "Home")).toBe(0);
    expect(nextDayTab(1, "End")).toBe(5);
  });
  it("leave other keys alone", () => {
    expect(nextDayTab(2, "Enter")).toBeNull();
    expect(nextDayTab(2, "ArrowDown")).toBeNull();
    expect(nextDayTab(2, "Tab")).toBeNull();
  });
});

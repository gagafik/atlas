import { describe, it, expect } from "vitest";
import { bellState, todayBells } from "./startPage";
import type { State } from "./types";

const bells = [
  { start: "09:00", end: "09:45" },
  { start: "09:55", end: "10:40" },
];
const at = (time: string) =>
  Number(time.slice(0, 2)) * 60 + Number(time.slice(3));

describe("the countdown of the school day", () => {
  it.each([
    ["08:30", { phase: "before", index: 0, minutes: 30 }],
    ["09:00", { phase: "lesson", index: 0, minutes: 45 }],
    ["09:20", { phase: "lesson", index: 0, minutes: 25 }],
    // The minute a lesson ends already belongs to the break.
    ["09:45", { phase: "break", index: 1, minutes: 10 }],
    ["09:55", { phase: "lesson", index: 1, minutes: 45 }],
    ["10:40", { phase: "after" }],
    ["15:00", { phase: "after" }],
  ])("at %s", (time, expected) => {
    expect(bellState(bells, at(time))).toEqual(expected);
  });

  it("does not depend on the order the periods are listed in, and knows no day without them", () => {
    expect(bellState([bells[1], bells[0]], at("09:50"))).toEqual({
      phase: "break",
      index: 1,
      minutes: 5,
    });
    expect(bellState([], at("09:00"))).toBeNull();
  });
});

describe("the bells of a day", () => {
  const profile = (extra = {}) => ({
    id: "secondary",
    name: "Основная школа",
    periods: [
      { label: "2", start: "09:55", end: "10:40" },
      { label: "1", start: "09:00", end: "09:45" },
    ],
    ...extra,
  });
  const school = (extra: Partial<State> = {}, profiles = [profile()]) =>
    ({
      settings: {
        name: "Школа",
        yearStart: "2026-09-01",
        yearEnd: "2027-05-31",
        days: ["Пн", "Вт", "Ср", "Чт", "Пт"],
      },
      profiles,
      events: [],
      ...extra,
    }) as unknown as State;

  it("are the periods of the profile, in time order, on a school day", () => {
    // 2026-11-02 is a Monday.
    expect(todayBells(school(), "2026-11-02").map((p) => p.label)).toEqual([
      "1",
      "2",
    ]);
  });

  it("use the periods of the weekday when the profile sets its own", () => {
    const own = [{ label: "A", start: "08:00", end: "08:40" }];
    const state = school({}, [profile({ dayPeriods: { "0": own } })]);
    expect(todayBells(state, "2026-11-02")).toEqual(own);
    expect(todayBells(state, "2026-11-03").length).toBe(2);
  });

  it("are empty at the weekend, in a holiday and outside the school year", () => {
    expect(todayBells(school(), "2026-11-07")).toEqual([]);
    expect(todayBells(school(), "2026-08-15")).toEqual([]);
    const holiday = school({
      events: [
        {
          id: "e",
          name: "Каникулы",
          kind: "winter",
          start: "2026-10-26",
          end: "2026-11-03",
          grades: "",
        },
      ],
    });
    expect(todayBells(holiday, "2026-11-02")).toEqual([]);
    // An exam week is still a school week.
    const exams = school({
      events: [
        {
          id: "x",
          name: "Экзамены",
          kind: "exam",
          start: "2026-10-26",
          end: "2026-11-03",
          grades: "",
        },
      ],
    });
    expect(todayBells(exams, "2026-11-02").length).toBe(2);
  });
});

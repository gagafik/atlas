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
      classes: [{ id: "c1" }, { id: "c2" }],
      profiles,
      events: [],
      scheduleDays: [],
      ...extra,
    }) as unknown as State;
  const event = (kind: string, extra = {}) => ({
    id: "e",
    name: "Событие",
    kind,
    start: "2026-10-26",
    end: "2026-11-03",
    grades: "",
    ...extra,
  });
  const labels = (state: State, day: string) =>
    todayBells(state, day).map((p) => p.label);

  it("are the periods of the profile, in time order, on a school day", () => {
    // 2026-11-02 is a Monday.
    expect(labels(school(), "2026-11-02")).toEqual(["1", "2"]);
  });

  it("use the periods of the weekday when the profile sets its own", () => {
    const own = [{ label: "A", start: "08:00", end: "08:40" }];
    const state = school({}, [profile({ dayPeriods: { "0": own } })]);
    expect(todayBells(state, "2026-11-02")).toEqual(own);
    expect(labels(state, "2026-11-03")).toEqual(["1", "2"]);
  });

  it("are empty at the weekend and outside the school year, and run on the first day of the year", () => {
    expect(labels(school(), "2026-11-07")).toEqual([]);
    // 2026-08-31 is a Monday, 2026-09-01 a Tuesday.
    expect(labels(school(), "2026-08-31")).toEqual([]);
    expect(labels(school(), "2026-09-01")).toEqual(["1", "2"]);
  });

  it.each([
    ["a holiday", event("holiday"), "2026-10-28", false],
    ["the last day of a break", event("winter"), "2026-11-03", false],
    ["the day after a break", event("winter"), "2026-11-04", true],
    ["an exam week", event("exam"), "2026-11-02", true],
    ["a reading week that only notes", event("reading"), "2026-11-02", true],
    [
      "a reading week that cancels lessons",
      event("reading", { effect: "cancel" }),
      "2026-11-02",
      false,
    ],
    [
      "a cancelling event of one class",
      event("extra", { effect: "cancel", classIds: ["c1"] }),
      "2026-11-02",
      true,
    ],
    [
      "a cancelling event of every class",
      event("extra", { effect: "cancel", classIds: ["c1", "c2"] }),
      "2026-11-02",
      false,
    ],
  ])("when the calendar has %s", (_, e, day, runs) => {
    expect(labels(school({ events: [e] as never }), day).length > 0).toBe(runs);
  });

  it("follow the weekday chosen as «день в расписании»", () => {
    const own = [{ label: "A", start: "08:00", end: "08:40" }];
    const shifted = (extra = {}) =>
      school(
        {
          scheduleDays: [
            {
              id: "d",
              name: "",
              onDate: "2026-11-07",
              day: 0,
              classIds: [],
              cycleId: null,
              ...extra,
            },
          ],
        },
        [profile({ dayPeriods: { "0": own } })],
      );
    // A Saturday that works as a Monday runs Monday's bells.
    expect(todayBells(shifted(), "2026-11-07")).toEqual(own);
    // A day set for some classes only does not turn the whole school on.
    expect(todayBells(shifted({ classIds: ["c1"] }), "2026-11-07")).toEqual([]);
  });
});

import { describe, it, expect } from "vitest";
import {
  addDays,
  breakAfter,
  fairnessCsv,
  generationRanges,
  minutesText,
  monthRange,
  rosterCsv,
  templateTime,
  weekRange,
  type DutyTemplate,
  type FairnessData,
  type RosterData,
} from "./dutySchedule";
import type { Profile, State } from "./types";

const roster: RosterData = {
  school: "Школа",
  start: "2026-09-07",
  end: "2026-09-13",
  days: [
    { date: "2026-09-07", weekday: 0, name: "Понедельник", closed: "" },
    { date: "2026-09-08", weekday: 1, name: "Вторник", closed: "" },
  ],
  places: [
    { key: "холл", label: "Холл" },
    { key: "rooms:r0", label: "301" },
  ],
  cells: [
    {
      place: "холл",
      date: "2026-09-08",
      items: [
        {
          id: "b",
          start: "12:00",
          end: "12:05",
          name: "=evil",
          teachers: ["Иванова", "Петров"],
          required: 2,
          missing: 0,
        },
      ],
    },
    {
      place: "холл",
      date: "2026-09-07",
      items: [
        {
          id: "a",
          start: "12:00",
          end: "12:05",
          name: "Холл",
          teachers: ["=Сидоров"],
          required: 2,
          missing: 1,
        },
      ],
    },
    {
      place: "rooms:r0",
      date: "2026-09-07",
      items: [
        {
          id: "c",
          start: "08:30",
          end: "08:50",
          name: "301",
          teachers: [],
          required: 1,
          missing: 1,
        },
      ],
    },
  ],
  teachers: [],
  unfilled: 2,
};
const profile = {
  id: "secondary",
  name: "Средняя школа",
  periods: [
    { label: "1", start: "08:20", end: "09:00" },
    { label: "2", start: "09:05", end: "09:45" },
    { label: "3", start: "09:45", end: "10:25" },
  ],
  dayPeriods: {
    "1": [
      { label: "1", start: "08:00", end: "08:40" },
      { label: "2", start: "08:50", end: "09:30" },
      { label: "3", start: "09:30", end: "10:10" },
    ],
  },
} as unknown as Profile;
const template = {
  id: "t",
  weekday: 0,
  profileId: "secondary",
  afterPeriod: 0,
  startTime: null,
  endTime: null,
} as unknown as DutyTemplate;

describe("Duty roster helpers", () => {
  it("finds the Monday to Sunday week and the calendar month of a date", () => {
    expect(weekRange("2026-09-09")).toEqual(["2026-09-07", "2026-09-13"]);
    expect(weekRange("2026-09-07")).toEqual(["2026-09-07", "2026-09-13"]);
    expect(weekRange("2026-09-13")).toEqual(["2026-09-07", "2026-09-13"]);
    expect(monthRange("2026-02-15")).toEqual(["2026-02-01", "2026-02-28"]);
    expect(monthRange("2028-02-29")).toEqual(["2028-02-01", "2028-02-29"]);
    expect(monthRange("2026-12-31")).toEqual(["2026-12-01", "2026-12-31"]);
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
  });
  it("writes one CSV row per duty, sorted, with spreadsheet formulas neutralised", () => {
    const lines = rosterCsv(roster).split("\r\n");
    expect(lines[0]).toBe(
      '"Дата";"День";"Начало";"Конец";"Место";"Сотрудники";"Требуется";"Не хватает"',
    );
    expect(lines).toHaveLength(4);
    expect(lines[1]).toContain(
      '"2026-09-07";"Понедельник";"08:30";"08:50";"301";"";"1";"1"',
    );
    expect(lines[2]).toContain('"\'=Сидоров"');
    expect(lines[3]).toContain('"Иванова, Петров"');
    expect(lines[3]).toContain('"Холл"');
  });
  it("writes the fairness table", () => {
    const data = {
      start: "2026-09-07",
      end: "2026-09-13",
      rows: [
        {
          teacherId: "t0",
          name: "-Минус",
          duties: 2,
          minutes: 25,
          places: 1,
          deviation: 15,
          firstDate: "2026-09-07",
          lastDate: null,
        },
      ],
      summary: {},
    } as unknown as FairnessData;
    const lines = fairnessCsv(data).split("\r\n");
    expect(lines).toHaveLength(2);
    expect(lines[1]).toBe('"\'-Минус";"2";"25";"15";"1";"2026-09-07";""');
  });
  it("describes the break after a lesson with the bells of that weekday", () => {
    expect(breakAfter(profile, 0, 0)).toEqual({
      start: "09:00",
      end: "09:05",
      label: "1",
    });
    expect(breakAfter(profile, 1, 0)).toEqual({
      start: "08:40",
      end: "08:50",
      label: "1",
    });
    expect(breakAfter(profile, 0, 1)).toBeNull();
    expect(breakAfter(profile, 0, 2)).toBeNull();
    expect(breakAfter(undefined, 0, 0)).toBeNull();
    expect(templateTime(template, [profile])).toBe(
      "перемена после 1 урока (09:00–09:05)",
    );
    expect(templateTime({ ...template, weekday: 1 }, [profile])).toBe(
      "перемена после 1 урока (08:40–08:50)",
    );
    expect(templateTime({ ...template, afterPeriod: 2 }, [profile])).toContain(
      "в звонках её больше нет",
    );
    expect(
      templateTime(
        {
          ...template,
          afterPeriod: null,
          startTime: "08:30",
          endTime: "08:50",
        },
        [profile],
      ),
    ).toBe("08:30–08:50");
  });
  it("offers each teaching period, the year and a custom range", () => {
    const s = {
      settings: { yearStart: "2026-08-17", yearEnd: "2027-06-11" },
      cycles: {
        patterns: [],
        periods: [
          {
            id: "q1",
            name: "Первая четверть",
            start: "2026-08-10",
            end: "2026-10-23",
            anchor: "2026-08-17",
            patternIds: ["a"],
          },
        ],
      },
    } as unknown as State;
    const ranges = generationRanges(s);
    expect(ranges.map((r) => r.id)).toEqual(["q1", "year", "custom"]);
    expect(ranges[0]).toMatchObject({ start: "2026-08-17", end: "2026-10-23" });
    expect(ranges[1]).toMatchObject({ start: "2026-08-17", end: "2027-06-11" });
    expect(ranges[0].label).toBe("Первая четверть · 10.08.2026–23.10.2026");
  });
  it("shows minutes as hours and minutes", () => {
    expect(minutesText(25)).toBe("25 мин");
    expect(minutesText(135)).toBe("2 ч 15 мин");
    expect(minutesText(60)).toBe("1 ч 0 мин");
  });
});

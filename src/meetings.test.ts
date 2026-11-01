import { describe, expect, it } from "vitest";
import {
  schoolInput,
  schoolISO,
  blankDefinition,
  groupByDay,
  dayRows,
} from "./meetings";
import type { DayView } from "./meetings";
describe("school meeting time", () => {
  it("keeps 17:00 school time independently of device zone", () => {
    expect(schoolISO("2027-04-05T17:00")).toBe("2027-04-05T12:00:00.000Z");
    expect(schoolInput("2027-04-05T12:00:00Z")).toBe("2027-04-05T17:00");
  });
  it("handles a UTC date boundary", () =>
    expect(schoolInput("2027-04-05T23:30:00Z")).toBe("2027-04-06T04:30"));
  it("rejects impossible dates and incomplete input", () => {
    expect(() => schoolISO("2027-02-30T17:00")).toThrow();
    expect(() => schoolISO("")).toThrow();
  });
  it("defaults to the school's 17–19 ten-minute meetings", () => {
    const d = blankDefinition();
    expect(schoolInput(d.startsAt).slice(11)).toBe("17:00");
    expect(schoolInput(d.endsAt).slice(11)).toBe("19:00");
    expect(d.slotMinutes).toBe(10);
  });
});
describe("meeting agenda grouping", () => {
  it("groups by school day and keeps the order", () => {
    const items = [
      { id: "a", startsAt: "2027-04-05T12:00:00Z" },
      { id: "b", startsAt: "2027-04-05T19:30:00Z" },
      { id: "c", startsAt: "2027-04-06T12:00:00Z" },
    ];
    expect(
      groupByDay(items).map(([day, rows]) => [day, rows.map((r) => r.id)]),
    ).toEqual([
      ["2027-04-05", ["a"]],
      ["2027-04-06", ["b", "c"]],
    ]);
    expect(groupByDay([])).toEqual([]);
  });
});
describe("teacher day rows", () => {
  // The shape of GET /api/meetings/day: the timetable sends a lesson's rooms as one text.
  const view: DayView = {
    date: "2027-04-05",
    hostName: "Учитель",
    lessons: [
      {
        start: "08:20",
        end: "09:00",
        subject: "Английский язык",
        group: "10A",
        rooms: "301, 302",
        status: "scheduled",
        activity: "lesson",
      },
      {
        start: "10:00",
        end: "10:40",
        subject: "Математика",
        group: "",
        rooms: "",
        status: "scheduled",
        activity: "lesson",
      },
    ],
    slots: [
      {
        id: "s1",
        eventTitle: "Консультации",
        kind: "consultation",
        startsAt: "2027-04-05T04:00:00Z",
        endsAt: "2027-04-05T04:15:00Z",
        status: "available",
        location: "",
        roomName: "Кабинет 5",
        conflict: "Учитель занят в школьном календаре.",
      },
    ],
    classMeetings: [],
  };
  it("merges lessons and slots in time order and names the room of each", () => {
    const rows = dayRows(view);
    expect(rows.map((r) => [r.start, r.place])).toEqual([
      ["08:20", "301, 302"],
      ["09:00", "Кабинет 5"],
      ["10:00", ""],
    ]);
    expect(rows[1].warning).toBe("Учитель занят в школьном календаре.");
  });
});

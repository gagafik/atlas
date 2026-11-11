import { describe, it, expect } from "vitest";
import {
  csvCell,
  weekdays,
  placementMatches,
  chronologicalPlacements,
  issuesForPlacements,
  issuesForStudent,
  lessonTime,
  teacherNames,
  roomNames,
  rosterComplete,
} from "./utils";
import type { State, Workload, Placement, Group } from "./types";
describe("Export and timetable filtering", () => {
  it("distinguishes a confirmed empty section from an unknown roster", () => {
    const g = {
      kind: "elective",
      studentIds: [],
      rosterStatus: "complete",
    } as unknown as Group;
    expect(rosterComplete(g)).toBe(true);
    expect(rosterComplete({ ...g, rosterStatus: null })).toBe(false);
    expect(rosterComplete({ ...g, rosterStatus: "partial" })).toBe(false);
  });
  const people = {
    teachers: [
      { id: "a", name: "Первый" },
      { id: "b", name: "Второй" },
    ],
    rooms: [
      { id: "one", name: "101" },
      { id: "two", name: "102" },
    ],
  } as State;
  const shared = {
    teacherId: "a",
    additionalTeacherIds: ["b"],
    roomIds: ["one"],
    requiredRoomIds: ["two"],
  } as Workload;
  it("finds a lesson by its co-teacher and by a simultaneous room", () => {
    const p = { roomId: "one" } as Placement;
    expect(placementMatches(people, shared, p, "teacher", "b")).toBe(true);
    expect(placementMatches(people, shared, p, "room", "two")).toBe(true);
    expect(placementMatches(people, shared, null, "room", "two")).toBe(true);
  });
  it("names every co-teacher and every simultaneous room, and tells no teacher needed from none given", () => {
    const p = { roomId: "one" } as Placement;
    const teachers = teacherNames(people, shared);
    expect(teachers).toContain("Первый");
    expect(teachers).toContain("Второй");
    const rooms = roomNames(people, shared, p);
    expect(rooms).toContain("101");
    expect(rooms).toContain("102");
    const notNeeded = { teacherId: null, teacherRequired: false } as Workload;
    const missing = { teacherId: null } as Workload;
    expect(teacherNames(people, notNeeded)).not.toBe(
      teacherNames(people, missing),
    );
    const noRoom = { roomId: null } as Placement;
    expect(
      roomNames(people, { roomIds: [], roomRequired: false } as unknown as Workload, noRoom),
    ).not.toBe(
      roomNames(people, { roomIds: [] } as unknown as Workload, noRoom),
    );
  });
  it("uses the selected day's bells and falls back to the base profile", () => {
    const s = {
      workloads: [{ id: "w", profileId: "p" }],
      profiles: [
        {
          id: "p",
          periods: [{ label: "0", start: "09:00", end: "09:40" }],
          dayPeriods: { "0": [{ label: "0", start: "08:00", end: "08:40" }] },
        },
      ],
    } as unknown as State;
    expect(
      lessonTime(s, { workloadId: "w", day: 0, period: 0 } as Placement).start,
    ).toBe("08:00");
    expect(
      lessonTime(s, { workloadId: "w", day: 1, period: 0 } as Placement).start,
    ).toBe("09:00");
  });
  it("neutralizes spreadsheet formula injection and escapes quotes", () => {
    expect(csvCell('=HYPERLINK("x")')).toBe('"\'=HYPERLINK(""x"")"');
    expect(csvCell("Math")).toBe('"Math"');
  });
  it("counts weekdays inclusively without a timezone shift", () => {
    expect(weekdays("2026-08-17", "2026-08-21")).toBe(5);
    expect(weekdays("2026-08-22", "2026-08-23")).toBe(0);
  });
  it("uses actual imported class membership for each shared lesson", () => {
    const s = {
      groups: [{ id: "g", classIds: ["a", "b"] }],
    } as unknown as State;
    const w = { groupId: "g" } as Workload;
    const p = { classIds: ["a"] } as Placement;
    expect(placementMatches(s, w, p, "class", "b")).toBe(false);
    expect(placementMatches(s, w, null, "class", "b")).toBe(true);
  });
});

describe("Personal timetable", () => {
  it("keeps per-pupil daily limits personal, including minimums on empty days", () => {
    const issues = [
      {
        type: "rule",
        ruleId: "limit",
        ids: ["shared"],
        affectedStudentIds: ["one"],
        message: "daily maximum",
      },
      {
        type: "rule",
        ruleId: "minimum",
        ids: [],
        affectedStudentIds: ["one"],
        message: "empty day minimum",
      },
    ];
    expect(
      issuesForStudent(
        issues,
        [{ id: "shared", workloadId: "w" }] as Placement[],
        "two",
      ),
    ).toHaveLength(0);
    expect(issuesForStudent(issues, [], "one")).toHaveLength(2);
  });
  const state = {
    students: [
      { id: "one", name: "Same name", classId: "a" },
      { id: "two", name: "Same name", classId: "b" },
    ],
    groups: [
      { id: "class", kind: "whole", classIds: ["a", "b"], studentIds: [] },
      {
        id: "core",
        kind: "core",
        classIds: ["a", "b"],
        studentIds: ["one"],
        rosterStatus: "complete",
      },
      {
        id: "elective",
        kind: "elective",
        classIds: ["a", "b"],
        studentIds: ["two"],
        rosterStatus: "complete",
      },
      {
        id: "unknown",
        kind: "core",
        classIds: ["a", "b"],
        studentIds: [],
        rosterStatus: "unknown",
      },
    ],
    workloads: [
      { id: "late", groupId: "class", profileId: "primary" },
      { id: "early", groupId: "core", profileId: "secondary" },
    ],
    profiles: [
      { id: "primary", periods: [{ start: "09:00", end: "09:40" }] },
      { id: "secondary", periods: [{ start: "08:20", end: "09:00" }] },
    ],
  } as unknown as State;
  it("includes whole-class lessons without requiring explicit group membership", () => {
    expect(
      placementMatches(
        state,
        { groupId: "class" } as Workload,
        null,
        "student",
        "one",
      ),
    ).toBe(true);
    expect(
      placementMatches(
        state,
        { groupId: "class" } as Workload,
        { classIds: ["b"] } as Placement,
        "student",
        "one",
      ),
    ).toBe(false);
  });
  it("includes only selected core/electives and uses IDs for pupils with identical names", () => {
    expect(
      placementMatches(
        state,
        { groupId: "core" } as Workload,
        null,
        "student",
        "one",
      ),
    ).toBe(true);
    expect(
      placementMatches(
        state,
        { groupId: "elective" } as Workload,
        null,
        "student",
        "one",
      ),
    ).toBe(false);
    expect(
      placementMatches(
        state,
        { groupId: "core" } as Workload,
        null,
        "student",
        "two",
      ),
    ).toBe(false);
  });
  it("uses mixed-group membership even when the imported cell belongs to another class", () => {
    expect(
      placementMatches(
        state,
        { groupId: "core" } as Workload,
        { classIds: ["b"] } as Placement,
        "student",
        "one",
      ),
    ).toBe(true);
  });
  it("does not turn an unknown membership or missing pupil into a whole-school schedule", () => {
    expect(
      placementMatches(
        state,
        { groupId: "unknown" } as Workload,
        null,
        "student",
        "one",
      ),
    ).toBe(false);
    expect(
      placementMatches(
        state,
        { groupId: "core" } as Workload,
        null,
        "student",
        "",
      ),
    ).toBe(false);
    expect(
      placementMatches(
        state,
        { groupId: "core" } as Workload,
        null,
        "student",
        "missing",
      ),
    ).toBe(false);
  });
  it("orders lessons by actual bell times, not period numbers, without mutating the input", () => {
    const lessons = [
      { id: "l", workloadId: "late", day: 0, period: 0 },
      { id: "e", workloadId: "early", day: 0, period: 0 },
    ] as Placement[];
    expect(chronologicalPlacements(state, lessons).map((p) => p.id)).toEqual([
      "e",
      "l",
    ]);
    expect(lessons[0].id).toBe("l");
  });
  it("filters both placement conflicts and workload violations to the personal week", () => {
    const lessons = [{ id: "p", workloadId: "w" }] as Placement[];
    const issues = [
      { type: "collision", ids: ["p", "other"], message: "collision" },
      { type: "daily", ids: ["w"], message: "daily" },
      { type: "hours", ids: ["elsewhere"], message: "unrelated" },
    ];
    expect(issuesForPlacements(issues, lessons).map((i) => i.type)).toEqual([
      "collision",
      "daily",
    ]);
  });
  it("does not blame every pupil in a group for another pupil's overlapping choice", () => {
    const placements = [{ id: "math", workloadId: "wm" }] as Placement[];
    const collision = {
      day: 0,
      start: "10:00",
      end: "10:40",
      studentIds: ["one"],
      classIds: [],
      teacherIds: [] as string[],
      roomIds: [] as string[],
    };
    const issues = [
      {
        type: "collision",
        ids: ["math", "physics"],
        message: "overlap",
        collision,
      },
    ];
    expect(issuesForStudent(issues, placements, "one")).toHaveLength(1);
    expect(issuesForStudent(issues, placements, "two")).toHaveLength(0);
    expect(
      issuesForStudent(
        [
          {
            ...issues[0],
            collision: { ...collision, teacherIds: ["teacher"] },
          },
        ],
        placements,
        "two",
      ),
    ).toHaveLength(1);
    expect(
      issuesForStudent(
        [{ ...issues[0], collision: { ...collision, roomIds: ["room"] } }],
        placements,
        "two",
      ),
    ).toHaveLength(1);
  });
});

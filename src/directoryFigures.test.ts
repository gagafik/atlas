import { describe, expect, it } from "vitest";
import { directoryFigures } from "./directoryFigures";
import type { State } from "./types";

const state = {
  groups: [
    { id: "g1", name: "1А", kind: "whole", classIds: ["c1"], studentIds: [] },
    { id: "g2", name: "Англ", kind: "subgroup", classIds: ["c1", "c2"], studentIds: [] },
  ],
  workloads: [
    { id: "w1", groupId: "g1", teacherId: "t1", subjectId: "s1", hours: 3, roomIds: ["r1"] },
    { id: "w2", groupId: "g2", teacherId: "t1", additionalTeacherIds: ["t1", "t2"], subjectId: "s1", hours: 2, roomIds: [] },
  ],
  placements: [
    { id: "p1", workloadId: "w1", day: 0, period: 1, roomId: "r1", classIds: [] },
    { id: "p2", workloadId: "w2", day: 0, period: 2, roomId: null, classIds: ["c2"] },
    { id: "p3", workloadId: "w2", day: 1, period: 2, roomId: "r2", classIds: [] },
  ],
  rules: [],
} as unknown as State;

describe("directory figures", () => {
  const f = directoryFigures(state, "weekly");
  it("counts the hours of a teacher once per workload", () => {
    expect(f.teacherHours).toEqual({ t1: 5, t2: 2 });
  });
  it("counts subject loads and hours", () => {
    expect(f.subjectLoads.s1).toBe(2);
    expect(f.subjectHours.s1).toBe(5);
  });
  it("counts placements by room and by class, taking classes of the group when the placement has none", () => {
    expect(f.roomPlacements).toEqual({ r1: 1, r2: 1 });
    expect(f.classPlacements).toEqual({ c1: 2, c2: 2 });
  });
  it("counts the groups of a class without whole-class groups", () => {
    expect(f.classGroups).toEqual({ c1: 1, c2: 1 });
  });
});

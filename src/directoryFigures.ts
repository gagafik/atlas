import { weekState } from "./cycles";
import type { State } from "./types";
import { roomIds, teacherIds } from "./utils";

export type DirectoryFigures = {
  teacherHours: Record<string, number>;
  subjectLoads: Record<string, number>;
  subjectHours: Record<string, number>;
  roomPlacements: Record<string, number>;
  classGroups: Record<string, number>;
  classPlacements: Record<string, number>;
};

const add = (map: Record<string, number>, id: string, n: number) => {
  map[id] = (map[id] ?? 0) + n;
};

/**
 * The figures of the directory columns for one week, counted in a single pass over the workloads and placements,
 * so the lists read them by id instead of scanning the week once per row.
 */
export function directoryFigures(s: State, cycleId: string): DirectoryFigures {
  const week = weekState(s, cycleId);
  const figures: DirectoryFigures = {
    teacherHours: {},
    subjectLoads: {},
    subjectHours: {},
    roomPlacements: {},
    classGroups: {},
    classPlacements: {},
  };
  const workloads = new Map(week.workloads.map((w) => [w.id, w]));
  const groups = new Map(s.groups.map((g) => [g.id, g]));
  for (const w of week.workloads) {
    for (const id of new Set(teacherIds(w))) add(figures.teacherHours, id, w.hours);
    add(figures.subjectLoads, w.subjectId, 1);
    add(figures.subjectHours, w.subjectId, w.hours);
  }
  for (const p of week.placements) {
    const w = workloads.get(p.workloadId);
    if (!w) continue;
    for (const id of new Set(roomIds(w, p))) add(figures.roomPlacements, id, 1);
    const classIds = p.classIds.length
      ? p.classIds
      : (groups.get(w.groupId)?.classIds ?? []);
    for (const id of new Set(classIds)) add(figures.classPlacements, id, 1);
  }
  for (const g of s.groups)
    if (g.kind !== "whole")
      for (const id of new Set(g.classIds)) add(figures.classGroups, id, 1);
  return figures;
}

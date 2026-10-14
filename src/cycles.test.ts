import { describe, expect, it } from "vitest";
import { activeIn, cycleHours, patterns, patternOn, weekState } from "./cycles";
import type { State } from "./types";

const state = {
  settings: { yearStart: "2026-08-17", yearEnd: "2027-06-11" },
  cycles: {
    patterns: [
      { id: "weekly", name: "A" },
      { id: "b", name: "B" },
    ],
    periods: [],
  },
  workloads: [
    {
      id: "one",
      hours: 2,
      cycleHours: { b: 3 },
      lessonLengths: [2],
      cycleLessonLengths: { b: [3] },
    },
    { id: "two", hours: 1, cycleIds: ["weekly"] },
  ],
  placements: [
    { id: "one", workloadId: "one" },
    { id: "two", workloadId: "one", cycleId: "b" },
  ],
  rules: [{ id: "r", cycleIds: ["b"] }],
} as unknown as State;
describe("Teaching cycles", () => {
  it("isolates a weekly view without mutating shared hours or other weeks", () => {
    const view = weekState(state, "b");
    expect(view.workloads).toHaveLength(1);
    expect(view.workloads[0].hours).toBe(3);
    expect(view.workloads[0].lessonLengths).toEqual([3]);
    expect(view.placements.map((p) => p.id)).toEqual(["two"]);
    expect(state.workloads[0].hours).toBe(2);
    expect(cycleHours(state.workloads[0], "weekly")).toBe(2);
    expect(activeIn(state.workloads[1], "b")).toBe(false);
    expect(weekState(state, "weekly").rules).toEqual([]);
  });
  it("rotates calendar weeks without depending on a local time zone", () => {
    expect(patternOn(state, "2026-08-17")).toBe("weekly");
    expect(patternOn(state, "2026-08-23")).toBe("weekly");
    expect(patternOn(state, "2026-08-24")).toBe("b");
    expect(patternOn(state, "2026-08-31")).toBe("weekly");
    expect(patternOn(state, "2027-06-12")).toBeNull();
  });
  it("respects term gaps and an explicit Monday anchor, including dates before it", () => {
    const s = structuredClone(state);
    s.cycles!.periods = [
      {
        id: "t",
        name: "Term",
        start: "2026-09-01",
        end: "2026-12-20",
        anchor: "2026-09-07",
        patternIds: ["weekly", "b"],
      },
    ];
    expect(patternOn(s, "2026-09-01")).toBe("b");
    expect(patternOn(s, "2026-09-07")).toBe("weekly");
    expect(patternOn(s, "2026-08-31")).toBeNull();
    expect(patternOn(s, "2026-12-21")).toBeNull();
  });
  it("keeps old single-week documents compatible", () => {
    const s = { ...state, cycles: undefined };
    expect(patterns(s)[0].id).toBe("weekly");
    expect(patternOn(s, "2026-10-13")).toBe("weekly");
  });
});

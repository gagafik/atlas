import { describe, it, expect } from "vitest";
import { withEffectiveFrom } from "./timetableVersions";

describe("effective date of a timetable change", () => {
  it("keeps the path when no date is chosen", () => {
    expect(withEffectiveFrom("state", "")).toBe("state");
  });
  it("adds the chosen school day", () => {
    expect(withEffectiveFrom("state", "2026-08-24")).toBe(
      "state?effectiveFrom=2026-08-24",
    );
  });
  it("joins an existing query", () => {
    expect(withEffectiveFrom("check?x=1", "2026-08-24")).toBe(
      "check?x=1&effectiveFrom=2026-08-24",
    );
  });
  it("ignores values that are not an ISO date", () => {
    expect(withEffectiveFrom("state", "24.08.2026")).toBe("state");
    expect(withEffectiveFrom("state", "2026-8-24")).toBe("state");
  });
});

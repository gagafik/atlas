import { describe, it, expect } from "vitest";
import { queueParams } from "./homeworkQueue";

const empty = { start: "", end: "", filter: "", history: false, search: "" };
describe("homework queue request", () => {
  it("sends no dates, status or cursor for the default window", () => {
    const params = queueParams(empty, "");
    for (const name of ["start", "end", "filter", "cursor"])
      expect(params.has(name)).toBe(false);
    expect(params.get("history")).toBe("false");
    expect(params.get("search")).toBe("");
  });
  it("adds the cursor, chosen dates, status and search when they are set", () => {
    expect(queueParams(empty, "abc").get("cursor")).toBe("abc");
    const params = queueParams(
      {
        start: "2026-09-01",
        end: "2026-09-30",
        filter: "all",
        history: true,
        search: "физика",
      },
      "",
    );
    expect(params.get("start")).toBe("2026-09-01");
    expect(params.get("end")).toBe("2026-09-30");
    expect(params.get("filter")).toBe("all");
    expect(params.get("history")).toBe("true");
    expect(params.get("search")).toBe("физика");
    expect(params.has("cursor")).toBe(false);
  });
  it("sends a single date as it is", () => {
    const params = queueParams({ ...empty, start: "2026-09-01" }, "");
    expect(params.get("start")).toBe("2026-09-01");
    expect(params.has("end")).toBe(false);
  });
});

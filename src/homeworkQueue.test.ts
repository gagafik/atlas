import { describe, it, expect } from "vitest";
import {
  isPast,
  queueParams,
  queueSections,
  submissionTag,
  type QueueItem,
} from "./homeworkQueue";

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

const TODAY = "2026-09-10";
const item = (over: Partial<QueueItem> & { id?: string }) => ({
  id: "x",
  dueDate: null,
  lessonDate: "2026-09-10",
  status: "not_submitted",
  assignmentChanged: false,
  submission: "online" as const,
  ...over,
});
describe("homework queue sections", () => {
  it("splits by the date the server orders by: the deadline, else the lesson day", () => {
    expect(isPast(item({ dueDate: "2026-09-09" }), TODAY)).toBe(true);
    expect(isPast(item({ dueDate: TODAY }), TODAY)).toBe(false);
    expect(isPast(item({ lessonDate: "2026-09-01" }), TODAY)).toBe(true);
    // An individual extension is the effective deadline, so the card moves with it.
    expect(
      isPast(item({ dueDate: "2026-09-12", lessonDate: "2026-09-01" }), TODAY),
    ).toBe(false);
  });
  it("keeps the order and drops empty groups in the default window", () => {
    const rows = [
      item({ id: "a", dueDate: "2026-09-09" }),
      item({ id: "b", dueDate: "2026-09-02" }),
      item({ id: "c", dueDate: "2026-09-11" }),
      item({ id: "d", dueDate: "2026-09-20" }),
    ];
    const [past, next] = queueSections(rows, TODAY, "upcoming");
    expect([past.key, past.items.map((r) => r.id)]).toEqual([
      "past",
      ["a", "b"],
    ]);
    expect([next.key, next.items.map((r) => r.id)]).toEqual([
      "next",
      ["c", "d"],
    ]);
    expect(
      queueSections(rows.slice(2), TODAY, "upcoming").map((g) => g.key),
    ).toEqual(["next"]);
    expect(
      queueSections(rows.slice(0, 2), TODAY, "upcoming").map((g) => g.key),
    ).toEqual(["past"]);
    expect(queueSections([], TODAY, "upcoming")).toEqual([]);
  });
  it("is one list for explicit dates, whatever the dates are", () => {
    const rows = [
      item({ id: "a", dueDate: "2026-09-02" }),
      item({ id: "b", dueDate: "2026-09-20" }),
    ];
    const groups = queueSections(rows, TODAY, "range");
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe("all");
    expect(groups[0].items).toBe(rows);
  });
});

describe("homework submission tag", () => {
  const labels = {
    not_submitted: "N",
    submitted: "S",
    returned: "R",
    accepted: "A",
  };
  it("never mentions sending for work without online submission", () => {
    const offline = (over: Partial<QueueItem>) =>
      item({ submission: "offline", ...over });
    const future = submissionTag(
      offline({ dueDate: "2026-09-15" }),
      TODAY,
      labels,
    );
    const past = submissionTag(
      offline({ dueDate: "2026-09-01" }),
      TODAY,
      labels,
    );
    expect(future).not.toBe(past);
    expect(
      submissionTag(
        offline({ dueDate: "2026-09-15", assignmentChanged: true }),
        TODAY,
        labels,
      ),
    ).toBe(future);
    expect(
      submissionTag(
        offline({ dueDate: "2026-09-01", assignmentChanged: true }),
        TODAY,
        labels,
      ),
    ).toBe(past);
    // A result the teacher already recorded keeps its status label.
    expect(
      submissionTag(
        offline({ status: "accepted", dueDate: "2026-09-01" }),
        TODAY,
        labels,
      ),
    ).toBe("A");
  });
  it("leaves online work as it was", () => {
    expect(submissionTag(item({ status: "submitted" }), TODAY, labels)).toBe(
      "S",
    );
    const changed = submissionTag(
      item({ assignmentChanged: true }),
      TODAY,
      labels,
    );
    expect(changed).not.toBe("N");
    expect(changed).toContain("отправка");
    expect(submissionTag(item({ dueDate: "2026-09-01" }), TODAY, labels)).toBe(
      "N",
    );
  });
});

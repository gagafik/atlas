import { describe, it, expect } from "vitest";
import {
  dateLabel,
  isPast,
  itemAction,
  itemChips,
  manyPupils,
  periodLabel,
  queueParams,
  queueSections,
  queueTabs,
  shortDate,
  submissionTag,
  taskGroups,
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
  it("asks for one child when a family portal looks at a child", () => {
    expect(queueParams(empty, "", "s2").get("studentId")).toBe("s2");
    expect(queueParams(empty, "", "").has("studentId")).toBe(false);
    expect(queueParams(empty, "").has("studentId")).toBe(false);
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
    expect(groups[0].items).toEqual(rows);
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
    // It is not the on-the-lesson tag of work without online submission either.
    expect(
      submissionTag(
        item({ submission: "offline", assignmentChanged: true }),
        TODAY,
        labels,
      ),
    ).not.toBe(changed);
    expect(submissionTag(item({ dueDate: "2026-09-01" }), TODAY, labels)).toBe(
      "N",
    );
  });
});

describe("homework queue tabs", () => {
  const every = ["all", "todo", "submitted", "returned", "accepted", "overdue"];
  it("lists every status the server knows once, for each role", () => {
    for (const staff of [true, false]) {
      const filters = queueTabs(staff).map((tab) => tab.filter);
      expect([...filters].sort()).toEqual([...every].sort());
      for (const tab of queueTabs(staff)) expect(tab.label).not.toBe("");
    }
  });
  it("starts with the status the server opens each role on", () => {
    expect(queueTabs(true)[0].filter).toBe("submitted");
    expect(queueTabs(false)[0].filter).toBe("todo");
  });
});

describe("homework queue dates and period", () => {
  it("turns an ISO date into day.month.year and day.month", () => {
    expect(dateLabel("2026-09-01")).toBe("01.09.2026");
    expect(shortDate("2026-09-01")).toBe("01.09");
  });
  const year = { yearStart: "2026-09-01", yearEnd: "2027-05-25" };
  it("tells the default window, the whole year and other dates apart", () => {
    const upcoming = periodLabel({
      ...year,
      window: "upcoming",
      start: "2026-09-01",
      end: "2026-09-24",
    });
    const whole = periodLabel({
      ...year,
      window: "range",
      start: year.yearStart,
      end: year.yearEnd,
    });
    const dates = periodLabel({
      ...year,
      window: "range",
      start: "2026-10-05",
      end: "2026-10-18",
    });
    expect(new Set([upcoming, whole, dates]).size).toBe(3);
    expect(dates).toContain(dateLabel("2026-10-05"));
    expect(dates).toContain(dateLabel("2026-10-18"));
  });
  it("is dates, not the year, when only one end matches the year", () => {
    const label = periodLabel({
      ...year,
      window: "range",
      start: year.yearStart,
      end: "2026-12-31",
    });
    expect(label).toContain(dateLabel("2026-12-31"));
  });
});

describe("homework task groups", () => {
  const row = (lessonKey: string, studentId: string) => ({
    lessonKey,
    studentId,
  });
  it("joins neighbours with one key and keeps the order", () => {
    const groups = taskGroups([row("a", "s1"), row("a", "s2"), row("b", "s1")]);
    expect(groups.map((g) => g.lessonKey)).toEqual(["a", "b"]);
    expect(groups[0].items.map((r) => r.studentId)).toEqual(["s1", "s2"]);
  });
  it("starts a new group when a key comes back after another one", () => {
    const groups = taskGroups([row("a", "s1"), row("b", "s1"), row("a", "s2")]);
    expect(groups.map((g) => g.lessonKey)).toEqual(["a", "b", "a"]);
  });
  it("gives no groups for no rows", () => {
    expect(taskGroups([])).toEqual([]);
  });
});

describe("homework row chips and action", () => {
  const plain = {
    submission: "online" as const,
    overdue: false,
    deadlineClosed: false,
    late: false,
    readOnly: false,
  };
  it("has no chips when nothing is raised", () => {
    expect(itemChips(plain)).toEqual([]);
  });
  it("adds each raised flag once", () => {
    const flags = [
      { submission: "offline" as const },
      { overdue: true },
      { deadlineClosed: true },
      { late: true },
      { readOnly: true },
    ];
    const keys = flags.map((flag) => itemChips({ ...plain, ...flag }));
    for (const one of keys) expect(one).toHaveLength(1);
    expect(new Set(keys.flat()).size).toBe(flags.length);
  });
  it("keeps one order whichever flags are raised together", () => {
    const all = itemChips({
      submission: "offline",
      overdue: true,
      deadlineClosed: true,
      late: true,
      readOnly: true,
    });
    const some = itemChips({ ...plain, late: true, overdue: true });
    expect(all.filter((key) => some.includes(key))).toEqual(some);
  });
  it("prefers review over submitting over reading a task", () => {
    const base = { canReview: false, canSubmit: false };
    const action = (
      over: object,
      submission: "online" | "offline" = "online",
    ) => itemAction({ ...base, submission, ...over });
    const review = action({ canReview: true, canSubmit: true });
    const submit = action({ canSubmit: true }, "offline");
    const task = action({}, "offline");
    const work = action({});
    expect(new Set([review, submit, task, work]).size).toBe(4);
    expect(action({ canReview: true }, "offline")).toBe(review);
  });
});

describe("homework many pupils", () => {
  it("is true only for two different pupils", () => {
    expect(manyPupils([])).toBe(false);
    expect(manyPupils([{ studentId: "s1" }])).toBe(false);
    expect(manyPupils([{ studentId: "s1" }, { studentId: "s1" }])).toBe(false);
    expect(manyPupils([{ studentId: "s1" }, { studentId: "s2" }])).toBe(true);
  });
});

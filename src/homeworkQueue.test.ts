import { describe, it, expect } from "vitest";
import {
  dateLabel,
  fieldsFromAnswer,
  groupDeadline,
  isPast,
  itemAction,
  itemChips,
  listKey,
  manyPupils,
  mayLeave,
  nextConditions,
  pageExpired,
  periodButtonLabel,
  periodLabel,
  periodPatch,
  periodRefused,
  queueParams,
  queueSections,
  queueTabs,
  shortDate,
  submissionTag,
  taskGroups,
  wholeTaskKey,
  workKey,
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

describe("homework open key", () => {
  it("tells one pupil's work from the whole task and from another pupil", () => {
    const keys = [
      workKey("t1", "s1"),
      workKey("t1", "s2"),
      workKey("t1"),
      workKey("t2"),
    ];
    expect(new Set(keys).size).toBe(4);
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

describe("homework task groups that come back", () => {
  const row = (lessonKey: string, studentId: string) => ({
    lessonKey,
    studentId,
  });
  it("counts the earlier groups of the same task", () => {
    const groups = taskGroups([
      row("a", "s1"),
      row("b", "s1"),
      row("a", "s2"),
      row("a", "s3"),
    ]);
    expect(groups.map((g) => [g.lessonKey, g.part])).toEqual([
      ["a", 0],
      ["b", 0],
      ["a", 1],
    ]);
  });
  it("names the whole-task button of each part of a task differently", () => {
    const keys = [
      wholeTaskKey("a"),
      wholeTaskKey("a", 1),
      wholeTaskKey("a", 2),
      wholeTaskKey("b"),
      workKey("a", "s1"),
    ];
    expect(new Set(keys).size).toBe(keys.length);
    expect(wholeTaskKey("a", 0)).toBe(workKey("a"));
  });
});

describe("homework group deadline", () => {
  const pupil = (extended: boolean, dueDate: string | null) => ({
    extended,
    dueDate,
    baseDueDate: "2026-11-20",
  });
  it("is the shared deadline while any pupil of the group has it", () => {
    expect(
      groupDeadline([pupil(false, "2026-11-20"), pupil(true, "2026-11-27")]),
    ).toEqual({ date: "2026-11-20", individual: false });
  });
  it("is the pupils' own date when every pupil of the group has an extension", () => {
    expect(
      groupDeadline([pupil(true, "2026-11-27"), pupil(true, "2026-11-27")]),
    ).toEqual({ date: "2026-11-27", individual: true });
  });
  it("is no date for a task without a deadline", () => {
    expect(
      groupDeadline([{ extended: false, dueDate: null, baseDueDate: null }])
        .date,
    ).toBeNull();
  });
});

describe("homework queue conditions", () => {
  const fields = { ...empty, filter: "submitted", search: "old" };
  it("applies the typed search together with any other change", () => {
    const next = nextConditions(fields, "typed", { filter: "all" });
    expect(next.search).toBe("typed");
    expect(next.filter).toBe("all");
  });
  it("keeps what the change does not touch", () => {
    const next = nextConditions({ ...fields, history: true }, "old", {
      filter: "todo",
    });
    expect(next.history).toBe(true);
  });
  it("takes the tab the server chose, and the dates only for explicit dates", () => {
    const base = { start: "2026-09-01", end: "2026-09-24" };
    const upcoming = fieldsFromAnswer(empty, {
      ...base,
      window: "upcoming",
      filter: "todo",
    });
    expect(upcoming.filter).toBe("todo");
    expect(upcoming.start).toBe("");
    expect(upcoming.end).toBe("");
    const range = fieldsFromAnswer(empty, {
      ...base,
      window: "range",
      filter: "submitted",
    });
    expect(range.start).toBe(base.start);
    expect(range.end).toBe(base.end);
  });
  const year = { yearStart: "2026-09-01", yearEnd: "2027-05-25" };
  const draft = { start: "2026-10-05", end: "2026-10-18", history: true };
  it("sets the period from the two dates, the whole year, or back to the default window", () => {
    expect(periodPatch("dates", draft, year)).toEqual(draft);
    expect(periodPatch("year", draft, year)).toEqual({
      start: year.yearStart,
      end: year.yearEnd,
      history: true,
    });
    expect(periodPatch("default", draft, year)).toEqual({
      start: "",
      end: "",
      history: true,
    });
  });
  it("sends no dates for the default window and the dates otherwise", () => {
    const patch = periodPatch("default", draft, year);
    const params = queueParams({ ...empty, ...patch }, "");
    expect(params.has("start")).toBe(false);
    expect(params.has("end")).toBe(false);
    const dates = queueParams({ ...empty, ...draft }, "");
    expect(dates.get("start")).toBe(draft.start);
    expect(dates.get("end")).toBe(draft.end);
  });
});

describe("homework period button after the server refused the dates", () => {
  const meta = {
    staff: true,
    filter: "submitted",
    window: "range" as const,
    start: "2026-09-01",
    end: "2027-05-25",
    yearStart: "2026-09-01",
    yearEnd: "2027-05-25",
    schoolToday: "2026-11-16",
  };
  const asked = { start: "2026-12-01", end: "2026-11-01" };
  it("names the dates that were asked for, not the last good answer", () => {
    const label = periodButtonLabel(meta, asked, true);
    expect(label).toContain(dateLabel(asked.start));
    expect(label).toContain(dateLabel(asked.end));
  });
  it("names the answer's period when nothing was refused or no dates were asked for", () => {
    expect(periodButtonLabel(meta, asked, false)).toBe(periodLabel(meta));
    expect(periodButtonLabel(meta, { start: "", end: "" }, true)).toBe(
      periodLabel(meta),
    );
    expect(periodButtonLabel(null, { start: "", end: "" }, false)).toBe("");
  });
});

describe("what a failed answer of the queue means", () => {
  const dates = { start: "2026-12-01", end: "2026-11-01" };
  it("blames the period only when the server refused the dates that were asked for", () => {
    expect(periodRefused(422, dates)).toBe(true);
    expect(periodRefused(409, dates)).toBe(false);
    expect(periodRefused(403, dates)).toBe(false);
    expect(periodRefused(undefined, dates)).toBe(false);
    expect(periodRefused(422, { start: "", end: "" })).toBe(false);
  });
  it("starts again from the first page only when a later page read again is gone", () => {
    expect(pageExpired(409, "page-two", true)).toBe(true);
    // The user asked for this page: the server's message is shown.
    expect(pageExpired(409, "page-two", false)).toBe(false);
    // The first page has nothing to fall back to.
    expect(pageExpired(409, "", true)).toBe(false);
    expect(pageExpired(422, "page-two", true)).toBe(false);
    expect(pageExpired(undefined, "page-two", true)).toBe(false);
  });
});

describe("homework queue list key", () => {
  const applied = { ...empty, filter: "submitted" };
  it("stays the same for the same page, so that a reload keeps the list on the screen", () => {
    expect(listKey("w", undefined, applied, "c1")).toBe(
      listKey("w", undefined, { ...applied }, "c1"),
    );
  });
  it("changes with the page, the conditions, the child and the workspace", () => {
    const keys = [
      listKey("w", undefined, applied, "c1"),
      listKey("w", undefined, applied, "c2"),
      listKey("w", undefined, applied, ""),
      listKey("w", undefined, { ...applied, search: "x" }, "c1"),
      listKey("w", "s1", applied, "c1"),
      listKey("v", undefined, applied, "c1"),
    ];
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe("leaving an opened homework", () => {
  const never = () => {
    throw new Error("must not ask");
  };
  it("is free when nothing is changed", () => {
    expect(mayLeave({ dirty: false, busy: false }, never)).toBe(true);
  });
  it("is refused without a question while something is being sent", () => {
    expect(mayLeave({ dirty: true, busy: true }, never)).toBe(false);
  });
  it("follows the answer when there are unsaved changes", () => {
    expect(mayLeave({ dirty: true, busy: false }, () => true)).toBe(true);
    expect(mayLeave({ dirty: true, busy: false }, () => false)).toBe(false);
  });
});

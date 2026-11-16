import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import HomeworkQueueTable from "./HomeworkQueueTable";
import type { QueueResult, QueueRow } from "./homeworkQueue";

const row = (over: Partial<QueueRow>): QueueRow => ({
  lessonKey: "task-1",
  studentId: "s1",
  student: "Примеров Данияр",
  subject: "Математика",
  group: "7А",
  preview: "Решить номера 1-5",
  dueDate: "2026-11-20",
  baseDueDate: "2026-11-20",
  lessonDate: "2026-11-16",
  extended: false,
  status: "not_submitted",
  submittedAt: null,
  late: false,
  assignmentChanged: false,
  overdue: false,
  deadlineClosed: false,
  submission: "online",
  readOnly: false,
  canReview: false,
  canSubmit: false,
  ...over,
});
const result = (items: QueueRow[], staff: boolean): QueueResult => ({
  items,
  nextCursor: null,
  staff,
  filter: staff ? "submitted" : "todo",
  window: "range",
  start: "2026-09-01",
  end: "2027-05-25",
  yearStart: "2026-09-01",
  yearEnd: "2027-05-25",
  schoolToday: "2026-11-16",
});
const render = (r: QueueResult, busy = false) =>
  renderToStaticMarkup(
    <HomeworkQueueTable result={r} busy={busy} onOpen={() => {}} />,
  );
const wholeTaskButtons = (html: string) =>
  html.match(/<button[^>]*data-row="[^"]*:\*"/g) ?? [];

describe("HomeworkQueueTable for the staff", () => {
  const staff = result(
    [
      row({ studentId: "s1", canReview: true }),
      row({
        studentId: "s2",
        student: "Примерова Алина",
        canReview: true,
      }),
      row({ lessonKey: "task-2", subject: "Физика", canReview: true }),
    ],
    true,
  );
  it("gives each task one button for the whole task, however many pupils it has", () => {
    expect(wholeTaskButtons(render(staff))).toHaveLength(2);
  });
  it("starts a new group where the task changes, not where the same task comes back", () => {
    const again = result(
      [
        row({ lessonKey: "task-1" }),
        row({ lessonKey: "task-2", studentId: "s2" }),
        row({ lessonKey: "task-1", studentId: "s3" }),
      ],
      true,
    );
    expect(wholeTaskButtons(render(again))).toHaveLength(3);
  });
  it("names every pupil of the task", () => {
    const html = render(staff);
    expect(html).toContain("Примеров Данияр");
    expect(html).toContain("Примерова Алина");
  });
});

describe("HomeworkQueueTable for the family", () => {
  it("names both pupils when a parent sees the works of two", () => {
    const html = render(
      result(
        [
          row({ studentId: "s1", canSubmit: true }),
          row({ studentId: "s2", student: "Примерова Алина" }),
        ],
        false,
      ),
    );
    expect(html).toContain("Примеров Данияр");
    expect(html).toContain("Примерова Алина");
  });
  it("has no button for the whole task: the family cannot read the roster", () => {
    const html = render(result([row({ canSubmit: true })], false));
    expect(wholeTaskButtons(html)).toHaveLength(0);
    expect(html).toContain("<button");
  });
});

describe("HomeworkQueueTable while a work is being edited", () => {
  it("disables every button of the staff and of the family list", () => {
    for (const staff of [true, false]) {
      const html = render(result([row({ canReview: true })], staff), true);
      const buttons = html.match(/<button[^>]*>/g) ?? [];
      expect(buttons.length).toBeGreaterThan(0);
      for (const button of buttons) expect(button).toContain("disabled");
    }
  });
  it("leaves them enabled otherwise", () => {
    const html = render(result([row({ canReview: true })], true));
    expect(html).not.toMatch(/<button[^>]*disabled/);
  });
});

import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import HomeworkQueueTable, { type OpenedWork } from "./HomeworkQueueTable";
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
type ButtonProps = {
  disabled?: boolean;
  "aria-label"?: string;
  onClick?: () => void;
  children?: ReactNode;
};
// The table has no hooks: its components are expanded into elements, so that the buttons can be found and pressed.
function buttonsOf(node: ReactNode, out: ReactElement<ButtonProps>[] = []) {
  if (Array.isArray(node)) {
    node.forEach((child) => buttonsOf(child, out));
    return out;
  }
  if (!isValidElement(node)) return out;
  const element = node as ReactElement<any>;
  if (typeof element.type === "function")
    return buttonsOf((element.type as any)(element.props), out);
  if (element.type === "button") out.push(element);
  return buttonsOf(element.props.children, out);
}
const textOf = (node: ReactNode): string =>
  Array.isArray(node)
    ? node.map(textOf).join("")
    : typeof node === "string" || typeof node === "number"
      ? String(node)
      : isValidElement(node)
        ? textOf((node.props as { children?: ReactNode }).children)
        : "";
// Presses every button of the list and returns what each one opens.
function pressAll(r: QueueResult, busy = false) {
  const opened: OpenedWork[] = [];
  const buttons = buttonsOf(
    <HomeworkQueueTable
      result={r}
      busy={busy}
      onOpen={(work) => opened.push(work)}
    />,
  );
  for (const button of buttons) button.props.onClick?.();
  return { buttons, opened };
}

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
  const wholeTasks = (r: QueueResult) =>
    pressAll(r).opened.filter((work) => work.studentId === undefined);
  it("gives each task one button for the whole task, however many pupils it has", () => {
    expect(wholeTasks(staff).map((work) => work.lessonKey)).toEqual([
      "task-1",
      "task-2",
    ]);
  });
  it("opens each pupil's own work from the pupil's row", () => {
    const pupils = pressAll(staff).opened.filter((work) => work.studentId);
    expect(pupils.map((work) => work.studentId)).toEqual(["s1", "s2", "s1"]);
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
    expect(wholeTasks(again)).toHaveLength(3);
  });
  it("gives every button of a task that comes back its own name, so that the focus returns to the right one", () => {
    const split = result(
      [
        row({ studentId: "s1" }),
        row({
          studentId: "s2",
          extended: true,
          dueDate: "2026-11-27",
        }),
        row({ lessonKey: "task-2", studentId: "s1" }),
        row({
          studentId: "s3",
          extended: true,
          dueDate: "2026-11-27",
        }),
      ],
      true,
    );
    const keys = pressAll(split).opened.map((work) => work.rowKey);
    expect(new Set(keys).size).toBe(keys.length);
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
    const { buttons, opened } = pressAll(
      result([row({ canSubmit: true })], false),
    );
    expect(buttons.length).toBeGreaterThan(0);
    expect(opened.every((work) => work.studentId)).toBe(true);
  });
  it("tells rows of one subject apart for a screen reader by date and pupil", () => {
    const { buttons } = pressAll(
      result(
        [
          row({ studentId: "s1", canSubmit: true }),
          row({
            studentId: "s2",
            student: "Примерова Алина",
            dueDate: "2026-11-25",
          }),
        ],
        false,
      ),
    );
    const names = buttons.map((button) => button.props["aria-label"]);
    expect(new Set(names).size).toBe(names.length);
    expect(names[1]).toContain("Примерова Алина");
  });
});

describe("HomeworkQueueTable buttons", () => {
  const lists = [true, false].map((staff) =>
    result([row({ canReview: true })], staff),
  );
  it("carry the visible word in their accessible name, for voice control", () => {
    for (const list of lists)
      for (const button of pressAll(list).buttons) {
        const name = button.props["aria-label"];
        if (name) expect(name).toContain(textOf(button.props.children));
      }
  });
  it("are all disabled while a work is being edited", () => {
    for (const list of lists) {
      const { buttons, opened } = pressAll(list, true);
      expect(buttons.length).toBeGreaterThan(0);
      for (const button of buttons) expect(button.props.disabled).toBe(true);
      void opened;
    }
  });
  it("are enabled otherwise", () => {
    for (const list of lists)
      for (const button of pressAll(list).buttons)
        expect(button.props.disabled).toBe(false);
  });
});

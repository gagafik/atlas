import { isValidElement, type ReactElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";
import {
  BulkPreviewTable,
  ClassesTable,
  LessonsTable,
  NotesTable,
} from "./AbsenceTables";
import type { AbsenceNote, ClassSetting } from "./absenceNotes";

const note = (over: Partial<AbsenceNote> = {}): AbsenceNote => ({
  id: "n1",
  studentId: "s1",
  studentName: "Алия Нурова",
  version: 1,
  status: "pending",
  start: "2026-10-19T00:00",
  end: "2026-10-19T23:59",
  created: "2026-10-18T10:00",
  ...over,
});
const cls = (over: Partial<ClassSetting> = {}): ClassSetting => ({
  id: "c1",
  name: "7А",
  classTeacher: "Анна Примерова",
  enabled: true,
  version: 1,
  updated: null,
  actor: null,
  canChange: true,
  ...over,
});

type Props = {
  onClick?: () => void;
  onChange?: (e: { target: { checked: boolean } }) => void;
  children?: ReactNode;
  type?: string;
  checked?: boolean;
  disabled?: boolean;
  href?: string;
  "aria-label"?: string;
};
// The tables have no hooks: their components are expanded into elements, so that rows and buttons can be found.
function find(
  node: ReactNode,
  tag: string,
  out: ReactElement<Props>[] = [],
): ReactElement<Props>[] {
  if (Array.isArray(node)) {
    node.forEach((child) => find(child, tag, out));
    return out;
  }
  if (!isValidElement(node)) return out;
  const element = node as ReactElement<any>;
  if (typeof element.type === "function")
    return find((element.type as any)(element.props), tag, out);
  if (element.type === tag) out.push(element);
  return find(element.props.children, tag, out);
}
const textOf = (node: ReactNode): string =>
  Array.isArray(node)
    ? node.map(textOf).join("")
    : typeof node === "string" || typeof node === "number"
      ? String(node)
      : isValidElement(node)
        ? typeof node.type === "function"
          ? textOf((node.type as any)(node.props))
          : textOf((node.props as { children?: ReactNode }).children)
        : "";
const bodyRows = (ui: ReactNode) =>
  find(find(ui, "tbody")[0]?.props.children, "tr");
const checkboxes = (ui: ReactNode) =>
  find(ui, "input").filter((i) => i.props.type === "checkbox");
const rowBoxes = (ui: ReactNode) =>
  checkboxes(ui).filter((c) => c.props["aria-label"]?.startsWith("Включить"));
const headerBox = (ui: ReactNode) =>
  checkboxes(ui).find((c) => c.props["aria-label"]?.startsWith("Выбрать"))!;

const names = {
  codes: [{ id: "ill", name: "Болезнь" }],
  subjects: [{ id: "m", name: "Математика" }],
};
const notesUi = (
  items: AbsenceNote[],
  over: Partial<Parameters<typeof NotesTable>[0]> = {},
) => (
  <NotesTable
    items={items}
    {...names}
    viewer={{ parent: false, reviewer: true }}
    onOpen={() => {}}
    {...over}
  />
);
const pick = (ids: string[] = [], action = "approve") => ({
  ids,
  action,
  onToggle: () => {},
  onToggleAll: () => {},
});

describe("NotesTable", () => {
  it("has one row per note and one button per row", () => {
    const ui = notesUi([note(), note({ id: "n2" }), note({ id: "n3" })]);
    expect(bodyRows(ui)).toHaveLength(3);
    for (const row of bodyRows(ui)) expect(find(row, "button")).toHaveLength(1);
  });
  it("names the button after the pupil and the period, and opens that note", () => {
    const opened: string[] = [];
    const ui = notesUi(
      [note({ start: "2026-10-19T08:00", end: "2026-10-19T12:30" })],
      { onOpen: (n) => opened.push(n.id) },
    );
    const button = find(ui, "button")[0];
    expect(button.props["aria-label"]).toBe(
      "Открыть записку: Алия Нурова, 19.10.2026 08:00–12:30",
    );
    button.props.onClick?.();
    expect(opened).toEqual(["n1"]);
  });
  it("shows the type, subject, who filed it and the status of a note", () => {
    const text = textOf(
      notesUi([
        note({ codeId: "ill", subjectId: "m", status: "approved" }),
        note({ id: "n2", origin: "school" }),
      ]),
    );
    expect(text).toContain("Болезнь");
    expect(text).toContain("Математика");
    expect(text).toContain("Принято");
    expect(text).toContain("Школа");
    expect(text).toContain("Все");
    expect(text).toContain("Без типа");
  });
  it("tells a parent which notes are theirs", () => {
    const text = textOf(
      notesUi([note(), note({ id: "n2", mine: false })], {
        viewer: { parent: true, reviewer: false },
      }),
    );
    expect(text).toContain("Вы");
    expect(text).toContain("Другой представитель");
  });
  it("offers no selection to someone who cannot decide", () => {
    expect(checkboxes(notesUi([note()]))).toHaveLength(0);
  });
  it("lets a reviewer pick only the notes the action applies to", () => {
    const items = [
      note({ id: "p1" }),
      note({ id: "a1", status: "approved" }),
      note({ id: "p2" }),
    ];
    expect(rowBoxes(notesUi(items, { pick: pick([]) }))).toHaveLength(2);
    expect(rowBoxes(notesUi(items, { pick: pick([], "revoke") }))).toHaveLength(
      1,
    );
  });
  it("checks the header box when everything that can be picked is picked", () => {
    const items = [note({ id: "p1" }), note({ id: "p2" })];
    const header = (ids: string[]) =>
      headerBox(notesUi(items, { pick: pick(ids) }));
    expect(header(["p1", "p2"]).props.checked).toBe(true);
    expect(header(["p1"]).props.checked).toBe(false);
    expect(header([]).props.checked).toBe(false);
  });
  it("disables the header box when nothing can be picked", () => {
    const ui = notesUi([note({ status: "rejected" })], { pick: pick() });
    expect(headerBox(ui).props.disabled).toBe(true);
  });
  it("stops offering more rows once fifty are picked", () => {
    const items = Array.from({ length: 52 }, (_, i) => note({ id: `n${i}` }));
    const ids = items.slice(0, 50).map((n) => n.id);
    const rows = rowBoxes(notesUi(items, { pick: pick(ids) }));
    expect(rows.filter((c) => c.props.disabled)).toHaveLength(2);
    expect(rows.filter((c) => c.props.checked)).toHaveLength(50);
  });
  it("reports a row being ticked", () => {
    const calls: [string, boolean][] = [];
    const ui = notesUi([note()], {
      pick: { ...pick(), onToggle: (id, on) => calls.push([id, on]) },
    });
    rowBoxes(ui)[0].props.onChange?.({ target: { checked: true } });
    expect(calls).toEqual([["n1", true]]);
  });
  it("says there are no notes once loading is over", () => {
    expect(textOf(notesUi([]))).toContain("Записок не найдено");
    expect(textOf(notesUi([], { loading: true }))).not.toContain(
      "Записок не найдено",
    );
    expect(find(notesUi([]), "table")).toHaveLength(0);
  });
  it("disables the row buttons while something loads", () => {
    const button = find(notesUi([note()], { busy: true }), "button")[0];
    expect(button.props.disabled).toBe(true);
  });
});

describe("ClassesTable", () => {
  it("has one row per class", () => {
    const ui = (
      <ClassesTable
        classes={[cls(), cls({ id: "c2", name: "7Б" })]}
        onSwitch={() => {}}
      />
    );
    expect(bodyRows(ui)).toHaveLength(2);
  });
  it("offers to switch off a class that takes notes and to switch on one that does not", () => {
    const ui = (
      <ClassesTable
        classes={[cls(), cls({ id: "c2", name: "7Б", enabled: false })]}
        onSwitch={() => {}}
      />
    );
    const buttons = find(ui, "button");
    expect(buttons.map((b) => textOf(b))).toEqual(["Выключить", "Включить"]);
    expect(buttons[0].props["aria-label"]).toContain("7А");
    expect(buttons[1].props["aria-label"]).toContain("7Б");
  });
  it("gives no button where the class cannot be changed", () => {
    const ui = (
      <ClassesTable classes={[cls({ canChange: false })]} onSwitch={() => {}} />
    );
    expect(find(ui, "button")).toHaveLength(0);
  });
  it("passes the class of the pressed row", () => {
    const got: string[] = [];
    const ui = (
      <ClassesTable
        classes={[cls(), cls({ id: "c2", name: "7Б" })]}
        onSwitch={(c) => got.push(c.id)}
      />
    );
    find(ui, "button")[1].props.onClick?.();
    expect(got).toEqual(["c2"]);
  });
  it("says when a class has no class teacher", () => {
    const ui = (
      <ClassesTable
        classes={[cls({ classTeacher: null })]}
        onSwitch={() => {}}
      />
    );
    expect(textOf(ui)).toContain("не назначен");
  });
  it("shows whether notes are on", () => {
    const text = textOf(
      <ClassesTable
        classes={[cls(), cls({ id: "c2", enabled: false })]}
        onSwitch={() => {}}
      />,
    );
    expect(text).toContain("включены");
    expect(text).toContain("выключены");
  });
});

describe("BulkPreviewTable", () => {
  it("shows what each note becomes, its reason and its files", () => {
    const ui = (
      <BulkPreviewTable
        fileUrl={(id) => `/f/${id}`}
        items={[
          {
            ...note({
              reason: "Болел",
              files: [{ id: "f1", filename: "справка.pdf", size: 10 }],
            }),
            nextStatus: "approved",
          },
        ]}
      />
    );
    expect(bodyRows(ui)).toHaveLength(1);
    const text = textOf(ui);
    expect(text).toContain("На рассмотрении → Принято");
    expect(text).toContain("Болел");
    expect(text).toContain("справка.pdf");
    expect(find(ui, "a")[0].props.href).toBe("/f/f1");
  });
});

describe("LessonsTable", () => {
  const lesson = (over = {}) => ({
    date: "2026-10-19",
    start: "08:30",
    end: "09:15",
    subject: "Математика",
    ...over,
  });
  const headers = (ui: ReactNode) =>
    find(find(ui, "thead")[0]?.props.children, "th").map((h) => textOf(h));
  it("has one row per lesson with its date, time and subject", () => {
    const ui = (
      <LessonsTable
        lessons={[lesson(), lesson({ date: "2026-10-20" })]}
        onEdge={() => {}}
      />
    );
    expect(bodyRows(ui)).toHaveLength(2);
    const text = textOf(ui);
    expect(text).toContain("2026-10-19");
    expect(text).toContain("08:30–09:15");
    expect(text).toContain("Математика");
  });
  it("shows the group column only when the lessons have a group", () => {
    const plain = <LessonsTable lessons={[lesson()]} onEdge={() => {}} />;
    const grouped = (
      <LessonsTable lessons={[lesson({ group: "7А" })]} onEdge={() => {}} />
    );
    expect(headers(plain)).not.toContain("Группа");
    expect(headers(grouped)).toContain("Группа");
    expect(textOf(grouped)).toContain("7А");
  });
  it("sets the first or the last lesson from the row buttons", () => {
    const calls: string[] = [];
    const ui = (
      <LessonsTable
        lessons={[lesson({ date: "2026-10-20" })]}
        onEdge={(l, which) => calls.push(`${which}:${l.date}`)}
      />
    );
    const buttons = find(ui, "button");
    expect(buttons.map((b) => textOf(b))).toEqual([
      "С этого урока",
      "По этот урок",
    ]);
    buttons[0].props.onClick?.();
    buttons[1].props.onClick?.();
    expect(calls).toEqual(["start:2026-10-20", "end:2026-10-20"]);
  });
  it("names the lesson on each row button, so that two rows are told apart", () => {
    const ui = (
      <LessonsTable
        lessons={[lesson(), lesson({ date: "2026-10-20", subject: "Химия" })]}
        onEdge={() => {}}
      />
    );
    const labels = find(ui, "button").map((b) => b.props["aria-label"]);
    expect(new Set(labels).size).toBe(4);
    expect(labels.every((l) => l?.includes("урок"))).toBe(true);
    expect(labels.filter((l) => l?.includes("Химия"))).toHaveLength(2);
  });
  it("shows nothing when there are no lessons", () => {
    expect(
      bodyRows(<LessonsTable lessons={[]} onEdge={() => {}} />),
    ).toHaveLength(0);
  });
});

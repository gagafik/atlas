import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import BehaviourNotesTable from "./BehaviourNotesTable";
import type { History, Note } from "./behaviourNotesModel";

const note = (over: Partial<Note>): Note => ({
  id: "n1",
  version: 1,
  status: "published",
  kind: "remark",
  day: "2026-11-16",
  categoryId: "c1",
  category: "Опоздание",
  text: "Опоздал на урок",
  author: "Примерова Алина",
  level: "teacher",
  levelTitle: "Учитель",
  studentId: "s1",
  student: "Примеров Данияр",
  className: "7А",
  requireAck: false,
  acknowledged: false,
  acknowledgedAt: null,
  acknowledgedBy: null,
  letter: false,
  letterNumber: "",
  lesson: "",
  ...over,
});
type Calls = {
  decided: [Note, boolean][];
  historyOf: Note[];
  acknowledged: Note[];
  toggled: string[];
};
const calls = (): Calls => ({
  decided: [],
  historyOf: [],
  acknowledged: [],
  toggled: [],
});
const table = (
  notes: Note[],
  seen: Calls,
  over: {
    staff?: boolean;
    canAcknowledge?: boolean;
    openId?: string | null;
    history?: { id: string; items: History } | null;
  } = {},
) => (
  <BehaviourNotesTable
    notes={notes}
    staff={over.staff ?? true}
    canAcknowledge={over.canAcknowledge ?? false}
    openId={over.openId ?? null}
    history={over.history ?? null}
    empty=""
    onToggle={(id) => seen.toggled.push(id)}
    onDecide={(n, approve) => seen.decided.push([n, approve])}
    onAcknowledge={(n) => seen.acknowledged.push(n)}
    onLetter={() => {}}
    onEdit={() => {}}
    onWithdraw={() => {}}
    onHistory={(n) => seen.historyOf.push(n)}
  />
);
type ButtonProps = {
  disabled?: boolean;
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
const buttonNamed = (ui: ReactElement, name: string) =>
  buttonsOf(ui).find((b) => textOf(b.props.children) === name);

describe("BehaviourNotesTable for the staff", () => {
  it("approves and rejects the note whose row holds the button", () => {
    const pending = note({ id: "n2", status: "pending", canDecide: true });
    const seen = calls();
    const ui = table([note({}), pending], seen);
    buttonNamed(ui, "Утвердить")!.props.onClick!();
    buttonNamed(ui, "Отклонить")!.props.onClick!();
    expect(seen.decided).toEqual([
      [pending, true],
      [pending, false],
    ]);
  });
  it("asks for the history of the note whose row holds the button", () => {
    const first = note({ id: "n1" });
    const second = note({ id: "n2" });
    const seen = calls();
    // The button is in the line that opens under a row.
    for (const opened of [first, second])
      buttonNamed(
        table([first, second], seen, { openId: opened.id }),
        "История",
      )!.props.onClick!();
    expect(seen.historyOf).toEqual([first, second]);
  });
  it("keeps editing and the history out of a row that is not opened", () => {
    const ui = table([note({ canChange: true })], calls());
    for (const name of ["Изменить", "Отозвать", "История"])
      expect(buttonNamed(ui, name)).toBeUndefined();
  });
  it("opens a row from its details button", () => {
    const seen = calls();
    buttonNamed(table([note({ id: "n7" })], seen), "Подробнее")!.props
      .onClick!();
    expect(seen.toggled).toEqual(["n7"]);
  });
  it("does not let a note be edited once the family has acknowledged it", () => {
    const seen = calls();
    const ui = table(
      [note({ canChange: true, requireAck: true, acknowledged: true })],
      seen,
      { openId: "n1" },
    );
    expect(buttonNamed(ui, "Изменить")!.props.disabled).toBe(true);
  });
  it("shows the history items only under the open row", () => {
    const items: History = [
      {
        seq: 1,
        actor: "Примерова Алина",
        created: "2026-11-16T08:00:00Z",
        action: "created",
        reason: "",
      },
      {
        seq: 2,
        actor: "Примерова Алина",
        created: "2026-11-16T09:00:00Z",
        action: "edited",
        reason: "Уточнение",
      },
    ];
    const history = { id: "n1", items };
    const notes = [note({ id: "n1" }), note({ id: "n2" })];
    const open = renderToStaticMarkup(
      table(notes, calls(), { openId: "n1", history }),
    );
    const closed = renderToStaticMarkup(table(notes, calls(), { history }));
    expect(open).toContain("Уточнение");
    expect(closed).not.toContain("Уточнение");
  });
});

describe("BehaviourNotesTable for the family", () => {
  const waiting = note({ requireAck: true, canChange: true });
  it("offers the acknowledgement to the parent and passes the note on", () => {
    const seen = calls();
    const ui = table([waiting], seen, { staff: false, canAcknowledge: true });
    buttonNamed(ui, "Ознакомлен")!.props.onClick!();
    expect(seen.acknowledged).toEqual([waiting]);
  });
  it("offers no acknowledgement to a pupil", () => {
    const ui = table([waiting], calls(), { staff: false });
    expect(buttonNamed(ui, "Ознакомлен")).toBeUndefined();
  });
  it("offers no editing, withdrawing or history, whatever the note says", () => {
    const ui = table([waiting], calls(), {
      staff: false,
      canAcknowledge: true,
      openId: waiting.id,
    });
    for (const name of ["Изменить", "Отозвать", "История"])
      expect(buttonNamed(ui, name)).toBeUndefined();
  });
});

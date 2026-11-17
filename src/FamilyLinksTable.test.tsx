import { isValidElement, type ReactElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";
import FamilyLinksTable from "./FamilyLinksTable";
import type { FamilyLink } from "./familyLinksModel";

const link = (over: Partial<FamilyLink> = {}): FamilyLink => ({
  parentId: "p1",
  studentId: "s1",
  parentName: "Анна Примерова",
  studentName: "Максим Вымыслов",
  version: 1,
  accountVersion: 1,
  relationship: "mother",
  verified: true,
  startDate: null,
  endDate: null,
  status: "active",
  activity: "active",
  legacy: false,
  accountActive: true,
  ...over,
});
const first = link();
const second = link({
  parentId: "p2",
  studentId: "s2",
  parentName: "Дмитрий Образцов",
  studentName: "Алина Макетова",
  accountActive: false,
});

type ButtonProps = { disabled?: boolean; onClick?: () => void };
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

const table = (rows: FamilyLink[], opened: FamilyLink[], busy = false) => (
  <FamilyLinksTable rows={rows} busy={busy} onOpen={(l) => opened.push(l)} />
);

describe("FamilyLinksTable", () => {
  it("opens the link whose row holds the button", () => {
    const opened: FamilyLink[] = [];
    const buttons = buttonsOf(table([first, second], opened));
    expect(buttons).toHaveLength(2);
    buttons[1].props.onClick!();
    buttons[0].props.onClick!();
    expect(opened).toEqual([second, first]);
  });
  it("has one button per link and none for an empty registry", () => {
    expect(buttonsOf(table([first], []))).toHaveLength(1);
    expect(buttonsOf(table([], []))).toHaveLength(0);
  });
  it("shows the names of the parent and the pupil in the row", () => {
    const text = textOf(
      (FamilyLinksTable as any)({ rows: [second], busy: false, onOpen() {} }),
    );
    expect(text).toContain(second.parentName);
    expect(text).toContain(second.studentName);
  });
  it("says that nothing was found instead of showing an empty table", () => {
    const ui = (FamilyLinksTable as any)({
      rows: [],
      busy: false,
      onOpen: () => {},
    });
    expect(textOf(ui).trim()).not.toBe("");
  });
  it("does not say that nothing was found while the registry is still coming", () => {
    const say = (status: "loading" | "ready" | "failed") =>
      textOf(
        (FamilyLinksTable as any)({
          rows: [],
          status,
          busy: false,
          onOpen: () => {},
        }),
      ).trim();
    // A load on the way, one that found nothing and one that failed each say their own thing.
    expect(say("loading")).not.toBe("");
    expect(say("failed")).not.toBe("");
    expect(new Set([say("loading"), say("ready"), say("failed")]).size).toBe(3);
  });
  it("shows the links that are there while the registry is being reloaded", () => {
    const ui = (
      <FamilyLinksTable
        rows={[first]}
        status="loading"
        busy={false}
        onOpen={() => {}}
      />
    );
    expect(buttonsOf(ui)).toHaveLength(1);
  });
  it("disables the buttons while the page is busy", () => {
    for (const b of buttonsOf(table([first, second], [], true)))
      expect(b.props.disabled).toBe(true);
  });
  it("shows what is placed under the table", () => {
    const ui = (
      <FamilyLinksTable rows={[first]} busy={false} onOpen={() => {}}>
        <button>ещё</button>
      </FamilyLinksTable>
    );
    expect(buttonsOf(ui)).toHaveLength(2);
  });
});

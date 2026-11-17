import { isValidElement, type ReactElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";
import AccessUsersTable from "./AccessUsersTable";
import type { Account } from "./auth";

const account = (over: Partial<Account> & { id: string }): Account => ({
  name: "Анна Примерова",
  login: "anna.p",
  role: "teacher",
  active: true,
  version: 1,
  workspaces: ["school"],
  grants: [],
  ...over,
});
const first = account({ id: "u1" });
const second = account({
  id: "u2",
  name: "Дмитрий Образцов",
  login: "dmitry.o",
  active: false,
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

const table = (rows: Account[], edited: Account[], busy = false) => (
  <AccessUsersTable rows={rows} busy={busy} onEdit={(a) => edited.push(a)} />
);

describe("AccessUsersTable", () => {
  it("opens the account whose row holds the button", () => {
    const edited: Account[] = [];
    const buttons = buttonsOf(table([first, second], edited));
    expect(buttons).toHaveLength(2);
    buttons[1].props.onClick!();
    buttons[0].props.onClick!();
    expect(edited).toEqual([second, first]);
  });
  it("has one button per account and none for an empty list", () => {
    expect(buttonsOf(table([first], []))).toHaveLength(1);
    expect(buttonsOf(table([], []))).toHaveLength(0);
  });
  it("says that nothing was found instead of showing an empty table", () => {
    const ui = (AccessUsersTable as any)({
      rows: [],
      busy: false,
      onEdit: () => {},
    });
    expect(textOf(ui).trim()).not.toBe("");
  });
  it("does not say that nothing was found while the accounts are still coming", () => {
    const say = (status: "loading" | "ready" | "failed") =>
      textOf(
        (AccessUsersTable as any)({
          rows: [],
          status,
          busy: false,
          onEdit: () => {},
        }),
      ).trim();
    // A load that is on the way and one that is done and found nothing look different; a failed one leaves the line to the error.
    expect(say("loading")).not.toBe("");
    expect(say("loading")).not.toBe(say("ready"));
    expect(say("failed")).toBe("");
  });
  it("shows the accounts that are there while the list is being reloaded", () => {
    const edited: Account[] = [];
    const ui = (
      <AccessUsersTable
        rows={[first]}
        status="loading"
        busy={false}
        onEdit={(a) => edited.push(a)}
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
      <AccessUsersTable rows={[first]} busy={false} onEdit={() => {}}>
        <button>ещё</button>
      </AccessUsersTable>
    );
    expect(buttonsOf(ui)).toHaveLength(2);
  });
});

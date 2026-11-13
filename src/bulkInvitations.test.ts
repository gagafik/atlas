import { expect, it } from "vitest";
import {
  invitationBatchCsv,
  registerInvitationsCsv,
  registerIssueCount,
  type BatchReceipt,
  type IssuedRow,
} from "./bulkInvitations";

it("exports only visible pending codes with recipient and exact children, escaping spreadsheet formulas", () => {
  const receipt: BatchReceipt = {
    id: 1,
    workspace: "demo",
    created: "2026-10-15",
    items: [
      {
        id: 1,
        key: "one",
        role: "parent",
        recipient: '=HYPERLINK("bad")',
        targets: [{ id: "s1", name: "Child, One", className: "10A" }],
        expires: "2026-10-22",
        status: "pending",
        code: "visible-code",
      },
      {
        id: 2,
        key: "two",
        role: "parent",
        recipient: "Revoked",
        targets: [],
        expires: "2026-10-22",
        status: "revoked",
        code: "revoked-code",
      },
      {
        id: 3,
        key: "three",
        role: "parent",
        recipient: "Hidden",
        targets: [],
        expires: "2026-10-22",
        status: "pending",
      },
    ],
  };
  const csv = invitationBatchCsv(receipt);
  expect(csv).toContain("visible-code");
  expect(csv).toContain("Child, One · 10A");
  expect(csv).toContain("'=HYPERLINK");
  expect(csv).not.toContain("revoked-code");
  expect(csv).not.toContain("Hidden");
  expect(csv.split("\r\n")).toHaveLength(2);
});

const issued = (
  id: number,
  recipient: string,
  className: string,
  code?: string,
): IssuedRow => ({
  id,
  key: `r${id}`,
  recipient,
  role: "parent",
  targets: className ? [{ id: `s${id}`, name: `Child ${id}`, className }] : [],
  expires: "2026-11-21",
  status: "pending",
  code,
});

it("orders a register handout by the class of the first child, numerically, then by parent", () => {
  const csv = registerInvitationsCsv({
    id: 1,
    workspace: "demo",
    created: "2026-11-14",
    kind: "register",
    items: [
      issued(1, "Zed", "10A", "c1"),
      issued(2, "Bob", "7B", "c2"),
      issued(3, "Amy", "7B", "c3"),
      issued(4, "Nobody", "", "c4"),
      issued(5, "Hidden", "3D"),
    ],
  });
  const lines = csv.split("\r\n").slice(1);
  expect(lines.map((l) => l.split(",")[1])).toEqual([
    '"Amy"',
    '"Bob"',
    '"Zed"',
    '"Nobody"',
  ]);
  expect(csv).toContain("Child 1 · 10A");
  expect(csv).not.toContain("Hidden");
});

it("counts exactly the invitations the chosen opt-ins allow", () => {
  const counts = {
    rows: 20,
    ready: 10,
    readyCovered: 2,
    partial: 3,
    partialCovered: 1,
    notFound: 2,
    ambiguous: 1,
    noChildren: 1,
    noName: 0,
    repeated: 0,
  };
  expect(registerIssueCount(counts, false, false)).toBe(10);
  expect(registerIssueCount(counts, true, false)).toBe(13);
  expect(registerIssueCount(counts, false, true)).toBe(12);
  expect(registerIssueCount(counts, true, true)).toBe(16);
});

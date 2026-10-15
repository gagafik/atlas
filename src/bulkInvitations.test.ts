import { expect, it } from "vitest";
import { invitationBatchCsv, type BatchReceipt } from "./bulkInvitations";

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

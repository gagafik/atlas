import { describe, expect, it } from "vitest";
import { unsavedMessage } from "./useUnsavedGuard";

describe("what is asked before unsaved results are dropped", () => {
  it("asks nothing when nothing is unsaved", () => {
    expect(unsavedMessage([])).toBe("");
  });
  it("asks about a row on its own and about a table when one is unsaved", () => {
    const row = unsavedMessage(["row"]);
    const table = unsavedMessage(["table-1"]);
    expect(row).not.toBe("");
    expect(table).not.toBe("");
    expect(table).not.toBe(row);
    expect(unsavedMessage(["row", "table-1"])).toBe(table);
  });
  it("leaves out the place that is being saved itself, but not the others", () => {
    expect(unsavedMessage(["row"], "row")).toBe("");
    // A row saves its own draft: typing in an open table is still at risk and is still asked about.
    expect(unsavedMessage(["row", "table-1"], "row")).toBe(
      unsavedMessage(["table-1"]),
    );
    expect(unsavedMessage(["table-1"], "row")).not.toBe("");
  });
});

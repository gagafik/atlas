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
});

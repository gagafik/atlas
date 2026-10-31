import { describe, it, expect } from "vitest";
import { focusRequest, spendFocus, type FocusRequest } from "./focusRequest";

describe("a notification's request to show one record", () => {
  it("is empty when the notification names no record", () => {
    expect(focusRequest(undefined)).toBeUndefined();
    expect(focusRequest("")).toBeUndefined();
  });
  it("is a new request every time, even for the same record", () => {
    const first = focusRequest("t1")!;
    const again = focusRequest("t1")!;
    expect(again).toEqual(first);
    // The panel reacts to a changed request; an unchanged one would leave the second «Открыть» without effect.
    expect(again).not.toBe(first);
  });
  it("keeps a newer request that arrived before the old one was spent", () => {
    const old = focusRequest("t1")!;
    const newer = focusRequest("t2")!;
    expect(spendFocus(newer, old)).toBe(newer);
    expect(spendFocus(undefined, old)).toBeUndefined();
  });
  it("is gone once taken, so a remounted panel finds nothing, and the same entry opens again", () => {
    let held: FocusRequest | undefined = focusRequest("t1");
    held = spendFocus(held, held!);
    // The workspace is changed now and the panel is mounted afresh: there is no request left to replay.
    expect(held).toBeUndefined();
    held = focusRequest("t1");
    expect(held).toEqual({ id: "t1" });
  });
});

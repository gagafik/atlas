import { describe, it, expect } from "vitest";
import {
  focusRequest,
  spendFocus,
  takeFocus,
  type FocusRequest,
} from "./focusRequest";

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

describe("a notification's request reaching a reader with unsaved work", () => {
  // The panel is stubbed with what takeFocus may ask of it, in the order it asks.
  function reader(agrees: boolean) {
    const calls: string[] = [];
    const spent: FocusRequest[] = [];
    return {
      calls,
      spent,
      panel: {
        spend: (r: FocusRequest) => {
          calls.push("spend");
          spent.push(r);
        },
        leave: () => {
          calls.push("leave");
          return agrees;
        },
        open: (id: string) => {
          calls.push("open");
          return `opened ${id}`;
        },
      },
    };
  }
  it("opens nothing when the reader stays, and still spends the request", () => {
    const { calls, spent, panel } = reader(false);
    const request = focusRequest("B")!;
    expect(takeFocus(request, panel)).toBeUndefined();
    expect(calls).not.toContain("open");
    // The request is gone all the same, so it cannot ask again when the panel is mounted afresh.
    expect(spent).toEqual([request]);
  });
  it("opens the other conversation once the reader agrees to leave, after asking", () => {
    const { calls, spent, panel } = reader(true);
    const request = focusRequest("B")!;
    expect(takeFocus(request, panel)).toBe("opened B");
    expect(calls.indexOf("leave")).toBeLessThan(calls.indexOf("open"));
    expect(spent).toEqual([request]);
  });
});

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
  // A reader of one conversation, typing a reply; leaving asks first and wipes the typed text only when the answer is yes.
  function reader(typed: string, agrees: boolean) {
    const state = { typed, shown: "A", asked: 0, spent: [] as FocusRequest[] };
    return {
      state,
      panel: {
        spend: (r: FocusRequest) => void state.spent.push(r),
        leave: () => {
          if (state.typed) {
            state.asked++;
            if (!agrees) return false;
            state.typed = "";
          }
          return true;
        },
        open: (id: string) => {
          state.shown = id;
          return id;
        },
      },
    };
  }
  it("keeps the typed reply and the conversation when the reader stays", () => {
    const { state, panel } = reader("Здравствуйте, ответ на", false);
    const request = focusRequest("B")!;
    expect(takeFocus(request, panel)).toBeUndefined();
    expect(state).toMatchObject({
      typed: "Здравствуйте, ответ на",
      shown: "A",
    });
    // The request is gone all the same, so it cannot ask again when the panel is mounted afresh.
    expect(state.spent).toEqual([request]);
  });
  it("opens the other conversation once the reader agrees to leave the reply", () => {
    const { state, panel } = reader("черновик", true);
    const request = focusRequest("B")!;
    expect(takeFocus(request, panel)).toBe("B");
    expect(state).toMatchObject({
      typed: "",
      shown: "B",
      asked: 1,
      spent: [request],
    });
  });
});

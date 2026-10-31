import { describe, expect, it } from "vitest";
import {
  choiceClosed,
  holdsChoice,
  keepTyped,
  limitsFor,
} from "./formAnswers";
describe("keepTyped", () => {
  const ids = ["a", "b", "c"];
  it("keeps only values changed since the answer was loaded", () => {
    const baseline = { a: "old", b: ["x"], c: "same" };
    const typed = { a: "new", b: ["x"], c: "same" };
    const fresh = { a: "server", b: ["y"], c: "other" };
    expect(keepTyped(fresh, typed, baseline, ids)).toEqual({
      a: "new",
      b: ["y"],
      c: "other",
    });
  });
  it("drops questions that no longer exist and treats arrays by content", () => {
    expect(
      keepTyped({ a: "f" }, { a: "t", gone: "t" }, { a: "f" }, ["a"]),
    ).toEqual({ a: "t" });
    expect(
      keepTyped({ b: ["1"] }, { b: ["2", "3"] }, { b: ["2"] }, ["b"]),
    ).toEqual({ b: ["2", "3"] });
    expect(keepTyped({ a: "f" }, { a: "f" }, { a: "f" }, ["a"])).toEqual({
      a: "f",
    });
  });
  it("keeps a typed value for a question the saved answer never had", () => {
    expect(keepTyped({}, { a: "fresh text" }, {}, ["a"])).toEqual({
      a: "fresh text",
    });
    expect(keepTyped({ a: "s" }, {}, {}, ["a"])).toEqual({ a: "s" });
  });
});

describe("limitsFor", () => {
  it("drops limits of removed choices and empty values and keeps the rest", () => {
    expect(
      limitsFor(["First", " Second ", ""], {
        First: 3,
        Second: 5,
        Gone: 2,
        Empty: null,
      }),
    ).toEqual({ First: 3, Second: 5 });
    expect(
      limitsFor(["First", "Second"], { First: undefined, Second: 0 }),
    ).toEqual({});
    expect(limitsFor(["First"], { First: 1.5 })).toEqual({});
    expect(limitsFor(["First"], undefined)).toEqual({});
  });
});

describe("choiceClosed", () => {
  it("closes a full choice unless the answer already holds a seat", () => {
    expect(choiceClosed(0, false)).toBe(true);
    expect(choiceClosed(0, true)).toBe(false);
    expect(choiceClosed(2, false)).toBe(false);
    expect(choiceClosed(undefined, false)).toBe(false);
  });
});

describe("holdsChoice", () => {
  it("recognises a choice named by the held answer, single or multiple", () => {
    expect(holdsChoice({ q: "A" }, "q", "A")).toBe(true);
    expect(holdsChoice({ q: "A" }, "q", "B")).toBe(false);
    expect(holdsChoice({ q: ["A", "C"] }, "q", "C")).toBe(true);
    expect(holdsChoice({ q: ["A", "C"] }, "q", "B")).toBe(false);
  });
  it("holds nothing without a held answer or for another question", () => {
    expect(holdsChoice(undefined, "q", "A")).toBe(false);
    expect(holdsChoice({}, "q", "A")).toBe(false);
    expect(holdsChoice({ other: "A" }, "q", "A")).toBe(false);
  });
});

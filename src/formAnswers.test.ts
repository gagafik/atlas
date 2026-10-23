import { describe, expect, it } from "vitest";
import { keepTyped } from "./formAnswers";
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

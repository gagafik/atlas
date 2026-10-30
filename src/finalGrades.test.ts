import { expect, it } from "vitest";
import { defaultDraft } from "./finalGrades";

const thresholds = { id: "a", version: 3, active: true };
const labels = { id: "b", version: 1, active: true };
const retired = { id: "c", version: 2, active: false };
const advice = {
  "a:3": { recommended: "Выше" },
  "b:1": { recommended: null },
  "c:2": { recommended: "Ниже" },
};

it("keeps an existing proposal unchanged", () => {
  const proposal = {
    scale: { id: "c", version: 2 },
    outcome: "exempt",
    label: "",
    comment: "Приказ",
    explanation: "Освобождён",
  };
  expect(defaultDraft(proposal, [thresholds], advice, "a")).toEqual({
    scaleId: "c",
    scaleVersion: 2,
    outcome: "exempt",
    label: "",
    comment: "Приказ",
    explanation: "Освобождён",
  });
});

it("preselects the only active scale and its recommended label", () => {
  expect(defaultDraft(null, [thresholds, retired], advice, "")).toEqual({
    scaleId: "a",
    scaleVersion: 3,
    outcome: "graded",
    label: "Выше",
    comment: "",
    explanation: "",
  });
});

it("does not guess among several active scales", () => {
  const draft = defaultDraft(null, [thresholds, labels], advice, "");
  expect([draft.scaleId, draft.scaleVersion, draft.label]).toEqual(["", 0, ""]);
});

it("prefers the last used scale and ignores an inactive one", () => {
  expect(defaultDraft(null, [thresholds, labels], advice, "b").scaleId).toBe(
    "b",
  );
  const draft = defaultDraft(null, [thresholds, labels, retired], advice, "c");
  expect(draft.scaleId).toBe("");
});

it("leaves the label empty on a scale without recommendations", () => {
  const draft = defaultDraft(null, [labels], advice, "");
  expect([draft.scaleId, draft.label]).toEqual(["b", ""]);
});

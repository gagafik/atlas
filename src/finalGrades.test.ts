import { expect, it } from "vitest";
import {
  changedRows,
  chunked,
  defaultDraft,
  eligibleFor,
  needsExplanation,
  tablePrefill,
  tableScale,
} from "./finalGrades";

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

const proposal = (scale: { id: string; version: number }, label: string) => ({
  scale,
  outcome: "graded",
  label,
  comment: "",
  explanation: "",
});
const row = (
  id: string,
  status: string,
  saved: ReturnType<typeof proposal> | null,
  recommended: string | null = null,
) => ({
  id,
  version: 1,
  status,
  proposal: saved,
  advice: { recommended },
});

it("shows a saved result as it is and otherwise the recommendation", () => {
  const saved = {
    ...proposal({ id: "a", version: 3 }, "Ниже"),
    comment: "Хорошо",
  };
  expect(tablePrefill(row("1", "draft", saved, "Выше"), thresholds)).toEqual({
    outcome: "graded",
    label: "Ниже",
    comment: "Хорошо",
    explanation: "",
  });
  expect(tablePrefill(row("2", "draft", null, "Выше"), thresholds).label).toBe(
    "Выше",
  );
  // A result saved on another scale is not carried over to this one.
  const other = proposal({ id: "z", version: 1 }, "Ниже");
  expect(tablePrefill(row("3", "draft", other, null), thresholds).label).toBe(
    "",
  );
});

it("starts a table on the scale the class already uses and never guesses among several", () => {
  const rows = [
    row("1", "draft", proposal({ id: "b", version: 1 }, "x")),
    row("2", "draft", proposal({ id: "b", version: 1 }, "x")),
    row("3", "draft", proposal({ id: "a", version: 3 }, "x")),
  ];
  expect(tableScale(rows, [thresholds, labels], "a")?.id).toBe("b");
  expect(tableScale([], [thresholds, labels], "")).toBeUndefined();
  expect(tableScale([], [thresholds, labels], "a")?.id).toBe("a");
  expect(tableScale([], [thresholds, retired], "")?.id).toBe("a");
  // A retired scale is never picked, even when the class used it.
  expect(
    tableScale(
      [row("4", "draft", proposal({ id: "c", version: 2 }, "x"))],
      [thresholds, retired],
      "",
    )?.id,
  ).toBe("a");
});

it("saves only draft rows with a result that differs from the stored one", () => {
  const rows = [
    row("fresh", "draft", null, "Выше"),
    row("same", "draft", proposal({ id: "a", version: 3 }, "Выше"), "Выше"),
    row("edited", "draft", proposal({ id: "a", version: 3 }, "Выше"), "Выше"),
    row("blank", "draft", null, null),
    row("sent", "submitted", null, "Выше"),
    row("exempt", "draft", null, null),
  ];
  const values = Object.fromEntries(
    rows.map((r) => [r.id, tablePrefill(r, thresholds)]),
  );
  values.edited = { ...values.edited, label: "Ниже", explanation: "Решение" };
  values.exempt = {
    ...values.exempt,
    outcome: "exempt",
    explanation: "Приказ",
  };
  expect(changedRows(rows, values, thresholds).map((r) => r.id)).toEqual([
    "fresh",
    "edited",
    "exempt",
  ]);
  // The same label on a newer edition of the scale is saved again.
  expect(
    changedRows(rows.slice(1, 2), values, { id: "a", version: 4, active: true })
      .length,
  ).toBe(1);
});

it("asks for an explanation exactly when the server does", () => {
  const advice = { recommended: "Выше" };
  const graded = (label: string) => ({
    outcome: "graded",
    label,
    comment: "",
    explanation: "",
  });
  expect(needsExplanation(graded("Ниже"), advice, thresholds)).toBe(true);
  expect(needsExplanation(graded("Выше"), advice, thresholds)).toBe(false);
  expect(
    needsExplanation(graded("Выше"), { recommended: null }, thresholds),
  ).toBe(true);
  expect(needsExplanation(graded(""), advice, thresholds)).toBe(false);
  expect(
    needsExplanation({ ...graded(""), outcome: "exempt" }, advice, thresholds),
  ).toBe(true);
  expect(
    needsExplanation(
      graded("Ниже"),
      { recommended: null },
      { ...labels, kind: "labels" },
    ),
  ).toBe(false);
  expect(
    needsExplanation({ ...graded(""), outcome: "exempt" }, advice, {
      ...labels,
      kind: "labels",
    }),
  ).toBe(true);
});

it("sends a mass action only the rows the server accepts for it", () => {
  const statuses = ["draft", "submitted", "approved", "published"];
  for (const [action, accepted] of [
    ["assign", "draft"],
    ["approve", "submitted"],
    ["publish", "approved"],
  ])
    expect(
      statuses.filter((status) => eligibleFor(action, { status })),
    ).toEqual([accepted]);
  expect(eligibleFor("withdraw", { status: "published" })).toBe(false);
});

it("splits a long list into ordered pieces of at most the given size", () => {
  const items = Array.from({ length: 250 }, (_, i) => i);
  const pieces = chunked(items, 100);
  expect(pieces.map((p) => p.length)).toEqual([100, 100, 50]);
  expect(pieces.flat()).toEqual(items);
  expect(chunked([], 100)).toEqual([]);
});

import { expect, it } from "vitest";
import {
  cellKey,
  changedRows,
  chunked,
  defaultDraft,
  eligibleFor,
  isForeign,
  markDirty,
  needsExplanation,
  submittableRows,
  tablePrefill,
  tableScale,
  visibleSelection,
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
});

it("keeps a result saved on another scale until the class is recalculated", () => {
  const other = {
    scale: { id: "z", version: 1 },
    outcome: "exempt",
    label: "",
    comment: "Приказ",
    explanation: "Освобождён по справке",
  };
  const foreign = row("3", "draft", other, "Выше");
  expect(tablePrefill(foreign, thresholds)).toEqual({
    outcome: "exempt",
    label: "",
    comment: "Приказ",
    explanation: "Освобождён по справке",
  });
  // The user asked for this scale: the recommendation of this scale takes over.
  expect(tablePrefill(foreign, thresholds, true)).toEqual({
    outcome: "graded",
    label: "Выше",
    comment: "",
    explanation: "",
  });
  expect(isForeign(foreign, thresholds)).toBe(true);
  expect(isForeign(row("4", "draft", null), thresholds)).toBe(false);
  expect(
    isForeign(row("5", "draft", proposal(thresholds, "Ниже")), thresholds),
  ).toBe(false);
});

it("never saves a decision made on another scale unless the class is recalculated", () => {
  // Three rows on scale a, one pupil exempted on scale z: saving the table must leave the exemption alone.
  const rows = [
    row("1", "draft", proposal({ id: "a", version: 3 }, "Ниже"), "Выше"),
    row("2", "draft", null, "Выше"),
    row(
      "3",
      "draft",
      {
        ...proposal({ id: "z", version: 1 }, ""),
        outcome: "exempt",
        explanation: "Освобождён по справке",
      },
      "Выше",
    ),
  ];
  const values = Object.fromEntries(
    rows.map((r) => [r.id, tablePrefill(r, thresholds)]),
  );
  expect(changedRows(rows, values, thresholds).map((r) => r.id)).toEqual(["2"]);
  const again = Object.fromEntries(
    rows.map((r) => [r.id, tablePrefill(r, thresholds, true)]),
  );
  expect(changedRows(rows, again, thresholds, true)).toEqual([
    expect.objectContaining({ id: "2", label: "Выше" }),
    expect.objectContaining({ id: "3", outcome: "graded", label: "Выше" }),
  ]);
  // Row 1 keeps its own saved grade on its own scale, so it is not saved again.
  expect(
    changedRows(rows, again, thresholds, true).map((r) => r.id),
  ).not.toContain("1");
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

it("sends the whole table for approval, including drafts saved earlier and left unchanged", () => {
  const mine = { id: "a", version: 3 };
  const rows = [
    row("saved", "draft", proposal(mine, "Выше"), "Выше"),
    row("carried", "draft", proposal(mine, "Ниже"), "Выше"),
    row("fresh", "draft", null, "Выше"),
    row("blank", "draft", null, null),
    row("sent", "submitted", proposal(mine, "Выше"), "Выше"),
    row("other", "draft", proposal({ id: "z", version: 1 }, "Ниже"), "Выше"),
  ];
  const values = Object.fromEntries(
    rows.map((r) => [r.id, tablePrefill(r, thresholds)]),
  );
  // Nothing was edited, yet saving a draft sends only the pupil without a stored result.
  expect(changedRows(rows, values, thresholds).map((r) => r.id)).toEqual([
    "fresh",
  ]);
  // Sending for approval takes every draft with a result, but not a blank row, a sent row or a result saved on another scale.
  expect(submittableRows(rows, values, thresholds).map((r) => r.id)).toEqual([
    "saved",
    "carried",
    "fresh",
  ]);
  const again = Object.fromEntries(
    rows.map((r) => [r.id, tablePrefill(r, thresholds, true)]),
  );
  expect(
    submittableRows(rows, again, thresholds, true).map((r) => r.id),
  ).toEqual(["saved", "carried", "fresh", "other"]);
  // What is sent is what the table shows, with the version the server expects.
  expect(submittableRows(rows, values, thresholds)[0]).toEqual({
    id: "saved",
    version: 1,
    outcome: "graded",
    label: "Выше",
    comment: "",
    explanation: "",
  });
});

it("never acts on a ticked cell that is no longer on screen", () => {
  const cells = [
    { classId: "10A", subjectId: "eng" },
    { classId: "10A", subjectId: "math" },
    { classId: "10D", subjectId: "eng" },
  ];
  const selected = [cellKey(cells[0]), cellKey(cells[2])];
  expect(visibleSelection(selected, cells)).toEqual(selected);
  // The class was switched to 10D: the tick on 10A stays in the state but must not reach the action.
  expect(visibleSelection(selected, [cells[2]])).toEqual([cellKey(cells[2])]);
  expect(visibleSelection(selected, [])).toEqual([]);
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

it("keeps the unsaved mark of one table when another table is saved or closed", () => {
  // The matrix and the report-card steps each have a table open; both hold typed results.
  let keys: readonly string[] = [];
  keys = markDirty(keys, "matrix", true);
  keys = markDirty(keys, "steps", true);
  expect(keys.length).toBe(2);
  // The table in the steps is saved (or closed): the matrix still has something to lose.
  keys = markDirty(keys, "steps", false);
  expect(keys).toEqual(["matrix"]);
  // A table that never held anything cannot clear the mark of another one.
  expect(markDirty(keys, "other", false)).toEqual(["matrix"]);
  expect(markDirty(keys, "matrix", true)).toEqual(["matrix"]);
  expect(markDirty(keys, "matrix", false)).toEqual([]);
});

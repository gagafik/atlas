// Names of statuses, outcomes and history actions as the school staff reads them.
export const names: Record<string, string> = {
  draft: "Черновик",
  submitted: "На утверждении",
  approved: "Утверждено",
  published: "Опубликовано",
  graded: "Оценка",
  not_assessed: "Не аттестован",
  exempt: "Освобождён",
  assign: "Назначен ответственный",
  saved: "Черновик сохранён",
  approve: "Утверждение",
  return: "Возврат на исправление",
  publish: "Публикация",
  reopen: "Открыто исправление",
  withdraw: "Публикация отозвана",
};
export function finalLabel(p: { outcome: string; label: string }) {
  return p.outcome === "graded" ? p.label : names[p.outcome];
}
type ScaleRef = { id: string; version: number; active: boolean };
type ProposalRef = {
  scale: { id: string; version: number };
  outcome: string;
  label: string;
  comment: string;
  explanation: string;
};
export type FinalDraft = {
  scaleId: string;
  scaleVersion: number;
  outcome: string;
  label: string;
  comment: string;
  explanation: string;
};

// Only these actions carry a reason; the others must never reuse an old one.
export const needsReason = (action: string) =>
  ["return", "reopen", "withdraw"].includes(action);

export function defaultDraft(
  proposal: ProposalRef | null,
  scales: ScaleRef[],
  recommendations: Record<string, { recommended: string | null }>,
  lastScaleId: string,
): FinalDraft {
  if (proposal)
    return {
      scaleId: proposal.scale.id,
      scaleVersion: proposal.scale.version,
      outcome: proposal.outcome,
      label: proposal.label,
      comment: proposal.comment,
      explanation: proposal.explanation,
    };
  // The last scale used, or the only active one; with several choices the user decides.
  const active = scales.filter((s) => s.active);
  const scale =
    active.find((s) => s.id === lastScaleId) ||
    (active.length === 1 ? active[0] : undefined);
  return {
    scaleId: scale?.id || "",
    scaleVersion: scale?.version || 0,
    outcome: "graded",
    label: scale
      ? recommendations[`${scale.id}:${scale.version}`]?.recommended || ""
      : "",
    comment: "",
    explanation: "",
  };
}

// ---- Table entry: one class and subject at a time -------------------------

export type TableEntry = {
  outcome: string;
  label: string;
  comment: string;
  explanation: string;
};
type SavedProposal = TableEntry & { scale: { id: string; version: number } };
export type TableRowLike = {
  id: string;
  version: number;
  status: string;
  proposal: SavedProposal | null;
  advice?: { recommended: string | null };
};
type ScaleLike = ScaleRef & { kind?: string };

// The scale a table starts with: the one already used most in this class, else the last used, else the only active one.
export function tableScale<S extends ScaleRef>(
  rows: { proposal: { scale: { id: string } } | null }[],
  scales: S[],
  lastScaleId: string,
): S | undefined {
  const active = scales.filter((s) => s.active);
  const used = new Map<string, number>();
  for (const r of rows)
    if (r.proposal)
      used.set(r.proposal.scale.id, (used.get(r.proposal.scale.id) || 0) + 1);
  const common = [...used.entries()]
    .filter(([id]) => active.some((s) => s.id === id))
    .sort((a, b) => b[1] - a[1])[0];
  return (
    active.find((s) => s.id === common?.[0]) ||
    active.find((s) => s.id === lastScaleId) ||
    (active.length === 1 ? active[0] : undefined)
  );
}

// A result saved on another scale is a decision of its own: the table keeps it until the user recalculates the class on this scale.
export const isForeign = (
  row: Pick<TableRowLike, "proposal">,
  scale: ScaleRef,
) => !!row.proposal && row.proposal.scale.id !== scale.id;

// What a row shows: its saved result, otherwise the computed recommendation (nothing is saved by showing it).
// With `recalculate` a result saved on another scale is replaced by the recommendation of this one.
export function tablePrefill(
  row: TableRowLike,
  scale: ScaleRef,
  recalculate = false,
): TableEntry {
  const p = row.proposal;
  if (p && (p.scale.id === scale.id || !recalculate))
    return {
      outcome: p.outcome,
      label: p.label,
      comment: p.comment,
      explanation: p.explanation,
    };
  return {
    outcome: "graded",
    label: row.advice?.recommended || "",
    comment: "",
    explanation: "",
  };
}

// The server asks for a reason unless the grade is the recommendation or a hand-picked grade on a labels scale.
export function needsExplanation(
  entry: TableEntry,
  advice: { recommended: string | null } | undefined,
  scale: ScaleLike,
): boolean {
  if (entry.outcome !== "graded") return true;
  if (!entry.label || scale.kind === "labels") return false;
  return !advice?.recommended || entry.label !== advice.recommended;
}

const filled = (e: TableEntry) => e.outcome !== "graded" || !!e.label;

type Pending = TableEntry & { id: string; version: number };
function pending(
  rows: TableRowLike[],
  values: Record<string, TableEntry>,
  scale: ScaleRef,
  recalculate: boolean,
  everything: boolean,
): Pending[] {
  const result: Pending[] = [];
  for (const r of rows) {
    const v = values[r.id];
    if (r.status !== "draft" || !v || !filled(v)) continue;
    if (!recalculate && isForeign(r, scale)) continue;
    const p = r.proposal;
    const same =
      !!p &&
      p.scale.id === scale.id &&
      p.scale.version === scale.version &&
      p.outcome === v.outcome &&
      p.label === v.label &&
      p.comment === v.comment &&
      p.explanation === v.explanation;
    if (everything || !same)
      result.push({
        id: r.id,
        version: r.version,
        outcome: v.outcome,
        label: v.label,
        comment: v.comment,
        explanation: v.explanation,
      });
  }
  return result;
}

// The draft rows that have a result and differ from what is stored. A pre-filled grade counts once the user saves.
// A row saved on another scale is left alone unless the class is recalculated on this one.
export const changedRows = (
  rows: TableRowLike[],
  values: Record<string, TableEntry>,
  scale: ScaleRef,
  recalculate = false,
) => pending(rows, values, scale, recalculate, false);

// Sending for approval takes the whole table: drafts saved earlier and left as they are go with the edited ones.
export const submittableRows = (
  rows: TableRowLike[],
  values: Record<string, TableEntry>,
  scale: ScaleRef,
  recalculate = false,
) => pending(rows, values, scale, recalculate, true);

// Several tables can be open at once (the matrix and the report-card steps), each with its own unsaved edits.
// The panel keeps the keys of the tables that hold some, so one table being saved or reloaded never hides the others.
export function markDirty(
  keys: readonly string[],
  key: string,
  dirty: boolean,
): readonly string[] {
  const has = keys.includes(key);
  if (dirty) return has ? keys : [...keys, key];
  return has ? keys.filter((k) => k !== key) : keys;
}

// ---- Mass actions from the class x subject matrix -------------------------

const NEEDS: Record<string, string> = {
  assign: "draft",
  approve: "submitted",
  publish: "approved",
};
// Only rows in the status the server accepts for the action are sent, so a whole class is never refused for one stray row.
export const eligibleFor = (action: string, row: { status: string }) =>
  NEEDS[action] === row.status;

// A cell of the class x subject matrix as it is kept in a selection.
export const cellKey = (c: { classId: string; subjectId: string }) =>
  JSON.stringify([c.classId, c.subjectId]);

// Only cells that are on screen can be acted on: a tick made before the class was switched must never reach rows nobody sees.
export const visibleSelection = (
  selected: string[],
  shown: { classId: string; subjectId: string }[],
) => {
  const keys = new Set(shown.map(cellKey));
  return selected.filter((k) => keys.has(k));
};

export function chunked<T>(items: T[], size: number): T[][] {
  const result: T[][] = [];
  for (let i = 0; i < items.length; i += size)
    result.push(items.slice(i, i + size));
  return result;
}

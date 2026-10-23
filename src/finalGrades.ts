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

// Unsaved answers survive loading a fresh version of the form.
export function keepTyped<T>(
  fresh: Record<string, T>,
  typed: Record<string, T>,
  baseline: Record<string, T>,
  questionIds: string[],
): Record<string, T> {
  const result = { ...fresh };
  for (const id of questionIds)
    if (
      id in typed &&
      JSON.stringify(typed[id]) !== JSON.stringify(baseline[id])
    )
      result[id] = typed[id];
  return result;
}

// A seat limit belongs to a choice that still exists and holds a whole number of seats: renamed, removed or emptied choices lose theirs.
export function limitsFor(
  choices: string[],
  limits: Record<string, number | null | undefined> | undefined,
): Record<string, number> {
  const kept = new Set(choices.map((c) => c.trim()).filter(Boolean));
  return Object.fromEntries(
    Object.entries(limits ?? {}).filter(
      (entry): entry is [string, number] =>
        kept.has(entry[0]) && Number.isInteger(entry[1]) && entry[1]! >= 1,
    ),
  );
}

// A choice with no seats left is closed, except for an answer that already holds one of them.
export function choiceClosed(left: number | undefined, held: boolean) {
  return left === 0 && !held;
}

// A held answer names the choices whose seats it keeps: one value for a single choice, a list for several.
export function holdsChoice(
  held: Record<string, unknown> | undefined,
  questionId: string,
  choice: string,
): boolean {
  const value = held?.[questionId];
  return Array.isArray(value) ? value.includes(choice) : value === choice;
}

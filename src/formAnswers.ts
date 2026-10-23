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

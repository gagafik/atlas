export interface QueueFilters {
  start: string;
  end: string;
  filter: string;
  history: boolean;
  search: string;
}
// Without dates the server opens pupils and parents on its default window, so empty fields are never sent.
export function queueParams(
  applied: QueueFilters,
  cursor: string,
): URLSearchParams {
  const params = new URLSearchParams({
    history: String(applied.history),
    search: applied.search,
  });
  for (const name of ["start", "end", "filter"] as const)
    if (applied[name]) params.set(name, applied[name]);
  if (cursor) params.set("cursor", cursor);
  return params;
}

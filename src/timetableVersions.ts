/** Adds the school day from which a state change applies; empty keeps the whole current timetable period. */
export function withEffectiveFrom(path: string, effectiveFrom: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(effectiveFrom)
    ? `${path}${path.includes("?") ? "&" : "?"}effectiveFrom=${effectiveFrom}`
    : path;
}

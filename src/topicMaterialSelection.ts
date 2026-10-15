import type { MaterialSnapshot } from "./teachingMaterials";

// Selecting a topic does not erase independent lesson materials. Checking a
// different version explicitly replaces that material; unchecked ones stay put.
export function mergeTopicMaterials(
  existing: MaterialSnapshot[],
  selected: MaterialSnapshot[],
) {
  const replacements = new Map(selected.map((item) => [item.id, item]));
  return [
    ...existing.map((item) => replacements.get(item.id) || item),
    ...selected.filter((item) => !existing.some((old) => old.id === item.id)),
  ];
}

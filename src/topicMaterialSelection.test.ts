import { expect, it } from "vitest";
import { mergeTopicMaterials } from "./topicMaterialSelection";
import type { MaterialSnapshot } from "./teachingMaterials";

const material = (id: string, version = 1): MaterialSnapshot => ({
  id,
  version,
  title: id,
  subject: "",
  text: "",
  topics: [],
  files: [],
});

it("keeps existing lesson material versions unless their replacement is explicitly selected", () => {
  const independent = material("independent"),
    old = material("topic", 3),
    extra = material("new");
  const existing = [independent, old];
  expect(mergeTopicMaterials(existing, [extra])).toEqual([
    independent,
    old,
    extra,
  ]);
  expect(mergeTopicMaterials(existing, [material("topic", 1), extra])).toEqual([
    independent,
    material("topic", 1),
    extra,
  ]);
  expect(mergeTopicMaterials(existing, [])).toEqual(existing);
  expect(existing).toEqual([independent, old]);
});

it("counts unique materials without silently trimming an overflowing selection", () => {
  const existing = Array.from({ length: 10 }, (_, i) => material(String(i)));
  expect(mergeTopicMaterials(existing, [material("0", 2)])).toHaveLength(10);
  expect(mergeTopicMaterials(existing, [material("new")])).toHaveLength(11);
});

import { lazy, Suspense } from "react";
import type { MaterialSnapshot } from "./teachingMaterials";
const Panel = lazy(() => import("./TeachingMaterialsPanel"));
export default function TeachingMaterialsLoader(props: {
  workspace: string;
  onChoose?: (item: MaterialSnapshot) => void;
}) {
  return (
    <Suspense fallback={<p role="status">Загружаем библиотеку материалов…</p>}>
      <Panel {...props} />
    </Suspense>
  );
}

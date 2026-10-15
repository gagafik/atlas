import { lazy, Suspense } from "react";
const Panel = lazy(() => import("./CurriculumPanel"));
export default function CurriculumLoader(props: { workspace: string }) {
  return (
    <Suspense fallback={<p role="status">Загружаем учебные планы…</p>}>
      <Panel {...props} />
    </Suspense>
  );
}

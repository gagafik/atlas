import { lazy, Suspense } from "react";

const QuizzesPanel = lazy(() => import("./QuizzesPanel"));

export default function QuizzesLoader({ workspace }: { workspace: string }) {
  return (
    <Suspense fallback={<p role="status">Загружаем контрольные и тесты…</p>}>
      <QuizzesPanel workspace={workspace} />
    </Suspense>
  );
}

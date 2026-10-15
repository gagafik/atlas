import { useEffect, useRef, useState } from "react";
import { startPolling } from "./polling";
import { LiveMaterials, LiveSolution } from "./LiveLessonMaterials";

export default function LiveProjector({
  lesson,
  workspace,
}: {
  lesson: string;
  workspace: string;
}) {
  const [data, setData] = useState<any>(null),
    [error, setError] = useState("");
  const [selected, setSelected] = useState("current"),
    [retry, setRetry] = useState(0);
  const [serverOffset, setServerOffset] = useState(0),
    [clock, setClock] = useState(Date.now());
  const page = useRef<HTMLElement>(null);
  useEffect(() => {
    let active = true,
      etag = "",
      terminal = false;
    const controller = new AbortController();
    setData(null);
    setError("");
    const poll = startPolling(
      async () => {
        try {
          const query = new URLSearchParams({ workspace });
          if (selected !== "current") query.set("questionIndex", selected);
          const r = await fetch(
            `/api/live-lessons/${encodeURIComponent(lesson)}/projection?${query}`,
            {
              signal: controller.signal,
              headers: etag ? { "If-None-Match": etag } : {},
            },
          );
          if (!active) return false;
          const server = Date.parse(r.headers.get("X-Atlas-Server-Time") || "");
          if (Number.isFinite(server)) setServerOffset(server - Date.now());
          if (r.status === 304) {
            setError("");
            return true;
          }
          if (!r.ok) {
            setData(null);
            etag = "";
            const body = await r.json();
            throw new Error(
              typeof body.detail === "string"
                ? body.detail
                : "Не удалось открыть экран занятия.",
            );
          }
          const value = await r.json();
          if (!active) return false;
          terminal = ["ended", "cancelled"].includes(value.status);
          setData(value);
          setError("");
          etag = r.headers.get("ETag") || "";
          return true;
        } catch (e: any) {
          if (active)
            setError(e.message || "Связь потеряна. Повторяем подключение…");
          return false;
        }
      },
      { interval: () => (terminal ? 30000 : 4000) },
    );
    return () => {
      active = false;
      controller.abort();
      poll.stop();
    };
  }, [lesson, workspace, selected, retry]);
  useEffect(() => {
    const t = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const round = data?.round;
  const left = round
    ? Math.max(
        0,
        Math.ceil((Date.parse(round.deadline) - clock - serverOffset) / 1000),
      )
    : 0;
  return (
    <main ref={page} className="live-projector">
      <header>
        <span>АТЛАС · ЭКРАН ЗАНЯТИЯ</span>
        <div>
          <label>
            Показать{" "}
            <select
              aria-label="Вопрос на проекторе"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="current">Текущий вопрос</option>
              {data?.opened.map((i: number) => (
                <option value={String(i)} key={i}>
                  Вопрос {i + 1}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => setRetry((n) => n + 1)}>Обновить экран</button>
          <button
            onClick={async () => {
              try {
                if (document.fullscreenElement) await document.exitFullscreen();
                else await page.current?.requestFullscreen();
              } catch {
                setError("Полноэкранный режим недоступен в этом браузере.");
              }
            }}
          >
            Полный экран
          </button>
        </div>
      </header>
      {error && (
        <p className="projection-error" role="alert">
          {error} Экран может быть неактуален.
        </p>
      )}
      {!data && !error && <p>Загрузка занятия…</p>}
      {data && (
        <>
          <h1>{data.title}</h1>
          {data.status === "cancelled" && <p>Занятие отменено.</p>}
          {data.status === "ended" && <p>Занятие завершено.</p>}
          {!round ? (
            <>
              <h2>Ждём первый вопрос</h2>
              <p className="live-media-text">{data.instructions}</p>
            </>
          ) : (
            <>
              <span className="projection-phase">
                Вопрос {round.index + 1} из {data.questionCount} ·{" "}
                {round.phase === "revealed"
                  ? "Разбор"
                  : round.phase === "open" && left > 0
                    ? `Осталось ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`
                    : "Приём ответов завершён"}
              </span>
              <h2 className="live-media-text">{round.question.text}</h2>
              <LiveMaterials
                items={round.materials}
                workspace={workspace}
                lesson={lesson}
                index={round.index}
                projection
              />
              <ol className="projection-options" type="A">
                {round.question.options.map((o: any) => (
                  <li key={o.id}>{o.text}</li>
                ))}
              </ol>
              {round.solution && (
                <section className="projection-solution">
                  <h3>Разбор</h3>
                  <LiveSolution question={round.solution} />
                  <LiveMaterials
                    items={round.solutionMaterials}
                    workspace={workspace}
                    lesson={lesson}
                    index={round.index}
                    part="solutionMaterials"
                    projection
                  />
                </section>
              )}
            </>
          )}
        </>
      )}
    </main>
  );
}

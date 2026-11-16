import { useState } from "react";
import {
  isActiveJob,
  JOB_PHASE,
  JOB_STATUS,
  type Preparations,
} from "./usePreparations";

type Period = { id: string; name: string; version: number; status: string };

export default function GradebookPreparationPanel({
  periods,
  preparations,
  initialPeriodId = "",
  onOpen,
}: {
  periods: Period[];
  // The jobs and the actions on them; the screen holds one instance, so the first view and the settings show the same jobs.
  preparations: Preparations;
  initialPeriodId?: string;
  onOpen: (id: string) => Promise<void>;
}) {
  const { jobs, busy, error, action, prepare, cancel, refresh } = preparations;
  const [periodId, setPeriodId] = useState(
    periods.some((p) => p.id === initialPeriodId && p.status === "closed")
      ? initialPeriodId
      : "",
  );
  async function start() {
    const period = periods.find((p) => p.id === periodId);
    if (!period) return;
    const bookId = await prepare(period);
    if (bookId) await onOpen(bookId);
  }
  return (
    <section className="gradebook-preparations">
      <h3>Подготовка ведомостей</h3>
      <p>
        Расчёт выполняется в фоне. Можно закрыть этот раздел и продолжать
        работу. Состояние задания сохраняется при перезапуске сервера.
        Одновременно считаются одна ведомость или одно расписание.
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <fieldset disabled={busy}>
        <div className="dated-toolbar">
          <label className="field">
            Закрытый период
            <select
              value={periodId}
              onChange={(e) => setPeriodId(e.target.value)}
            >
              <option value="">Выберите</option>
              {periods
                .filter((p) => p.status === "closed")
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </select>
          </label>
          <button
            disabled={
              !periodId ||
              jobs.some(
                (j) =>
                  isActiveJob(j) &&
                  j.periodId === periodId &&
                  j.closureVersion ===
                    periods.find((p) => p.id === periodId)?.version,
              )
            }
            onClick={() => void start()}
          >
            Подготовить ведомость
          </button>
          <button onClick={() => refresh()}>Обновить подготовку</button>
        </div>
        <p>
          Ведомость включает учеников из известного состава. Неполные списки
          классов нужно проверить в контроле журналов.
        </p>
        {!jobs.length && <p>Заданий подготовки пока нет.</p>}
        {jobs.map((job) => (
          <article className="absence-card" key={job.id}>
            <h4>
              {job.periodName} · закрытие {job.closureVersion}
            </h4>
            <p>
              {JOB_STATUS[job.status]} · {JOB_PHASE[job.phase] || job.phase}
              {job.attempts > 1 ? ` · запуск ${job.attempts}` : ""}
            </p>
            {isActiveJob(job) && job.total > 0 && (
              <>
                <progress
                  max={job.total}
                  value={job.completed}
                  aria-label={`${JOB_PHASE[job.phase] || "Подготовка"}: ${job.periodName}`}
                />
                <p>
                  {job.completed.toLocaleString("ru-RU")} из{" "}
                  {job.total.toLocaleString("ru-RU")} на текущем этапе
                </p>
              </>
            )}
            <p>{job.message}</p>
            {job.status === "succeeded" && (
              <button onClick={() => void action(() => onOpen(job.bookId))}>
                Открыть готовую ведомость
              </button>
            )}
            {isActiveJob(job) && (
              <button
                disabled={job.status === "cancelling"}
                onClick={() => void cancel(job)}
              >
                Отменить подготовку
              </button>
            )}
            {["failed", "stale", "cancelled"].includes(job.status) && (
              <p>
                Чтобы повторить подготовку, выберите актуальное закрытие периода
                выше.
              </p>
            )}
          </article>
        ))}
        <p>Показаны активные задания и последние завершённые, до 50 записей.</p>
      </fieldset>
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { startPolling } from "./polling";

type Period = { id: string; name: string; version: number; status: string };
type Job = {
  id: string;
  periodId: string;
  periodName: string;
  closureVersion: number;
  status: string;
  phase: string;
  completed: number;
  total: number;
  attempts: number;
  message: string;
  bookId: string;
};
const active = (job: Job) =>
  ["queued", "running", "cancelling"].includes(job.status);
const statuses: Record<string, string> = {
  queued: "В очереди",
  running: "Подготовка",
  cancelling: "Остановка",
  cancelled: "Отменено",
  succeeded: "Готово",
  failed: "Не завершено",
  stale: "Источник или доступ изменился",
};
const phases: Record<string, string> = {
  queued: "Ожидание",
  waiting: "Ожидание исполнителя",
  schedule: "Составы и уроки",
  journals: "Работы из архива",
  rows: "Итоговые строки",
  saving: "Сохранение",
  done: "Завершено",
};

export default function GradebookPreparationPanel({
  workspace,
  periods,
  onOpen,
}: {
  workspace: string;
  periods: Period[];
  onOpen: (id: string) => Promise<void>;
}) {
  const [periodId, setPeriodId] = useState(""),
    [jobs, setJobs] = useState<Job[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const pending = useRef(false),
    epoch = useRef(0),
    mounted = useRef(true),
    hasActive = useRef(false),
    poll = useRef<ReturnType<typeof startPolling> | null>(null);
  useEffect(() => {
    mounted.current = true;
    poll.current = startPolling(
      async () => {
        if (pending.current) return;
        const version = epoch.current;
        try {
          const result = await api("final-grades/preparations", workspace);
          if (mounted.current && epoch.current === version) {
            setJobs(result.items);
            hasActive.current = result.items.some(active);
            setError("");
          }
          return true;
        } catch (e) {
          if (mounted.current && epoch.current === version)
            setError(
              e instanceof Error
                ? e.message
                : "Не удалось проверить подготовку.",
            );
          return false;
        }
      },
      {
        interval: () => (hasActive.current ? 2000 : 15000),
        paused: () => pending.current,
      },
    );
    return () => {
      mounted.current = false;
      epoch.current++;
      poll.current?.stop();
    };
  }, [workspace]);
  async function action(work: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    epoch.current++;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      if (mounted.current)
        setError(
          e instanceof Error ? e.message : "Не удалось выполнить действие.",
        );
    } finally {
      pending.current = false;
      if (mounted.current) {
        setBusy(false);
        poll.current?.refresh();
      }
    }
  }
  async function prepare() {
    const period = periods.find((p) => p.id === periodId);
    if (!period) return;
    await action(async () => {
      const job: Job = await api(
        `final-grades/periods/${period.id}/prepare`,
        workspace,
        "POST",
        { version: period.version },
      );
      if (job.id) {
        setJobs((old) => [job, ...old.filter((j) => j.id !== job.id)]);
        hasActive.current = active(job);
      } else await onOpen(job.bookId);
    });
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
                  active(j) &&
                  j.periodId === periodId &&
                  j.closureVersion ===
                    periods.find((p) => p.id === periodId)?.version,
              )
            }
            onClick={() => void prepare()}
          >
            Подготовить ведомость
          </button>
          <button onClick={() => poll.current?.refresh()}>
            Обновить подготовку
          </button>
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
              {statuses[job.status]} · {phases[job.phase] || job.phase}
              {job.attempts > 1 ? ` · запуск ${job.attempts}` : ""}
            </p>
            {active(job) && job.total > 0 && (
              <>
                <progress
                  max={job.total}
                  value={job.completed}
                  aria-label={`${phases[job.phase] || "Подготовка"}: ${job.periodName}`}
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
            {active(job) && (
              <button
                disabled={job.status === "cancelling"}
                onClick={() =>
                  void action(async () => {
                    const value = await api(
                      `final-grades/preparations/${job.id}/cancel`,
                      workspace,
                      "POST",
                    );
                    setJobs((old) =>
                      old.map((j) => (j.id === job.id ? value : j)),
                    );
                  })
                }
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

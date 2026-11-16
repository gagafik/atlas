import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { startPolling } from "./polling";
import type { Job } from "./finalGradesTypes";

export const isActiveJob = (job: Job) =>
  ["queued", "running", "cancelling"].includes(job.status);
export const JOB_STATUS: Record<string, string> = {
  queued: "В очереди",
  running: "Подготовка",
  cancelling: "Остановка",
  cancelled: "Отменено",
  succeeded: "Готово",
  failed: "Не завершено",
  stale: "Источник или доступ изменился",
};
export const JOB_PHASE: Record<string, string> = {
  queued: "Ожидание",
  waiting: "Ожидание исполнителя",
  schedule: "Составы и уроки",
  journals: "Работы из архива",
  rows: "Итоговые строки",
  saving: "Сохранение",
  done: "Завершено",
};

/** The background jobs that prepare statements: polled while the screen is open, quickly while one is running. */
export function usePreparations(workspace: string) {
  const [jobs, setJobs] = useState<Job[]>([]),
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
            hasActive.current = result.items.some(isActiveJob);
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
  /** One action at a time; the poll waits for it. */
  async function action<T>(work: () => Promise<T>): Promise<T | undefined> {
    if (pending.current) return undefined;
    pending.current = true;
    epoch.current++;
    setBusy(true);
    setError("");
    try {
      return await work();
    } catch (e) {
      if (mounted.current)
        setError(
          e instanceof Error ? e.message : "Не удалось выполнить действие.",
        );
      return undefined;
    } finally {
      pending.current = false;
      if (mounted.current) {
        setBusy(false);
        poll.current?.refresh();
      }
    }
  }
  /** Starts the preparation of a closed period. Gives the id of the statement when the server already had one, else ''. */
  async function prepare(period: { id: string; version: number }) {
    const bookId = await action(async () => {
      const job: Job = await api(
        `final-grades/periods/${period.id}/prepare`,
        workspace,
        "POST",
        { version: period.version },
      );
      if (job.id) {
        setJobs((old) => [job, ...old.filter((j) => j.id !== job.id)]);
        hasActive.current = isActiveJob(job);
        return "";
      }
      return job.bookId;
    });
    return bookId ?? "";
  }
  async function cancel(job: Job) {
    await action(async () => {
      const value = await api(
        `final-grades/preparations/${job.id}/cancel`,
        workspace,
        "POST",
      );
      setJobs((old) => old.map((j) => (j.id === job.id ? value : j)));
    });
  }
  return {
    jobs,
    busy,
    error,
    action,
    prepare,
    cancel,
    refresh: () => poll.current?.refresh(),
  };
}
export type Preparations = ReturnType<typeof usePreparations>;

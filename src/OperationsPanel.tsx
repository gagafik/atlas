import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { startPolling } from "./polling";
import "./OperationsPanel.css";

type Backup = {
  state: string;
  phase?: string;
  intervalHours?: number;
  keep?: number;
  nextRun?: number;
  overdue?: boolean;
  cleanupWarning?: boolean;
  lastFailure?: { at: string; code: string } | null;
  lastSuccess?: {
    at: string;
    files: number;
    bytes: number;
    snapshotSeconds: number;
    writerLockSeconds: number | null;
    durationSeconds: number;
    restoreVerified: boolean;
  } | null;
};
type Traffic = {
  startedAt: string;
  uptimeSeconds: number;
  inFlight: number;
  totalRequests: number;
  totalServerErrors: number;
  window: {
    seconds: number;
    requests: number;
    requestsPerSecond: number;
    serverErrors: number;
    clientErrors: number;
    unavailable: number;
    rateLimited: number;
    incomplete: number;
    averageMs: number | null;
    maximumMs: number | null;
    p95UpperMs: number | null;
    p95Overflow: boolean;
  };
};
type Resources = {
  checkedAt: string;
  ageSeconds: number;
  disk: {
    freeBytes: number;
    totalBytes: number;
    freePercent: number;
    level: string;
  } | null;
  queues:
    | {
        name: string;
        initialized: boolean;
        counts: Record<string, { count: number; capped: boolean }>;
      }[]
    | null;
};
type Status = {
  databaseReady: boolean;
  backup: Backup;
  traffic: Traffic;
  resources: Resources;
};
const queueNames: Record<string, string> = {
  generation: "Генерация расписания",
  reports: "Подготовка ведомостей",
  push: "Уведомления на устройства",
};
const queueStates: Record<string, string> = {
  queued: "в очереди",
  running: "в работе",
  cancelling: "отмена",
  sending: "отправка",
  failed: "не доставлены",
};
const gib = (bytes: number) =>
  (bytes / 1024 ** 3).toLocaleString("ru-RU", { maximumFractionDigits: 2 });
const date = (value: string | number) =>
  new Date(value).toLocaleString("ru-RU");
const states: Record<string, string> = {
  disabled: "Автоматические копии не настроены",
  not_started: "Служба копирования ещё не запущена",
  configuration_error: "Проверьте настройки копирования на сервере",
  unreadable: "Не удалось прочитать состояние копирования",
  stopped: "Служба копирования не работает",
  running: "Служба копирования работает",
};
const phases: Record<string, string> = {
  snapshot:
    "Создаётся согласованная копия. Сохранение изменений может временно задерживаться.",
  verifying: "Проверяются файлы и пробное восстановление в отдельную папку.",
  retention: "Удаляются старые копии согласно сроку хранения.",
  waiting: "Ожидание следующей копии.",
};

export default function OperationsPanel({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const poll = useRef<ReturnType<typeof startPolling> | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
    let alive = true;
    poll.current = startPolling(
      async () => {
        try {
          const result = (await api("access/operations", "school")) as Status;
          if (alive) {
            setStatus(result);
            setError("");
            setUpdated(date(Date.now()));
          }
          return true;
        } catch (e) {
          if (alive) {
            setStatus(null);
            setError(
              e instanceof Error ? e.message : "Не удалось обновить состояние.",
            );
          }
          return false;
        }
      },
      { interval: 10000 },
    );
    return () => {
      alive = false;
      poll.current?.stop();
    };
  }, []);
  const backup = status?.backup;
  const last = backup?.lastSuccess;
  const traffic = status?.traffic;
  const recent = traffic?.window;
  const resources = status?.resources;
  return (
    <dialog
      ref={dialog}
      className="notifications-dialog operations-dialog"
      aria-labelledby="operations-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2 id="operations-title">Состояние сервера</h2>
        <button onClick={onClose}>Закрыть</button>
      </header>
      <p>Состояние обновляется, пока это окно открыто.</p>
      <button onClick={() => poll.current?.refresh()}>Обновить</button>
      {error && (
        <p role="alert" className="operations-error">
          {error}
        </p>
      )}
      {!status && !error && <p role="status">Проверяю сервер…</p>}
      {status && backup && (
        <>
          <p>
            <strong>База данных:</strong>{" "}
            {status.databaseReady ? "доступна" : "требуется проверка"}
          </p>
          {traffic && recent && (
            <section aria-labelledby="operations-traffic-title">
              <h3 id="operations-traffic-title">Скорость и нагрузка</h3>
              <p>
                За последние пять минут; после запуска — за доступное время.
              </p>
              <dl className="operations-facts">
                <dt>Запросы сейчас в работе</dt>
                <dd>{traffic.inFlight}</dd>
                <dt>Завершённые запросы</dt>
                <dd>
                  {recent.requests} · {recent.requestsPerSecond} в секунду
                </dd>
                <dt>Среднее время ответа</dt>
                <dd>
                  {recent.averageMs === null
                    ? "Пока нет данных"
                    : `${recent.averageMs} мс`}
                </dd>
                <dt>95% ответов не дольше</dt>
                <dd>
                  {recent.p95Overflow
                    ? "Более 30 секунд — проверьте нагрузку"
                    : recent.p95UpperMs === null
                      ? "Пока нет данных"
                      : `${recent.p95UpperMs} мс (верхняя граница)`}
                </dd>
                <dt>Самый долгий ответ</dt>
                <dd>
                  {recent.maximumMs === null
                    ? "Пока нет данных"
                    : `${recent.maximumMs} мс`}
                </dd>
                <dt>Ошибки сервера</dt>
                <dd>{recent.serverErrors}</dd>
                <dt>Сервер временно недоступен</dt>
                <dd>{recent.unavailable}</dd>
                <dt>Отказы в запросах</dt>
                <dd>
                  {recent.clientErrors}, из них ограничение частоты:{" "}
                  {recent.rateLimited}
                </dd>
                <dt>Незавершённые ответы</dt>
                <dd>{recent.incomplete}</dd>
              </dl>
              {(recent.serverErrors > 0 || recent.incomplete > 0) && (
                <p className="operations-error" role="status">
                  Есть ошибки или оборванные ответы. Ответственному за сервер
                  нужно проверить причину.
                </p>
              )}
              <p>
                С запуска {date(traffic.startedAt)}: запросов{" "}
                {traffic.totalRequests}, ошибок сервера{" "}
                {traffic.totalServerErrors}. При перезапуске показатели
                сбрасываются.
              </p>
              <details>
                <summary>Как считаются показатели</summary>
                <p>
                  Отказы могут означать неверный ввод или отсутствие доступа.
                  Один ответ может попасть в несколько счётчиков.
                </p>
                <p>
                  Измеряется обработка запросов приложением до отправки ответа.
                  Время сети и загрузки страницы сюда не входит. Проверки
                  состояния и статические файлы исключены. Адреса запросов и
                  данные пользователей в метриках не сохраняются.
                </p>
              </details>
            </section>
          )}
          {resources && (
            <section aria-labelledby="operations-resources-title">
              <h3 id="operations-resources-title">Место и фоновые задачи</h3>
              {resources.disk ? (
                <>
                  <p>
                    Свободно на диске с данными школы:{" "}
                    <strong>
                      {gib(resources.disk.freeBytes)} из{" "}
                      {gib(resources.disk.totalBytes)} ГиБ (
                      {resources.disk.freePercent}%)
                    </strong>
                    .
                  </p>
                  {resources.disk.level !== "normal" && (
                    <p className="operations-error" role="status">
                      {resources.disk.level === "critical"
                        ? "Критически мало места. Сохранение данных может прекратиться."
                        : "Место заканчивается. Нужно проверить хранилище."}
                    </p>
                  )}
                </>
              ) : (
                <p className="operations-error">
                  Не удалось проверить свободное место.
                </p>
              )}
              {resources.queues ? (
                <ul className="operations-queues">
                  {resources.queues.map((queue) => (
                    <li key={queue.name}>
                      <strong>{queueNames[queue.name] || queue.name}</strong>:{" "}
                      {queue.initialized
                        ? Object.entries(queue.counts)
                            .map(
                              ([name, value]) =>
                                `${queueStates[name] || name} — ${value.capped ? "более " : ""}${value.count}`,
                            )
                            .join("; ")
                        : "ещё не запускалась"}
                      .
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="operations-error">
                  Не удалось проверить фоновые задачи. Это не означает, что
                  очередь пуста.
                </p>
              )}
              <p>
                Количество задач само по себе не подтверждает работу
                исполнителя. Состояние диска и очередей проверяется не чаще раза
                в 10 секунд.
              </p>
              <small>Проверено: {date(resources.checkedAt)}.</small>
            </section>
          )}
          <h3>Резервные копии</h3>
          <p role="status">
            <strong>{states[backup.state] || "Состояние неизвестно"}</strong>
          </p>
          {backup.state === "disabled" && (
            <p>
              Ответственный за сервер должен включить автоматическое
              копирование. Ручные копии в этой истории не отображаются.
            </p>
          )}
          {backup.state === "running" && <p>{phases[backup.phase || ""]}</p>}
          {backup.intervalHours && (
            <p>
              Копирование каждые {backup.intervalHours} ч. Сохраняется до{" "}
              {backup.keep} успешных копий.
            </p>
          )}
          {last ? (
            <dl className="operations-facts">
              <dt>Последняя успешная копия</dt>
              <dd>{date(last.at)}</dd>
              <dt>Пробное восстановление</dt>
              <dd>{last.restoreVerified ? "Проверено" : "Не подтверждено"}</dd>
              <dt>Объём и вложения</dt>
              <dd>
                {(last.bytes / 1024 / 1024).toFixed(1)} МиБ · вложений:{" "}
                {last.files}
              </dd>
              <dt>Создание снимка</dt>
              <dd>{last.snapshotSeconds} с</dd>
              {typeof last.writerLockSeconds === "number" && (
                <>
                  <dt>Ожидание записи во время снимка</dt>
                  <dd>{last.writerLockSeconds.toFixed(3)} с</dd>
                </>
              )}
              <dt>Полный цикл проверки</dt>
              <dd>{last.durationSeconds} с</dd>
            </dl>
          ) : (
            backup.state !== "disabled" && (
              <p>Успешных автоматических копий ещё нет.</p>
            )
          )}
          {backup.overdue && (
            <p className="operations-error">
              Нет свежей проверенной копии. Требуется внимание ответственного за
              сервер.
            </p>
          )}
          {backup.lastFailure && (
            <p className="operations-error" role="alert">
              Последняя попытка не завершилась: {date(backup.lastFailure.at)}.
              {backup.lastFailure.code === "timeout"
                ? " Превышено время копирования или проверки."
                : " Проверьте службу копирования и свободное место на сервере."}
            </p>
          )}
          {backup.cleanupWarning && (
            <p className="operations-error">
              Старые или временные копии требуют проверки. Автоматическая
              очистка не завершилась.
            </p>
          )}
          {backup.nextRun && backup.state === "running" ? (
            <p>Следующая попытка: {date(backup.nextRun * 1000)}.</p>
          ) : null}
          <p>
            Проверка подтверждает целостность базы, вложений и восстановленной
            копии. Отдельный учебный запуск после аварии проверяется
            ответственным за сервер.
          </p>
          <p>
            Копия на том же сервере не защищает от потери сервера. Хранение
            проверенной копии вне сервера настраивается отдельно.
          </p>
        </>
      )}
      {updated && <small>Последнее обновление: {updated}</small>}
    </dialog>
  );
}

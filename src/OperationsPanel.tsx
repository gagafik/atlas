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
type Status = { databaseReady: boolean; backup: Backup };
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

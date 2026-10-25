import { useEffect, useRef, useState } from "react";
import { api } from "./api";

export interface HomeworkDeadline {
  version: number;
  baseDueDate: string | null;
  effectiveDueDate: string | null;
  deadlineClosed: boolean;
  canEdit: boolean;
  canRevoke?: boolean;
  minDate: string | null;
  maxDate: string | null;
  extension: {
    dueDate: string;
    reason: string;
    updated: string;
    actor: string;
  } | null;
}
interface Change {
  id: number;
  action: "extend" | "revoke" | "invalidated";
  created: string;
  actor: string;
  previousDueDate: string | null;
  dueDate: string | null;
  baseDueDate?: string | null;
  reason: string;
}
const labels = {
  extend: "Срок продлён",
  revoke: "Продление отменено",
  invalidated: "Продление завершено после изменения публикации",
};
const dateLabel = (value: string | null) =>
  value ? value.split("-").reverse().join(".") : "не указан";

export default function HomeworkDeadlinePanel({
  workspace,
  endpoint,
  studentId,
  assignment,
  value,
  disabled,
  onUpdated,
  onActivity,
}: {
  workspace: string;
  endpoint: string;
  studentId: string;
  assignment: string;
  value: HomeworkDeadline;
  disabled: boolean;
  onUpdated: (value: HomeworkDeadline) => void;
  onActivity: (value: { dirty: boolean; busy: boolean }) => void;
}) {
  const [open, setOpen] = useState(false),
    [action, setAction] = useState<"extend" | "revoke">("extend");
  const [dueDate, setDueDate] = useState(""),
    [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [history, setHistory] = useState<Change[]>([]),
    [historyOpen, setHistoryOpen] = useState(false),
    [next, setNext] = useState<number | null>(null);
  const alive = useRef(true);
  // A lesson turned offline keeps its individual date: it can only be cancelled.
  const mode = value.canEdit ? action : "revoke",
    canChange = value.canEdit || Boolean(value.canRevoke);
  const dirty = open && Boolean(dueDate || reason || mode === "revoke");
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onActivity({ dirty, busy });
    return () => onActivity({ dirty: false, busy: false });
  }, [dirty, busy, onActivity]);
  async function run(fn: () => Promise<void>) {
    if (busy || disabled) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  function reset() {
    setOpen(false);
    setDueDate("");
    setReason("");
    setAction("extend");
    setError("");
  }
  async function save() {
    const result = await api(`${endpoint}/deadline`, workspace, "PUT", {
      studentId,
      assignment,
      version: value.version,
      action: mode,
      dueDate: mode === "extend" ? dueDate : null,
      reason,
    });
    if (!alive.current) return;
    onUpdated(result);
    reset();
    setHistoryOpen(false);
    setHistory([]);
    setNext(null);
  }
  async function loadHistory(before?: number) {
    const params = new URLSearchParams({ studentId });
    if (before) params.set("before", String(before));
    const result = await api(
      `${endpoint}/deadline-history?${params}`,
      workspace,
    );
    if (!alive.current) return;
    setHistory((old) => (before ? [...old, ...result.items] : result.items));
    setNext(result.nextBefore);
    setHistoryOpen(true);
  }
  return (
    <section
      className="homework-deadline"
      aria-label="Индивидуальный срок сдачи"
    >
      {value.extension && (
        <div className="info-line">
          <strong>
            Индивидуальный срок: {dateLabel(value.effectiveDueDate)}
          </strong>
          <p>Общий срок задания: {dateLabel(value.baseDueDate)}.</p>
          <p className="journal-text">{value.extension.reason}</p>
          <small>
            {value.extension.actor} ·{" "}
            {new Date(value.extension.updated).toLocaleString("ru-RU")}
          </small>
        </div>
      )}
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div className="dated-toolbar">
        {canChange && !open && (
          <button disabled={disabled || busy} onClick={() => setOpen(true)}>
            Изменить срок ученика
          </button>
        )}
        <button
          disabled={disabled || busy}
          onClick={() => void run(() => loadHistory())}
        >
          История сроков
        </button>
      </div>
      {open && canChange && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(save);
          }}
        >
          <fieldset className="access-fields" disabled={disabled || busy}>
            <legend>Срок только для этого ученика</legend>
            <label className="field">
              <span>Действие со сроком</span>
              <select
                value={mode}
                onChange={(e) =>
                  setAction(e.target.value as "extend" | "revoke")
                }
              >
                {value.canEdit && <option value="extend">Продлить срок</option>}
                {value.extension && (
                  <option value="revoke">Отменить продление</option>
                )}
              </select>
            </label>
            {mode === "extend" ? (
              <label className="field">
                <span>Новый срок сдачи</span>
                <input
                  type="date"
                  required
                  min={value.minDate || undefined}
                  max={value.maxDate || undefined}
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                />
                <small>
                  Позже действующего срока, не раньше сегодня и не более года от
                  общего срока.
                </small>
              </label>
            ) : (
              <p className="info-line">
                Вернётся общий срок {dateLabel(value.baseDueDate)}. Если он
                истёк и поздняя сдача запрещена, приём работ закроется.
              </p>
            )}
            <label className="field">
              <span>Причина изменения срока</span>
              <textarea
                rows={2}
                required
                maxLength={1000}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <p className="muted">
              Причина видна ученику и родителям. Прежние отправки сохраняют свои
              сроки и отметку опоздания. Для повторной сдачи принятой работы
              используйте возврат на доработку.
            </p>
            <div className="dated-toolbar">
              <button
                className="primary"
                type="submit"
                disabled={!reason.trim() || (mode === "extend" && !dueDate)}
              >
                {mode === "extend"
                  ? "Сохранить продление"
                  : "Отменить продление"}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (
                    !dirty ||
                    window.confirm("Закрыть несохранённое изменение срока?")
                  )
                    reset();
                }}
              >
                Закрыть изменение срока
              </button>
            </div>
          </fieldset>
        </form>
      )}
      {historyOpen && (
        <div className="homework-history">
          {!history.length && <p>Индивидуальный срок ещё не менялся.</p>}
          {history.map((item) => (
            <details key={item.id}>
              <summary>
                {labels[item.action]} ·{" "}
                {new Date(item.created).toLocaleString("ru-RU")} · {item.actor}
              </summary>
              <p>
                Прежний срок: {dateLabel(item.previousDueDate)}.{" "}
                {item.action === "invalidated"
                  ? "Индивидуальный срок больше не действует."
                  : `Новый срок: ${dateLabel(item.dueDate || item.baseDueDate || null)}.`}
              </p>
              <p className="journal-text">{item.reason}</p>
            </details>
          ))}
          {next && (
            <button
              disabled={disabled || busy}
              onClick={() => void run(() => loadHistory(next))}
            >
              Более ранние изменения срока
            </button>
          )}
        </div>
      )}
    </section>
  );
}

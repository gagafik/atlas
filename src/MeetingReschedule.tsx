import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { clockTime, displayTime, schoolInput, type Booking } from "./meetings";

type Candidate = {
  id: string;
  version: number;
  startsAt: string;
  endsAt: string;
  available: boolean;
  reason: string;
};
type Choices = {
  booking: Booking;
  eventVersion: number;
  sourceSlotVersion: number;
  items: Candidate[];
  nextAfter: string | null;
};

export default function MeetingReschedule({
  booking,
  workspace,
  reminderMinutes,
  onClose,
  onComplete,
}: {
  booking: Booking;
  workspace: string;
  reminderMinutes: number;
  onClose: () => void;
  onComplete: (startsAt: string) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    lock = useRef(false);
  const replay = useRef({ key: "", id: "" });
  const [day, setDay] = useState(schoolInput(booking.startsAt).slice(0, 10));
  const [data, setData] = useState<Choices | null>(null),
    [selected, setSelected] = useState("");
  const [reason, setReason] = useState(""),
    [blockSource, setBlockSource] = useState(false);
  const [loading, setLoading] = useState(false),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const dirty = !!(reason || selected || blockSource);
  function close() {
    if (
      !lock.current &&
      (!dirty || window.confirm("Закрыть перенос без сохранения?"))
    )
      onClose();
  }
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    dialog.current?.showModal();
    return () => {
      ++sequence.current;
      previous?.focus();
    };
  }, []);
  useEffect(() => {
    const stop = (e: BeforeUnloadEvent) => {
      if (dirty || saving) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", stop);
    return () => window.removeEventListener("beforeunload", stop);
  }, [dirty, saving]);
  useEffect(() => {
    const n = ++sequence.current;
    setData(null);
    setSelected("");
    setError("");
    setLoading(true);
    const query = new URLSearchParams(day ? { onDate: day } : {});
    api(
      `meetings/bookings/${booking.id}/reschedule-options?${query}`,
      workspace,
    )
      .then((r) => {
        if (sequence.current === n) setData(r);
      })
      .catch((e) => {
        if (sequence.current === n) setError(e.message);
      })
      .finally(() => {
        if (sequence.current === n) setLoading(false);
      });
    return () => {
      ++sequence.current;
    };
  }, [booking.id, workspace, day, refresh]);
  async function more() {
    if (!data?.nextAfter || loading || lock.current) return;
    const n = sequence.current;
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({
        after: data.nextAfter,
        ...(day ? { onDate: day } : {}),
      });
      const r: Choices = await api(
        `meetings/bookings/${booking.id}/reschedule-options?${query}`,
        workspace,
      );
      if (n !== sequence.current) return;
      if (
        r.booking.version !== data.booking.version ||
        r.eventVersion !== data.eventVersion ||
        r.sourceSlotVersion !== data.sourceSlotVersion
      ) {
        setData(null);
        setSelected("");
        throw new Error(
          "Запись или интервалы изменились. Обновите доступное время.",
        );
      }
      setData({ ...r, items: [...data.items, ...r.items] });
    } catch (e: any) {
      if (n === sequence.current) {
        setError(e.message);
        if ([401, 403, 404].includes(e.status)) setData(null);
      }
    } finally {
      if (n === sequence.current) setLoading(false);
    }
  }
  async function save() {
    const target = data?.items.find((s) => s.id === selected && s.available);
    if (!data || !target || !reason.trim() || lock.current || loading) return;
    lock.current = true;
    setSaving(true);
    setError("");
    const n = sequence.current;
    const body = {
      version: data.booking.version,
      eventVersion: data.eventVersion,
      sourceSlotVersion: data.sourceSlotVersion,
      targetSlotId: target.id,
      targetSlotVersion: target.version,
      reason: reason.trim(),
      blockSource,
    };
    const key = JSON.stringify(body);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    try {
      const result = await api(
        `meetings/bookings/${booking.id}/reschedule`,
        workspace,
        "POST",
        { ...body, requestId: replay.current.id },
      );
      if (n === sequence.current) await onComplete(result.startsAt);
    } catch (e: any) {
      if (n === sequence.current) {
        setError(e.message);
        if ([401, 403, 404].includes(e.status)) setData(null);
      }
    } finally {
      lock.current = false;
      if (n === sequence.current) setSaving(false);
    }
  }
  const current = data?.booking || booking,
    target = data?.items.find((s) => s.id === selected);
  return (
    <dialog
      className="meeting-reschedule"
      ref={dialog}
      aria-labelledby="meeting-reschedule-title"
      onCancel={(e) => {
        e.preventDefault();
        e.stopPropagation();
        close();
      }}
    >
      <header>
        <h2 id="meeting-reschedule-title">Перенести встречу</h2>
        <button disabled={saving} onClick={close}>
          Закрыть перенос
        </button>
      </header>
      <p>
        {current.studentName} · {current.parentName}
      </p>
      <p>
        {current.hostName} · {current.roomName || current.location}
      </p>
      <p>
        Сейчас:{" "}
        <strong>
          {displayTime(current.startsAt)}–{clockTime(current.endsAt)}
        </strong>
      </p>
      <p>
        Время школы · Кызылорда, UTC+5. Перенос возможен к этому же ведущему в
        пределах мероприятия.
      </p>
      {error && (
        <p className="meeting-error" role="alert">
          {error}
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <fieldset disabled={saving}>
          <label>
            День встречи
            <input
              type="date"
              value={day}
              onChange={(e) => setDay(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={loading}
            onClick={() => setRefresh((n) => n + 1)}
          >
            Обновить доступное время
          </button>
          {loading && <p role="status">Проверяю интервалы…</p>}
          {data && (
            <>
              <label>
                Новое время
                <select
                  required
                  value={selected}
                  disabled={loading}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  <option value="">Выберите интервал</option>
                  {data.items.map((s) => (
                    <option key={s.id} value={s.id} disabled={!s.available}>
                      {displayTime(s.startsAt)}–{clockTime(s.endsAt)}
                      {s.available ? "" : ` · ${s.reason}`}
                    </option>
                  ))}
                </select>
              </label>
              {!data.items.some((s) => s.available) && !loading && (
                <p>
                  На этой странице нет доступного времени. Выберите другой день
                  или загрузите следующие интервалы.
                </p>
              )}
              {data.nextAfter && (
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => void more()}
                >
                  Ещё интервалы
                </button>
              )}
              <label>
                Причина переноса
                <textarea
                  required
                  maxLength={500}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <label className="meeting-reschedule-check">
                <input
                  type="checkbox"
                  checked={blockSource}
                  onChange={(e) => setBlockSource(e.target.checked)}
                />
                Закрыть прежний интервал для новых записей
              </label>
              {target && (
                <div className="meeting-preview">
                  <p>
                    Новое время:{" "}
                    <strong>
                      {displayTime(target.startsAt)}–{clockTime(target.endsAt)}
                    </strong>
                    .
                  </p>
                  <p>
                    {blockSource
                      ? "Прежний интервал станет недоступен."
                      : "Прежний интервал освободится для записи."}{" "}
                    Родитель и ведущий получат уведомление, если включили
                    уведомления в Атласе.{" "}
                    {reminderMinutes
                      ? `Напоминание будет назначено за ${reminderMinutes} мин. до новой встречи.`
                      : "Напоминания для этого мероприятия отключены."}
                  </p>
                </div>
              )}
              <button
                className="primary"
                type="submit"
                disabled={loading || !target?.available || !reason.trim()}
              >
                {saving ? "Переношу…" : "Подтвердить перенос"}
              </button>
            </>
          )}
        </fieldset>
      </form>
    </dialog>
  );
}

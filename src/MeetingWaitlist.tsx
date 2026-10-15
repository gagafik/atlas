import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import {
  displayTime,
  schoolInput,
  schoolISO,
  type MeetingEvent,
  type Named,
  type Slot,
} from "./meetings";

type Entry = {
  id: string;
  version: number;
  hostId: string;
  studentId: string;
  parentName: string;
  studentName: string;
  hostName: string;
  note: string;
  reason: string;
  created: string;
  status: string;
  current: boolean;
  canOffer: boolean;
  canAccept: boolean;
  canWithdraw: boolean;
  canManage: boolean;
  offer: {
    slotId: string;
    slotVersion: number;
    startsAt: string;
    endsAt: string;
    expiresAt: string;
  } | null;
};
type History = {
  action: string;
  created: string;
  reason?: string;
  startsAt?: string;
  expiresAt?: string;
};
const labels: Record<string, string> = {
  waiting: "Ожидает предложения",
  offered: "Предложено время",
  expired: "Ожидает нового предложения",
  accepted: "Встреча подтверждена",
  fulfilled: "Запись уже оформлена",
  withdrawn: "Заявка отозвана",
  cancelled: "Заявка закрыта",
  joined: "Заявка создана",
  decline: "Предложение отклонено",
  withdraw: "Заявка отозвана",
  revoke: "Предложение отозвано школой",
  revoked: "Предложение отозвано школой",
  cancel: "Заявка закрыта",
  booked: "Запись оформлена",
};

export default function MeetingWaitlist({
  workspace,
  eventId,
  admin,
  students,
  onClose,
}: {
  workspace: string;
  eventId: string;
  admin: boolean;
  students: Named[];
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null),
    lock = useRef(false),
    sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  const [event, setEvent] = useState<MeetingEvent | null>(null),
    [items, setItems] = useState<Entry[]>([]),
    [next, setNext] = useState<number | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [student, setStudent] = useState(students[0]?.id || ""),
    [host, setHost] = useState(""),
    [note, setNote] = useState("");
  const [chosen, setChosen] = useState<Entry | null>(null),
    [day, setDay] = useState(""),
    [slots, setSlots] = useState<Slot[]>([]),
    [nextSlot, setNextSlot] = useState<string | null>(null);
  const [slotId, setSlotId] = useState(""),
    [deadline, setDeadline] = useState(""),
    [reason, setReason] = useState("");
  const [history, setHistory] = useState<Record<string, History[]>>({});
  const dirty = !!(note || slotId || deadline || reason);
  function close() {
    if (
      !lock.current &&
      (!dirty || window.confirm("Закрыть очередь без сохранения изменений?"))
    )
      onClose();
  }
  async function run(task: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      if ([401, 403, 404].includes(e.status)) {
        setItems([]);
        setChosen(null);
        setEvent(null);
      }
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function mutate(path: string, body: object) {
    const key = JSON.stringify([workspace, path, body]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const result = await api(`meetings/${path}`, workspace, "POST", {
      ...body,
      requestId: replay.current.id,
    });
    replay.current = { key: "", id: "" };
    return result;
  }
  async function load(after?: number) {
    const n = sequence.current;
    const [e, q] = await Promise.all([
      api(`meetings/events/${eventId}`, workspace),
      api(
        `meetings/events/${eventId}/waitlist${after ? `?after=${after}` : ""}`,
        workspace,
      ),
    ]);
    if (n !== sequence.current) return;
    if (after && event?.version !== q.eventVersion)
      throw new Error("Мероприятие изменилось. Обновите очередь.");
    setEvent({ ...e, version: q.eventVersion });
    setItems((old) => (after ? [...old, ...q.items] : q.items));
    setNext(q.nextAfter);
    if (!after) {
      setHistory({});
      setChosen(null);
      setSlotId("");
      setDeadline("");
      setReason("");
    }
  }
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    dialog.current?.showModal();
    void run(() => load());
    return () => {
      sequence.current++;
      previous?.focus();
    };
  }, []);
  useEffect(() => {
    const stop = (e: BeforeUnloadEvent) => {
      if (dirty || busy) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", stop);
    return () => window.removeEventListener("beforeunload", stop);
  }, [dirty, busy]);
  async function loadSlots(entry: Entry, date: string, after?: string) {
    const query = new URLSearchParams({
      hostId: entry.hostId,
      onDate: date,
      ...(after ? { after } : {}),
    });
    const r = await api(`meetings/events/${eventId}/slots?${query}`, workspace);
    setSlots((old) => (after ? [...old, ...r.items] : r.items));
    setNextSlot(r.nextAfter);
  }
  async function choose(entry: Entry) {
    const date = schoolInput(event!.definition.startsAt).slice(0, 10);
    if (
      chosen &&
      (slotId || deadline || reason) &&
      !window.confirm(
        "Сбросить несохранённое предложение и выбрать другую заявку?",
      )
    )
      return;
    setChosen(entry);
    setDay(date);
    setSlots([]);
    setSlotId("");
    setReason("");
    setDeadline("");
    await loadSlots(entry, date);
  }
  async function change(entry: Entry, action: string) {
    let why = "";
    if (["revoke", "cancel"].includes(action)) {
      const input = window.prompt("Причина для семьи:");
      if (!input?.trim()) return;
      why = input.trim();
    } else if (
      !window.confirm(
        action === "accept"
          ? `Подтвердить встречу: ${displayTime(entry.offer!.startsAt)} — ${displayTime(entry.offer!.endsAt)}?`
          : action === "decline"
            ? "Отклонить это время и остаться в очереди?"
            : "Отозвать заявку из очереди?",
      )
    )
      return;
    await mutate(`waitlist/${entry.id}`, {
      version: entry.version,
      action,
      eventVersion: event!.version,
      slotVersion: entry.offer?.slotVersion ?? null,
      reason: why,
    });
    await load();
    setNotice(
      action === "accept"
        ? "Встреча подтверждена. Она появилась в «Мои встречи». Напоминание настроено по правилам мероприятия."
        : "Изменение сохранено.",
    );
  }
  const eligible =
    event?.hosts.filter(
      (h) => h.current && (admin || h.studentIds?.includes(student)),
    ) || [];
  const target = slots.find((s) => s.id === slotId);
  return (
    <dialog
      ref={dialog}
      className="meeting-reschedule meeting-waitlist"
      aria-labelledby="meeting-wait-title"
      onCancel={(e) => {
        e.preventDefault();
        e.stopPropagation();
        close();
      }}
    >
      <header>
        <h2 id="meeting-wait-title">Очередь ожидания встреч</h2>
        <button disabled={busy} onClick={close}>
          Закрыть очередь
        </button>
      </header>
      <p>{event?.definition.title} · Время школы, UTC+5</p>
      <p>
        Школа предлагает время, родитель подтверждает до указанного срока. До
        ответа интервал удерживается за семьёй. После истечения срока заявка
        остаётся в очереди.
      </p>
      {error && (
        <p className="meeting-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="meeting-notice" role="status">
          {notice}
        </p>
      )}
      <button
        disabled={busy}
        onClick={() => {
          if (
            !dirty ||
            window.confirm("Обновить и сбросить несохранённые изменения?")
          ) {
            setNote("");
            void run(() => load());
          }
        }}
      >
        Обновить очередь
      </button>
      {!admin && event?.registrationOpen && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await mutate(`events/${eventId}/waitlist`, {
                version: event.version,
                hostId: host,
                studentId: student,
                note,
              });
              setNote("");
              await load();
              setNotice(
                "Заявка создана. Это ещё не запись на встречу — дождитесь предложения школы.",
              );
            });
          }}
        >
          <fieldset disabled={busy}>
            <legend>Оставить заявку</legend>
            <label>
              Ребёнок
              <select
                value={student}
                onChange={(e) => {
                  setStudent(e.target.value);
                  setHost("");
                }}
              >
                {students.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Учитель
              <select
                required
                value={host}
                onChange={(e) => setHost(e.target.value)}
              >
                <option value="">Выберите учителя</option>
                {eligible.map((h) => (
                  <option key={h.accountId} value={h.accountId}>
                    {h.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Пожелания по времени или комментарий
              <textarea
                maxLength={500}
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
            <button disabled={!student || !host}>Встать в очередь</button>
          </fieldset>
        </form>
      )}
      {admin && (
        <p>
          Заявки показаны по времени поступления. Выберите семью и предложите
          свободный интервал. Срок ответа задаётся для каждого предложения.
        </p>
      )}
      {chosen && event && (
        <form
          className="meeting-preview"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              if (!target) throw new Error("Выберите интервал.");
              await mutate(`waitlist/${chosen.id}/offer`, {
                version: chosen.version,
                eventVersion: event.version,
                slotId: target.id,
                slotVersion: target.version,
                expiresAt: schoolISO(deadline),
                reason,
              });
              await load();
              setNotice(
                "Предложение сохранено. Семья получила уведомление в Атласе; запись появится после подтверждения.",
              );
            });
          }}
        >
          <fieldset disabled={busy}>
            <legend>
              Предложение: {chosen.studentName} → {chosen.hostName}
            </legend>
            <label>
              День предложения
              <input
                type="date"
                required
                value={day}
                min={schoolInput(event.definition.startsAt).slice(0, 10)}
                max={schoolInput(event.definition.endsAt).slice(0, 10)}
                onChange={(e) => {
                  const date = e.target.value;
                  setDay(date);
                  setSlots([]);
                  setNextSlot(null);
                  setSlotId("");
                  setDeadline("");
                  if (date) void run(() => loadSlots(chosen, date));
                }}
              />
            </label>
            <label>
              Свободный интервал
              <select
                required
                value={slotId}
                onChange={(e) => {
                  setSlotId(e.target.value);
                  const slot = slots.find((s) => s.id === e.target.value);
                  setDeadline(
                    slot
                      ? schoolInput(
                          new Date(
                            Math.min(
                              Date.now() + 24 * 3600000,
                              new Date(slot.startsAt).getTime(),
                            ),
                          ).toISOString(),
                        )
                      : "",
                  );
                }}
              >
                <option value="">Выберите время</option>
                {slots.map((s) => (
                  <option
                    key={s.id}
                    value={s.id}
                    disabled={s.status !== "available"}
                  >
                    {displayTime(s.startsAt)} — {displayTime(s.endsAt)}
                    {s.status !== "available" ? " · недоступно" : ""}
                  </option>
                ))}
              </select>
            </label>
            {nextSlot && (
              <button
                type="button"
                onClick={() => void run(() => loadSlots(chosen, day, nextSlot))}
              >
                Ещё интервалы
              </button>
            )}
            {!slots.length && !busy && (
              <p>На этот день интервалов нет. Выберите другой день.</p>
            )}
            <label>
              Подтвердить до
              <input
                type="datetime-local"
                required
                value={deadline}
                max={target ? schoolInput(target.startsAt) : undefined}
                onChange={(e) => setDeadline(e.target.value)}
              />
            </label>
            <label>
              Комментарий школы
              <textarea
                maxLength={500}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            {target && (
              <p>
                Встреча {displayTime(target.startsAt)} —{" "}
                {displayTime(target.endsAt)}. До подтверждения это время будет
                недоступно другим семьям.
              </p>
            )}
            <div className="meeting-toolbar">
              <button disabled={!target || !deadline}>Предложить время</button>
              <button
                type="button"
                onClick={() => {
                  if (
                    (slotId || deadline || reason) &&
                    !window.confirm("Закрыть предложение без сохранения?")
                  )
                    return;
                  setChosen(null);
                  setSlotId("");
                  setDeadline("");
                  setReason("");
                }}
              >
                Отмена предложения
              </button>
            </div>
          </fieldset>
        </form>
      )}
      <section
        className="meeting-agenda"
        aria-label={admin ? "Заявки семей" : "Мои заявки"}
      >
        {items.map((entry) => (
          <article key={entry.id}>
            <h3>
              {entry.studentName} → {entry.hostName}
            </h3>
            {admin && <p>Родитель: {entry.parentName}</p>}
            <p>
              <strong>{labels[entry.status] || entry.status}</strong> · Заявка
              от {displayTime(entry.created)}
            </p>
            {entry.note && <p>Пожелания: {entry.note}</p>}
            {entry.reason && <p>Комментарий: {entry.reason}</p>}
            {!entry.current && (
              <p className="meeting-error">
                Доступ семьи или ведущего изменился. Школе нужно проверить
                назначение.
              </p>
            )}
            {entry.offer && (
              <p>
                Предложенное время:{" "}
                <strong>
                  {displayTime(entry.offer.startsAt)} —{" "}
                  {displayTime(entry.offer.endsAt)}
                </strong>
                . Ответ до {displayTime(entry.offer.expiresAt)}.
              </p>
            )}
            <div className="meeting-toolbar">
              {entry.canOffer && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => choose(entry))}
                >
                  Выбрать время для семьи
                </button>
              )}
              {entry.canAccept && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => change(entry, "accept"))}
                >
                  Подтвердить встречу
                </button>
              )}
              {entry.canWithdraw && entry.offer && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => change(entry, "decline"))}
                >
                  Другое время — остаться в очереди
                </button>
              )}
              {entry.canWithdraw && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => change(entry, "withdraw"))}
                >
                  Отозвать заявку
                </button>
              )}
              {entry.canManage && entry.offer && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => change(entry, "revoke"))}
                >
                  Отозвать предложение
                </button>
              )}
              {entry.canManage && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => change(entry, "cancel"))}
                >
                  Закрыть заявку
                </button>
              )}
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const h = await api(
                      `meetings/waitlist/${entry.id}/history`,
                      workspace,
                    );
                    setHistory((old) => ({ ...old, [entry.id]: h }));
                  })
                }
              >
                История заявки
              </button>
            </div>
            {history[entry.id] && (
              <ul>
                {history[entry.id].map((h, i) => (
                  <li key={i}>
                    {displayTime(h.created)} · {labels[h.action] || h.action}
                    {h.startsAt ? ` · ${displayTime(h.startsAt)}` : ""}
                    {h.expiresAt
                      ? ` · ответ до ${displayTime(h.expiresAt)}`
                      : ""}
                    {h.reason ? ` · ${h.reason}` : ""}
                  </li>
                ))}
              </ul>
            )}
          </article>
        ))}
        {!items.length && !busy && <p>Заявок пока нет.</p>}
        {next && (
          <button disabled={busy} onClick={() => void run(() => load(next))}>
            Ещё заявки
          </button>
        )}
      </section>
    </dialog>
  );
}

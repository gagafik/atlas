import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import MeetingEditor from "./MeetingEditor";
import MeetingReschedule from "./MeetingReschedule";
import MeetingWaitlist from "./MeetingWaitlist";
import {
  blankDefinition,
  clockTime,
  displayTime,
  groupByDay,
  labels,
  schoolInput,
  schoolISO,
} from "./meetings";
import type {
  Booking,
  Definition,
  EventItem,
  MeetingEvent,
  Options,
  Preview,
  Slot,
} from "./meetings";
import "./ParentMeetings.css";

const dayHeading = (day: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Asia/Qyzylorda",
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(new Date(day + "T12:00:00+05:00"));

export default function ParentMeetings({
  user,
  initialWorkspace,
  onClose,
}: {
  user: Account;
  initialWorkspace: string;
  onClose: () => void;
}) {
  const spaces =
    user.role === "admin"
      ? ["school", "demo"]
      : Array.from(
          new Set([...user.workspaces, ...user.grants.map((g) => g.workspace)]),
        );
  const [workspace, setWorkspace] = useState(
    spaces.includes(initialWorkspace)
      ? initialWorkspace
      : spaces[0] || "school",
  );
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [options, setOptions] = useState<Options>({
    canManage: false,
    students: [],
    hosts: [],
    rooms: [],
    classes: [],
  });
  const [items, setItems] = useState<EventItem[]>([]),
    [next, setNext] = useState<number | null>(null);
  const [selected, setSelected] = useState<MeetingEvent | null>(null),
    [draft, setDraft] = useState<Definition | null>(null),
    [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<"slots" | "agenda" | "manage">("slots");
  const [slots, setSlots] = useState<Slot[]>([]),
    [nextSlot, setNextSlot] = useState<string | null>(null);
  const [bookings, setBookings] = useState<Booking[]>([]),
    [nextBooking, setNextBooking] = useState<string | null>(null);
  const [hostId, setHostId] = useState(""),
    [day, setDay] = useState(""),
    [student, setStudent] = useState(""),
    [pastToo, setPastToo] = useState(false);
  // Parents and hosts see all upcoming meetings of the event; administrators browse by day.
  const mine = !options.canManage;
  const [picked, setPicked] = useState<Slot | null>(null),
    [note, setNote] = useState("");
  const [action, setAction] = useState("generate"),
    [hostIds, setHostIds] = useState<string[]>([]),
    [from, setFrom] = useState("17:00"),
    [to, setTo] = useState("19:00"),
    [reason, setReason] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [queueOpen, setQueueOpen] = useState(false);
  const [transfer, setTransfer] = useState<Booking | null>(null);
  const [history, setHistory] = useState<
    Record<
      string,
      {
        action: string;
        created: string;
        reason?: string;
        source?: { startsAt: string };
        target?: { startsAt: string };
        sourceBlocked?: boolean;
      }[]
    >
  >({});
  const mayLeave = () =>
    !(dirty || note) ||
    window.confirm("Есть несохранённые изменения. Продолжить без сохранения?");
  function clear() {
    setQueueOpen(false);
    setTransfer(null);
    setSelected(null);
    setDraft(null);
    setDirty(false);
    setSlots([]);
    setBookings([]);
    setPicked(null);
    setNote("");
    setPreview(null);
    setHistory({});
  }
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      setPreview(null);
      if ([401, 403, 404].includes(e.status)) clear();
    } finally {
      setBusy(false);
    }
  }
  async function mutate(path: string, body: object, method = "POST") {
    const key = JSON.stringify([workspace, path, body, method]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const result = await api("meetings/" + path, workspace, method, {
      ...body,
      requestId: replay.current.id,
    });
    replay.current = { key: "", id: "" };
    return result;
  }
  async function list(before?: number) {
    const r = await api(
      "meetings/events" + (before ? `?before=${before}` : ""),
      workspace,
    );
    setItems((old) => (before ? [...old, ...r.items] : r.items));
    setNext(r.nextBefore);
  }
  async function loadSlots(
    event: MeetingEvent,
    host: string,
    date: string,
    after?: string,
  ) {
    const query = new URLSearchParams({
      hostId: host,
      ...(date ? { onDate: date } : {}),
      ...(after ? { after } : {}),
    });
    const r = await api(
      `meetings/events/${event.id}/slots?${query}`,
      workspace,
    );
    setSlots((old) => (after ? [...old, ...r.items] : r.items));
    setNextSlot(r.nextAfter);
  }
  async function loadBookings(
    event: MeetingEvent,
    date: string,
    after?: string,
    past = pastToo,
  ) {
    const range: Record<string, string> = mine
      ? past
        ? {}
        : { fromDate: schoolInput(new Date().toISOString()).slice(0, 10) }
      : date
        ? { onDate: date }
        : {};
    const query = new URLSearchParams({
      eventId: event.id,
      ...range,
      ...(after ? { after } : {}),
    });
    const r = await api(`meetings/bookings?${query}`, workspace);
    setBookings((old) => (after ? [...old, ...r.items] : r.items));
    setNextBooking(r.nextAfter);
  }
  async function open(id: string, keep = false, newDate?: string) {
    const e: MeetingEvent = await api(`meetings/events/${id}`, workspace);
    setSelected(e);
    setDraft(null);
    setDirty(false);
    setPicked(null);
    setNote("");
    setPreview(null);
    setHistory({});
    const date =
        newDate ??
        (keep ? day : schoolInput(e.definition.startsAt).slice(0, 10)),
      host = keep && e.hosts.some((h) => h.accountId === hostId) ? hostId : "";
    setDay(date);
    setHostId(host);
    setHostIds(e.hosts.filter((h) => h.current).map((h) => h.accountId));
    if (!keep) {
      setTab(
        options.canManage
          ? "manage"
          : user.role === "parent"
            ? "slots"
            : "agenda",
      );
      setFrom("17:00");
      setTo("19:00");
      setReason("");
    }
    await Promise.all([loadSlots(e, host, date), loadBookings(e, date)]);
  }
  async function refreshed(message: string) {
    if (selected) await open(selected.id, true);
    await list();
    setNotice(message);
  }
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    dialog.current?.showModal();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    const n = ++sequence.current;
    clear();
    setItems([]);
    setOptions({
      canManage: false,
      students: [],
      hosts: [],
      rooms: [],
      classes: [],
    });
    setBusy(true);
    setError("");
    Promise.all([
      api("meetings/options", workspace),
      api("meetings/events", workspace),
    ])
      .then(([o, r]) => {
        if (n !== sequence.current) return;
        setOptions(o);
        setStudent(o.students[0]?.id || "");
        setItems(r.items);
        setNext(r.nextBefore);
      })
      .catch((e) => {
        if (n === sequence.current) setError(e.message);
      })
      .finally(() => {
        if (n === sequence.current) setBusy(false);
      });
    return () => {
      ++sequence.current;
    };
  }, [workspace]);
  useEffect(() => {
    setPreview(null);
  }, [action, day, from, to, reason, hostIds]);
  useEffect(() => {
    const stop = (e: BeforeUnloadEvent) => {
      if (dirty || note) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", stop);
    return () => window.removeEventListener("beforeunload", stop);
  }, [dirty, note]);
  function availabilityBody() {
    if (!selected || !day || !from || !to)
      throw new Error("Укажите день и время.");
    return {
      version: selected.version,
      action,
      hostIds,
      startsAt: schoolISO(day + "T" + from),
      endsAt: schoolISO(day + "T" + to),
      reason,
    };
  }
  async function transition(action: string) {
    if (!selected) return;
    let explanation = "";
    if (action === "cancel") {
      const answer = window.prompt(
        "Причина отмены мероприятия. Все запланированные записи будут отменены.",
      );
      if (!answer?.trim()) return;
      explanation = answer.trim();
    } else if (
      !window.confirm(
        (
          {
            publish: "Открыть запись и уведомить родителей и учителей?",
            close: "Закрыть новую запись? Существующие встречи сохранятся.",
            reopen: "Снова открыть запись?",
            archive: "Перенести мероприятие в архив?",
          } as Record<string, string>
        )[action],
      )
    )
      return;
    await mutate(`events/${selected.id}/transition`, {
      version: selected.version,
      action,
      reason: explanation,
    });
    await refreshed("Состояние мероприятия изменено.");
  }
  async function changeBooking(b: Booking, action: string) {
    let reason = "";
    if (action === "cancel") {
      if (options.canManage) {
        const value = window.prompt("Причина отмены встречи:");
        if (!value?.trim()) return;
        reason = value.trim();
      } else if (
        !window.confirm("Отменить вашу запись? Интервал снова станет доступен.")
      )
        return;
    }
    await mutate(`bookings/${b.id}`, { version: b.version, action, reason });
    await refreshed("Запись обновлена.");
  }
  async function download() {
    if (!selected) return;
    const response = await fetch(
      `/api/meetings/events/${selected.id}/export?workspace=${encodeURIComponent(workspace)}`,
      { headers: { "X-Atlas-Request": "1" } },
    );
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.detail || "Не удалось выгрузить записи.");
    }
    const url = URL.createObjectURL(await response.blob()),
      link = document.createElement("a");
    link.href = url;
    link.download = "meetings.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const eligible =
    selected?.hosts.find((h) => h.accountId === picked?.hostId)?.studentIds ||
    [];
  return (
    <dialog
      className="parent-meetings"
      ref={dialog}
      aria-labelledby="meeting-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && mayLeave()) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="meeting-title">Родительские встречи</h2>
          <p>Время школы · Кызылорда, UTC+5</p>
        </div>
        <button
          disabled={busy}
          onClick={() => {
            if (mayLeave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      <div className="meeting-toolbar">
        <label>
          Пространство
          <select
            disabled={busy}
            value={workspace}
            onChange={(e) => {
              if (mayLeave()) setWorkspace(e.target.value);
            }}
          >
            {spaces.map((s) => (
              <option key={s} value={s}>
                {s === "demo" ? "Демонстрация" : "Школа"}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={busy}
          onClick={() => {
            if (mayLeave())
              void run(async () => {
                if (selected) await open(selected.id, true);
                await list();
              });
          }}
        >
          Обновить
        </button>
        {options.canManage && (
          <button
            disabled={busy}
            onClick={() => {
              if (mayLeave()) {
                clear();
                setDraft(blankDefinition());
              }
            }}
          >
            Новое мероприятие
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="meeting-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="meeting-notice">
          {notice}
        </p>
      )}
      {busy && <p role="status">Загружаю…</p>}
      <div className="meeting-layout">
        <aside aria-label="Мероприятия">
          <h3>Даты встреч</h3>
          {items.map((e) => (
            <button
              disabled={busy}
              aria-pressed={selected?.id === e.id}
              key={e.id}
              onClick={() => {
                if (mayLeave()) void run(() => open(e.id));
              }}
            >
              <strong>{e.title}</strong>
              <span>{displayTime(e.startsAt)}</span>
              <small>{labels[e.status]}</small>
            </button>
          ))}
          {!items.length && !busy && <p>Доступных мероприятий пока нет.</p>}
          {next && (
            <button disabled={busy} onClick={() => void run(() => list(next))}>
              Ещё мероприятия
            </button>
          )}
        </aside>
        <main>
          {draft && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  if (!draft.hosts.length)
                    throw new Error("Добавьте хотя бы одного ведущего.");
                  const r = await mutate(
                    selected ? `events/${selected.id}` : "events",
                    {
                      definition: draft,
                      ...(selected ? { version: selected.version } : {}),
                    },
                    selected ? "PUT" : "POST",
                  );
                  setDirty(false);
                  await open(r.id);
                  await list();
                  setNotice(
                    "Черновик сохранён. Добавьте интервалы и откройте запись.",
                  );
                });
              }}
            >
              <fieldset disabled={busy}>
                <legend>
                  {selected ? "Параметры черновика" : "Новое мероприятие"}
                </legend>
                <MeetingEditor
                  value={draft}
                  options={options}
                  onChange={(v) => {
                    setDraft(v);
                    setDirty(true);
                  }}
                />
                <button className="primary">Сохранить черновик</button>
                <button
                  type="button"
                  onClick={() => {
                    if (mayLeave()) {
                      setDraft(null);
                      setDirty(false);
                    }
                  }}
                >
                  Отмена
                </button>
              </fieldset>
            </form>
          )}
          {selected && !draft && (
            <>
              <h3>{selected.definition.title}</h3>
              <p>{selected.definition.description}</p>
              <p>
                {displayTime(selected.definition.startsAt)} —{" "}
                {displayTime(selected.definition.endsAt)} ·{" "}
                {labels[selected.status]}
              </p>
              <p>
                Встреча {selected.definition.slotMinutes} мин. Самостоятельная
                отмена — не позднее чем за {selected.definition.cancelMinutes}{" "}
                мин.{" "}
                {selected.definition.reminderMinutes
                  ? `Напоминание за ${selected.definition.reminderMinutes} мин.`
                  : "Напоминания отключены."}
              </p>
              {(options.canManage || user.role === "parent") && (
                <button
                  disabled={busy}
                  onClick={() => {
                    if (mayLeave()) setQueueOpen(true);
                  }}
                >
                  Очередь ожидания
                </button>
              )}
              <nav className="meeting-toolbar" aria-label="Разделы встреч">
                {(
                  [
                    "slots",
                    "agenda",
                    ...(options.canManage ? ["manage"] : []),
                  ] as const
                ).map((t) => (
                  <button
                    disabled={busy}
                    key={t}
                    aria-pressed={tab === t}
                    onClick={() => {
                      if (mayLeave()) {
                        setTab(t as typeof tab);
                        setPicked(null);
                        setNote("");
                      }
                    }}
                  >
                    {t === "slots"
                      ? "Свободное время"
                      : t === "agenda"
                        ? options.canManage
                          ? "Все записи"
                          : "Мои встречи"
                        : "Управление"}
                  </button>
                ))}
              </nav>
              <div className="meeting-toolbar">
                {!(tab === "agenda" && mine) && (
                  <label>
                    День
                    <input
                      type="date"
                      disabled={busy}
                      value={day}
                      onChange={(e) => {
                        if (!mayLeave()) return;
                        const value = e.target.value;
                        setDay(value);
                        setPicked(null);
                        setNote("");
                        void run(async () => {
                          await Promise.all([
                            loadSlots(selected, hostId, value),
                            loadBookings(selected, value),
                          ]);
                        });
                      }}
                    />
                  </label>
                )}
                {tab === "agenda" && mine && (
                  <label>
                    <input
                      type="checkbox"
                      disabled={busy}
                      checked={pastToo}
                      onChange={(e) => {
                        setPastToo(e.target.checked);
                        void run(() =>
                          loadBookings(
                            selected,
                            day,
                            undefined,
                            e.target.checked,
                          ),
                        );
                      }}
                    />{" "}
                    Показать прошедшие встречи
                  </label>
                )}
                {tab === "slots" && (
                  <label>
                    Учитель / сотрудник
                    <select
                      disabled={busy}
                      value={hostId}
                      onChange={(e) => {
                        if (!mayLeave()) return;
                        setHostId(e.target.value);
                        setPicked(null);
                        setNote("");
                        void run(() =>
                          loadSlots(selected, e.target.value, day),
                        );
                      }}
                    >
                      <option value="">Все доступные</option>
                      {selected.hosts.map((h) => (
                        <option key={h.accountId} value={h.accountId}>
                          {h.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
              {tab === "manage" && options.canManage && (
                <>
                  <div className="meeting-toolbar">
                    {selected.status === "draft" && (
                      <>
                        <button
                          disabled={busy || !!selected.slotCount}
                          onClick={() =>
                            setDraft({
                              ...selected.definition,
                              hosts: selected.hosts.map(
                                ({
                                  accountId,
                                  teacherId,
                                  scope,
                                  classIds,
                                  roomId,
                                  location,
                                }) => ({
                                  accountId,
                                  teacherId,
                                  scope,
                                  classIds,
                                  roomId,
                                  location,
                                }),
                              ),
                            })
                          }
                        >
                          Изменить параметры
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => void run(() => transition("publish"))}
                        >
                          Открыть запись
                        </button>
                      </>
                    )}
                    {selected.status === "open" && (
                      <button
                        disabled={busy}
                        onClick={() => void run(() => transition("close"))}
                      >
                        Закрыть запись
                      </button>
                    )}
                    {selected.status === "closed" && (
                      <button
                        disabled={busy}
                        onClick={() => void run(() => transition("reopen"))}
                      >
                        Снова открыть запись
                      </button>
                    )}
                    {!["cancelled", "archived"].includes(selected.status) && (
                      <button
                        disabled={busy}
                        onClick={() => void run(() => transition("cancel"))}
                      >
                        Отменить мероприятие
                      </button>
                    )}
                    {selected.status !== "archived" && (
                      <button
                        disabled={busy}
                        onClick={() => void run(() => transition("archive"))}
                      >
                        В архив
                      </button>
                    )}
                  </div>
                  <p>
                    Интервалами управляет администрация. Чтобы учитель пришёл
                    позже или ушёл раньше, отключите соответствующее время. Для
                    нескольких дней повторите действие по каждому дню.
                  </p>
                  {selected.hosts.some((h) => !h.current) && (
                    <p className="meeting-error">
                      У некоторых ведущих изменился доступ. Их интервалы
                      недоступны для новой записи.
                    </p>
                  )}
                  {selected.status === "draft" && !!selected.slotCount && (
                    <p>
                      После создания интервалов параметры и состав ведущих
                      закреплены. Для другого состава создайте новое
                      мероприятие; текущее можно отменить.
                    </p>
                  )}
                  {!["cancelled", "archived"].includes(selected.status) && (
                    <fieldset disabled={busy}>
                      <legend>Доступное время</legend>
                      <div className="meeting-fields">
                        <label>
                          Действие
                          <select
                            value={action}
                            onChange={(e) => setAction(e.target.value)}
                          >
                            <option value="generate">Добавить интервалы</option>
                            <option value="block">Отключить интервалы</option>
                            <option value="unblock">Вернуть интервалы</option>
                          </select>
                        </label>
                        <label>
                          С
                          <input
                            type="time"
                            value={from}
                            onChange={(e) => setFrom(e.target.value)}
                          />
                        </label>
                        <label>
                          До
                          <input
                            type="time"
                            value={to}
                            onChange={(e) => setTo(e.target.value)}
                          />
                        </label>
                      </div>
                      <div className="meeting-toolbar">
                        <button
                          type="button"
                          onClick={() =>
                            setHostIds(selected.hosts.map((h) => h.accountId))
                          }
                        >
                          Выбрать всех
                        </button>
                        <button type="button" onClick={() => setHostIds([])}>
                          Снять выбор
                        </button>
                      </div>
                      <div className="meeting-checks">
                        {selected.hosts.map((h) => (
                          <label key={h.accountId}>
                            <input
                              type="checkbox"
                              checked={hostIds.includes(h.accountId)}
                              onChange={(e) =>
                                setHostIds(
                                  e.target.checked
                                    ? [...hostIds, h.accountId]
                                    : hostIds.filter(
                                        (id) => id !== h.accountId,
                                      ),
                                )
                              }
                            />
                            {h.name} · {h.roomName || h.location}
                            {!h.current ? " · доступ изменён" : ""}
                          </label>
                        ))}
                      </div>
                      {action === "block" && (
                        <label>
                          Причина отмены (увидят записавшиеся родители)
                          <textarea
                            maxLength={500}
                            value={reason}
                            onChange={(e) => setReason(e.target.value)}
                          />
                        </label>
                      )}
                      <button
                        disabled={
                          !hostIds.length ||
                          !day ||
                          (action === "block" && !reason.trim())
                        }
                        onClick={() =>
                          void run(async () => {
                            setPreview(
                              await api(
                                `meetings/events/${selected.id}/availability/preview`,
                                workspace,
                                "POST",
                                {
                                  ...availabilityBody(),
                                  requestId: crypto.randomUUID(),
                                },
                              ),
                            );
                          })
                        }
                      >
                        Проверить изменения
                      </button>
                      {preview && (
                        <div className="meeting-preview" role="status">
                          <p>
                            Интервалов: <strong>{preview.affectedSlots}</strong>
                            . Записей будет отменено:{" "}
                            <strong>{preview.bookingsToCancel}</strong>.
                            Предложений времени будет отозвано:{" "}
                            <strong>{preview.offersToRevoke || 0}</strong>.
                            Пропущено начавшихся и прошедших интервалов:{" "}
                            <strong>{preview.skippedPast || 0}</strong>.
                            Пересечений:{" "}
                            <strong>{preview.conflictCount}</strong>.
                          </p>
                          {preview.conflicts.map((c, i) => (
                            <p key={i}>
                              {c.host} · {displayTime(c.startsAt)} · {c.reason}
                            </p>
                          ))}
                          <button
                            className="primary"
                            disabled={
                              !!preview.conflictCount || !preview.affectedSlots
                            }
                            onClick={() =>
                              void run(async () => {
                                await mutate(
                                  `events/${selected.id}/availability`,
                                  {
                                    ...availabilityBody(),
                                    previewHash: preview.previewHash,
                                  },
                                );
                                await refreshed(
                                  "Интервалы обновлены. Участники отменённых встреч получат уведомление.",
                                );
                              })
                            }
                          >
                            Применить изменения
                          </button>
                        </div>
                      )}
                    </fieldset>
                  )}
                </>
              )}
              {tab === "slots" && (
                <>
                  {!selected.registrationOpen && (
                    <p className="meeting-notice">
                      Новая запись сейчас закрыта.
                    </p>
                  )}
                  {picked && (
                    <form
                      className="meeting-preview"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void run(async () => {
                          await mutate(`slots/${picked.id}/book`, {
                            version: picked.version,
                            eventVersion: selected.version,
                            studentId: student,
                            note,
                          });
                          setNote("");
                          setPicked(null);
                          await refreshed("Вы записаны на встречу.");
                          setTab("agenda");
                        });
                      }}
                    >
                      <fieldset disabled={busy}>
                        <legend>Подтвердите запись</legend>
                        <p>
                          {picked.hostName} · {displayTime(picked.startsAt)} ·{" "}
                          {picked.roomName || picked.location}
                        </p>
                        <label>
                          Ребёнок
                          <select
                            required
                            value={eligible.includes(student) ? student : ""}
                            onChange={(e) => setStudent(e.target.value)}
                          >
                            <option value="">Выберите ребёнка</option>
                            {options.students
                              .filter((s) => eligible.includes(s.id))
                              .map((s) => (
                                <option key={s.id} value={s.id}>
                                  {s.name}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label>
                          Комментарий учителю (необязательно)
                          <textarea
                            maxLength={500}
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                          />
                        </label>
                        <button
                          className="primary"
                          disabled={!eligible.includes(student)}
                        >
                          Подтвердить запись
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (mayLeave()) {
                              setPicked(null);
                              setNote("");
                            }
                          }}
                        >
                          Отмена
                        </button>
                      </fieldset>
                    </form>
                  )}
                  <div className="meeting-slots">
                    {slots.map((s) => (
                      <article key={s.id} className={s.status}>
                        <strong>
                          {clockTime(s.startsAt)}–{clockTime(s.endsAt)}
                        </strong>
                        <span>{s.hostName}</span>
                        <span>{s.roomName || s.location}</span>
                        <small>{labels[s.status]}</small>
                        {s.booking && (
                          <p>
                            {s.booking.studentName} · {s.booking.parentName}
                          </p>
                        )}
                        {s.conflict && <p>{s.conflict}</p>}
                        {s.reason && <p>{s.reason}</p>}
                        {user.role === "parent" &&
                          s.status === "available" &&
                          selected.registrationOpen && (
                            <button
                              disabled={busy}
                              onClick={() => {
                                if (mayLeave()) {
                                  setPicked(s);
                                  setNote("");
                                  const ids =
                                    selected.hosts.find(
                                      (h) => h.accountId === s.hostId,
                                    )?.studentIds || [];
                                  if (!ids.includes(student))
                                    setStudent(ids[0] || "");
                                }
                              }}
                            >
                              Записаться
                            </button>
                          )}
                      </article>
                    ))}
                  </div>
                  {!slots.length && !busy && (
                    <p>Интервалов на выбранный день нет.</p>
                  )}
                  {nextSlot && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(() =>
                          loadSlots(selected, hostId, day, nextSlot),
                        )
                      }
                    >
                      Ещё интервалы
                    </button>
                  )}
                </>
              )}
              {tab === "agenda" && (
                <>
                  {user.role !== "parent" && (
                    <button disabled={busy} onClick={() => void run(download)}>
                      Скачать список за все дни (CSV)
                    </button>
                  )}
                  <div className="meeting-agenda">
                    {(mine
                      ? groupByDay(bookings)
                      : [["", bookings] as [string, Booking[]]]
                    ).map(([d, rows]) => (
                      <section key={d}>
                        {d && <h3>{dayHeading(d)}</h3>}
                        {rows.map((b) => (
                          <article key={b.id}>
                            <h4>
                              {displayTime(b.startsAt)}–{clockTime(b.endsAt)} ·{" "}
                              {b.hostName}
                            </h4>
                            <p>
                              {b.studentName} · {b.parentName} ·{" "}
                              {b.roomName || b.location}
                            </p>
                            <p>{labels[b.status]}</p>
                            {b.note && <p>Комментарий: {b.note}</p>}
                            {b.reason && <p>Причина отмены: {b.reason}</p>}
                            {b.rescheduleReason && (
                              <p>
                                Причина последнего переноса:{" "}
                                {b.rescheduleReason}
                              </p>
                            )}
                            {b.parentAccessCurrent === false && (
                              <p className="meeting-error">
                                Текущий доступ родителя к ребёнку изменился.
                                Проверьте запись.
                              </p>
                            )}
                            <div className="meeting-toolbar">
                              {options.canManage && b.canReschedule && (
                                <button
                                  disabled={
                                    busy || b.parentAccessCurrent === false
                                  }
                                  onClick={() => setTransfer(b)}
                                >
                                  Перенести встречу
                                </button>
                              )}
                              {b.canCancel && (
                                <button
                                  disabled={busy}
                                  onClick={() =>
                                    void run(() => changeBooking(b, "cancel"))
                                  }
                                >
                                  Отменить запись
                                </button>
                              )}
                              {b.canMark &&
                                ["attended", "no_show", "scheduled"].map(
                                  (a) => (
                                    <button
                                      disabled={busy || a === b.status}
                                      key={a}
                                      onClick={() =>
                                        void run(() => changeBooking(b, a))
                                      }
                                    >
                                      {a === "scheduled"
                                        ? "Убрать отметку"
                                        : labels[a]}
                                    </button>
                                  ),
                                )}
                              <button
                                disabled={busy}
                                onClick={() =>
                                  void run(async () => {
                                    const h = await api(
                                      `meetings/bookings/${b.id}/history`,
                                      workspace,
                                    );
                                    setHistory((old) => ({
                                      ...old,
                                      [b.id]: h,
                                    }));
                                  })
                                }
                              >
                                История
                              </button>
                            </div>
                            {history[b.id] && (
                              <ol>
                                {history[b.id].map((h, i) => (
                                  <li key={i}>
                                    {displayTime(h.created)} ·{" "}
                                    {labels[h.action] || h.action}
                                    {h.reason ? ` · ${h.reason}` : ""}
                                    {h.source && h.target && (
                                      <>
                                        {" "}
                                        · {displayTime(
                                          h.source.startsAt,
                                        )} → {displayTime(h.target.startsAt)}
                                        {h.sourceBlocked
                                          ? " · прежний интервал закрыт"
                                          : " · прежний интервал освобождён"}
                                      </>
                                    )}
                                  </li>
                                ))}
                              </ol>
                            )}
                          </article>
                        ))}
                      </section>
                    ))}
                  </div>
                  {!bookings.length && !busy && (
                    <p>
                      {mine
                        ? pastToo
                          ? "Встреч нет."
                          : "Предстоящих встреч нет."
                        : "Записей на выбранный день нет."}
                    </p>
                  )}
                  {nextBooking && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(() => loadBookings(selected, day, nextBooking))
                      }
                    >
                      Ещё записи
                    </button>
                  )}
                </>
              )}
            </>
          )}
          {!selected && !draft && (
            <p>
              Выберите мероприятие школы. Здесь можно записаться к учителю и
              посмотреть свои встречи.
            </p>
          )}
        </main>
      </div>
      {queueOpen && selected && (
        <MeetingWaitlist
          key={workspace + selected.id}
          workspace={workspace}
          eventId={selected.id}
          admin={options.canManage}
          students={options.students}
          onClose={() => {
            setQueueOpen(false);
            void run(() => open(selected.id, true));
          }}
        />
      )}
      {transfer && (
        <MeetingReschedule
          key={workspace + transfer.id}
          booking={transfer}
          workspace={workspace}
          reminderMinutes={selected?.definition.reminderMinutes || 0}
          onClose={() => setTransfer(null)}
          onComplete={async (startsAt) => {
            setTransfer(null);
            await run(async () => {
              setNotice("Перенос сохранён. Обновляю список встреч…");
              if (selected)
                await open(
                  selected.id,
                  true,
                  schoolInput(startsAt).slice(0, 10),
                );
              await list();
              setNotice("Встреча перенесена. Напоминание и история обновлены.");
            });
          }}
        />
      )}
    </dialog>
  );
}

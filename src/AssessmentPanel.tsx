import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import {
  addDays,
  clock,
  dayLabel,
  mondayOf,
  monthRange,
  monthTitle,
  monthWeeks,
  shiftMonth,
  shortDay,
  weekday,
  type Booking,
  type Options,
  type SchoolEvent,
  type SlotsResult,
} from "./assessmentCalendar";
import {
  BookingCard,
  CalendarGrid,
  FamilyView,
  LoadGrid,
  Settings,
} from "./AssessmentViews";
import "./AssessmentPanel.css";

type FormState = {
  mode: "create" | "move";
  id: string;
  version: number;
  pair: string;
  day: string;
  lessonId: string;
  typeId: string;
  topic: string;
  note: string;
  publish: boolean;
  override: string;
  reason: string;
};
const blank = (): FormState => ({
  mode: "create",
  id: "",
  version: 0,
  pair: "",
  day: "",
  lessonId: "",
  typeId: "",
  topic: "",
  note: "",
  publish: true,
  override: "",
  reason: "",
});

export default function AssessmentPanel({
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
      : [
          ...new Set([
            ...user.workspaces,
            ...user.grants.map((g) => g.workspace),
          ]),
        ];
  const [workspace, setWorkspace] = useState(
    spaces.includes(initialWorkspace)
      ? initialWorkspace
      : spaces[0] || "school",
  );
  const [options, setOptions] = useState<Options | null>(null),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(0),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [tab, setTab] = useState<"calendar" | "load" | "settings">("calendar");
  const [view, setView] = useState<"month" | "week">("month"),
    [month, setMonth] = useState(""),
    [weekStart, setWeekStart] = useState(""),
    [classId, setClassId] = useState(""),
    [teacherId, setTeacherId] = useState(""),
    [mineOnly, setMineOnly] = useState(false),
    [showCancelled, setShowCancelled] = useState(false);
  const [items, setItems] = useState<Booking[]>([]),
    [events, setEvents] = useState<SchoolEvent[]>([]),
    [selectedId, setSelectedId] = useState(""),
    [reload, setReload] = useState(0);
  const [form, setForm] = useState<FormState>(blank),
    [formOpen, setFormOpen] = useState(false),
    [slots, setSlots] = useState<SlotsResult | null>(null),
    [lessonDays, setLessonDays] = useState<string[]>([]);
  const dialog = useRef<HTMLDialogElement>(null),
    optionSequence = useRef(0),
    listSequence = useRef(0),
    slotSequence = useRef(0),
    daySequence = useRef(0),
    request = useRef({ key: "", id: "" });
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  useEffect(
    () => () => {
      ++optionSequence.current;
      ++listSequence.current;
      ++slotSequence.current;
      ++daySequence.current;
    },
    [],
  );

  const staff = Boolean(
    options && (options.canBook || options.admin || options.coordinator),
  );
  const manager = Boolean(options && (options.admin || options.coordinator));
  const dirty =
    formOpen &&
    Boolean(
      form.topic.trim() ||
      form.note.trim() ||
      form.reason.trim() ||
      form.override.trim(),
    );
  const [groupId, subjectId] = form.pair.split("|");
  const weeks = month ? monthWeeks(month) : [];
  const range =
    view === "month" && weeks.length
      ? { start: weeks[0][0], end: weeks[weeks.length - 1][6] }
      : weekStart
        ? { start: weekStart, end: addDays(weekStart, 6) }
        : null;
  const mineId = user.grants.find(
    (g) => g.workspace === workspace && g.view === "teacher",
  )?.entityIds[0];
  const teacherFilter = manager ? teacherId : mineOnly && mineId ? mineId : "";
  const selected = items.find((b) => b.id === selectedId) || null;
  const shown = items.filter((b) => showCancelled || b.status !== "cancelled");

  async function loadOptions() {
    const token = ++optionSequence.current;
    try {
      const opts: Options = await api("assessment-calendar/options", workspace);
      if (token !== optionSequence.current) return null;
      setOptions(opts);
      setMonth((m) => m || opts.today.slice(0, 7));
      setWeekStart((w) => w || mondayOf(opts.today));
      setForm((f) => ({
        ...f,
        typeId: f.typeId || opts.types.find((t) => t.active)?.id || "",
        pair:
          f.pair ||
          (opts.pairs.length === 1
            ? `${opts.pairs[0].groupId}|${opts.pairs[0].subjectId}`
            : ""),
      }));
      return opts;
    } catch (e: any) {
      if (token !== optionSequence.current) return null;
      setOptions(null);
      setError(e.message);
      return null;
    }
  }
  useEffect(() => {
    setOptions(null);
    setItems([]);
    setError("");
    setLoading((n) => n + 1);
    void loadOptions().finally(() => setLoading((n) => n - 1));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace]);

  // The calendar follows the visible range and filters; a late answer for an old range is dropped.
  useEffect(() => {
    if (!options || !staff || tab !== "calendar" || !range) return;
    const token = ++listSequence.current;
    const params = new URLSearchParams({ start: range.start, end: range.end });
    if (classId) params.set("classId", classId);
    if (teacherFilter) params.set("teacherId", teacherFilter);
    setLoading((n) => n + 1);
    api("assessment-calendar/bookings?" + params.toString(), workspace)
      .then((result) => {
        if (token !== listSequence.current) return;
        setItems(result.items);
        setEvents(result.events);
        setError("");
      })
      .catch((e: any) => {
        if (token !== listSequence.current) return;
        setItems([]);
        setError(e.message);
      })
      .finally(() => setLoading((n) => n - 1));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    options,
    tab,
    range?.start,
    range?.end,
    classId,
    teacherFilter,
    workspace,
    reload,
  ]);

  // Days with lessons of the chosen pair are marked in the grid.
  useEffect(() => {
    const token = ++daySequence.current;
    if (!formOpen || !groupId || !subjectId || !range || tab !== "calendar") {
      setLessonDays([]);
      return;
    }
    const params = new URLSearchParams({
      groupId,
      subjectId,
      start: range.start,
      end: range.end,
    });
    api("assessment-calendar/lesson-days?" + params.toString(), workspace)
      .then((r) => token === daySequence.current && setLessonDays(r.days))
      .catch(() => token === daySequence.current && setLessonDays([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formOpen, form.pair, range?.start, range?.end, tab, workspace]);

  // The lessons of the chosen date; a single free lesson is picked at once.
  useEffect(() => {
    const token = ++slotSequence.current;
    if (
      !formOpen ||
      tab !== "calendar" ||
      !groupId ||
      !subjectId ||
      !form.day
    ) {
      setSlots(null);
      return;
    }
    const params = new URLSearchParams({ groupId, subjectId, day: form.day });
    if (form.typeId) params.set("typeId", form.typeId);
    if (form.mode === "move") params.set("exclude", form.id);
    api("assessment-calendar/slots?" + params.toString(), workspace)
      .then((r: SlotsResult) => {
        if (token !== slotSequence.current) return;
        setSlots(r);
        const free = r.slots.filter((s) => !s.booked);
        setForm((f) => ({
          ...f,
          lessonId: free.some((s) => s.lessonId === f.lessonId)
            ? f.lessonId
            : free.length === 1
              ? free[0].lessonId
              : "",
        }));
      })
      .catch((e: any) => {
        if (token !== slotSequence.current) return;
        setSlots(null);
        setError(e.message);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    formOpen,
    tab,
    form.pair,
    form.day,
    form.typeId,
    form.mode,
    form.id,
    workspace,
    reload,
  ]);

  async function run(action: () => Promise<void>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      if (message) setNotice(message);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function leave() {
    return (
      !dirty ||
      window.confirm("Форма работы не сохранена. Отбросить её и продолжить?")
    );
  }
  function resetForm() {
    request.current = { key: "", id: "" };
    setForm((f) => ({
      ...blank(),
      typeId: f.typeId,
      pair: f.mode === "move" ? "" : f.pair,
    }));
    setFormOpen(false);
  }
  function openCreate() {
    if (!leave()) return;
    request.current = { key: "", id: "" };
    setForm((f) => ({
      ...blank(),
      typeId: f.typeId,
      pair: f.mode === "create" ? f.pair : "",
    }));
    setFormOpen(true);
  }
  function openMove(b: Booking) {
    if (!leave()) return;
    request.current = { key: "", id: "" };
    setForm({
      ...blank(),
      mode: "move",
      id: b.id,
      version: b.version,
      pair: `${b.groupId}|${b.subjectId}`,
      day: b.day,
      typeId: b.typeId,
      topic: b.topic,
    });
    setFormOpen(true);
  }
  const issues = slots?.issues || [];
  const chosen = slots?.slots.find((s) => s.lessonId === form.lessonId);
  const canSubmit =
    Boolean(
      groupId &&
      subjectId &&
      form.day &&
      form.lessonId &&
      form.typeId &&
      chosen &&
      !chosen.booked,
    ) &&
    !slots?.past &&
    !slots?.closed.length &&
    !slots?.outsideYear &&
    (form.mode === "create" || Boolean(form.reason.trim())) &&
    (!issues.length || Boolean(slots?.canOverride && form.override.trim()));
  async function submit() {
    const override = issues.length ? form.override.trim() : "";
    const body =
      form.mode === "create"
        ? {
            groupId,
            subjectId,
            day: form.day,
            lessonId: form.lessonId,
            typeId: form.typeId,
            topic: form.topic.trim(),
            note: form.note.trim(),
            publish: form.publish,
            overrideReason: override,
          }
        : {
            version: form.version,
            day: form.day,
            lessonId: form.lessonId,
            reason: form.reason.trim(),
            overrideReason: override,
          };
    const key = JSON.stringify([form.mode, form.id, body]);
    if (request.current.key !== key)
      request.current = { key, id: crypto.randomUUID() };
    const path =
      form.mode === "create"
        ? "assessment-calendar/bookings"
        : `assessment-calendar/bookings/${form.id}/move`;
    await run(
      async () => {
        const result = await api(path, workspace, "POST", {
          requestId: request.current.id,
          ...body,
        });
        resetForm();
        setSelectedId(result.id);
        setReload((n) => n + 1);
      },
      form.mode === "create" ? "Работа назначена" : "Работа перенесена",
    );
  }
  async function act(b: Booking, op: string, body: object, message: string) {
    await run(async () => {
      await api(
        `assessment-calendar/bookings/${b.id}/${op}`,
        workspace,
        "POST",
        body,
      );
      setReload((n) => n + 1);
    }, message);
  }
  async function cancel(b: Booking) {
    const reason = window.prompt("Причина отмены работы");
    if (!reason?.trim()) return;
    await act(
      b,
      "cancel",
      { version: b.version, reason: reason.trim() },
      "Работа отменена",
    );
  }
  async function saveSettings(body: Record<string, unknown>) {
    await run(async () => {
      await api("assessment-calendar/config", workspace, "PUT", body);
      await loadOptions();
    }, "Настройки сохранены");
  }
  function openTab(next: "calendar" | "load" | "settings") {
    if (next !== tab && !leave()) return;
    setTab(next);
  }

  const className = options?.classes.find((c) => c.id === classId)?.name || "";
  const exportHref = `/api/assessment-calendar/export?workspace=${encodeURIComponent(workspace)}&classId=${encodeURIComponent(classId)}&month=${month}`;
  const printable = shown.filter(
    (b) => b.status !== "cancelled" && b.day.slice(0, 7) === month,
  );
  const tabs: ["calendar" | "load" | "settings", string][] = [
    ["calendar", "Календарь"],
    ...(manager ? [["load", "Нагрузка школы"] as ["load", string]] : []),
    ...(options?.admin
      ? [["settings", "Настройки"] as ["settings", string]]
      : []),
  ];
  const activeTypes = (options?.types || []).filter((t) => t.active);
  const visibleDays = lessonDays.filter(
    (d) =>
      d >= (options?.today || "") &&
      d.slice(0, 7) === (form.day || month).slice(0, 7),
  );

  return (
    <dialog
      className="assessments"
      ref={dialog}
      aria-labelledby="assessments-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && leave()) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="assessments-title">Календарь контрольных</h2>
          <p>СОР, СОЧ, контрольные работы и экзамены по классам</p>
        </div>
        <button
          disabled={busy}
          onClick={() => {
            if (leave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {(busy || loading > 0) && <p role="status">Загрузка…</p>}
      <fieldset disabled={busy}>
        <div className="ac-controls">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => {
                if (!leave()) return;
                resetForm();
                setSelectedId("");
                setClassId("");
                setTeacherId("");
                setWorkspace(e.target.value);
              }}
            >
              {spaces.map((s) => (
                <option key={s} value={s}>
                  {s === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              void loadOptions();
              setReload((n) => n + 1);
            }}
          >
            Обновить
          </button>
        </div>

        {options && !staff && !options.family && (
          <p>
            Раздел недоступен. Календарь контрольных открыт учителям,
            ответственным за календарь, администрации, а также ученикам и
            родителям.
          </p>
        )}

        {options && !staff && options.family && (
          <FamilyView
            workspace={workspace}
            options={options}
            onError={setError}
          />
        )}

        {options && staff && (
          <nav className="ac-controls" aria-label="Разделы">
            {tabs.map(([id, title]) => (
              <button
                key={id}
                aria-pressed={tab === id}
                onClick={() => openTab(id)}
              >
                {title}
              </button>
            ))}
          </nav>
        )}

        {options && staff && tab === "load" && (
          <LoadGrid
            workspace={workspace}
            today={options.today}
            onError={setError}
          />
        )}

        {options && options.admin && tab === "settings" && (
          <Settings
            options={options}
            busy={busy}
            onSave={(body) => void saveSettings(body)}
          />
        )}

        {options && staff && tab === "calendar" && (
          <>
            <section className="ac-controls" aria-label="Фильтры">
              <div className="ac-controls" role="group" aria-label="Вид">
                <button
                  aria-pressed={view === "month"}
                  onClick={() => setView("month")}
                >
                  Месяц
                </button>
                <button
                  aria-pressed={view === "week"}
                  onClick={() => setView("week")}
                >
                  Неделя
                </button>
              </div>
              {view === "month" ? (
                <>
                  <button
                    aria-label="Предыдущий месяц"
                    onClick={() => setMonth(shiftMonth(month, -1))}
                  >
                    ←
                  </button>
                  <label>
                    Месяц
                    <input
                      type="month"
                      value={month}
                      onChange={(e) =>
                        e.target.value && setMonth(e.target.value)
                      }
                    />
                  </label>
                  <button
                    aria-label="Следующий месяц"
                    onClick={() => setMonth(shiftMonth(month, 1))}
                  >
                    →
                  </button>
                </>
              ) : (
                <>
                  <button
                    aria-label="Предыдущая неделя"
                    onClick={() => setWeekStart(addDays(weekStart, -7))}
                  >
                    ←
                  </button>
                  <strong>
                    {dayLabel(weekStart)} – {dayLabel(addDays(weekStart, 6))}
                  </strong>
                  <button
                    aria-label="Следующая неделя"
                    onClick={() => setWeekStart(addDays(weekStart, 7))}
                  >
                    →
                  </button>
                </>
              )}
              <button
                onClick={() => {
                  setMonth(options.today.slice(0, 7));
                  setWeekStart(mondayOf(options.today));
                }}
              >
                Сегодня
              </button>
              <label>
                Класс
                <select
                  value={classId}
                  onChange={(e) => setClassId(e.target.value)}
                >
                  <option value="">
                    {manager ? "Все классы" : "Все мои классы"}
                  </option>
                  {options.classes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              {manager ? (
                <label>
                  Учитель
                  <select
                    value={teacherId}
                    onChange={(e) => setTeacherId(e.target.value)}
                  >
                    <option value="">Все</option>
                    {options.teachers.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                mineId && (
                  <label className="ac-check">
                    <input
                      type="checkbox"
                      checked={mineOnly}
                      onChange={(e) => setMineOnly(e.target.checked)}
                    />
                    Только мои предметы
                  </label>
                )
              )}
              <label className="ac-check">
                <input
                  type="checkbox"
                  checked={showCancelled}
                  onChange={(e) => setShowCancelled(e.target.checked)}
                />
                Показывать отменённые
              </label>
              {classId && view === "month" && (
                <>
                  <a href={exportHref} download>
                    CSV за месяц
                  </a>
                  <button onClick={() => window.print()}>Печать месяца</button>
                </>
              )}
            </section>

            {options.canBook && !formOpen && (
              <div className="ac-controls">
                <button onClick={() => openCreate()}>Назначить работу</button>
              </div>
            )}

            {options.canBook && formOpen && (
              <section
                className="ac-form"
                aria-label={
                  form.mode === "create" ? "Новая работа" : "Перенос работы"
                }
              >
                <h3>
                  {form.mode === "create"
                    ? "Назначить работу"
                    : "Перенести работу"}
                </h3>
                <div className="ac-controls">
                  <label>
                    Предмет и группа
                    <select
                      value={form.pair}
                      disabled={form.mode === "move"}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          pair: e.target.value,
                          lessonId: "",
                        }))
                      }
                    >
                      <option value="">Выберите предмет и группу</option>
                      {options.pairs.map((p) => (
                        <option
                          key={p.groupId + p.subjectId}
                          value={`${p.groupId}|${p.subjectId}`}
                        >
                          {p.classes} · {p.subject}
                          {p.group !== p.classes ? ` · ${p.group}` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Дата
                    <input
                      type="date"
                      min={options.today}
                      max={options.yearEnd}
                      value={form.day}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          day: e.target.value,
                          lessonId: "",
                        }))
                      }
                    />
                  </label>
                  <label>
                    Тип
                    <select
                      value={form.typeId}
                      disabled={form.mode === "move"}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, typeId: e.target.value }))
                      }
                    >
                      {activeTypes.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.title}
                        </option>
                      ))}
                      {form.mode === "move" &&
                        !activeTypes.some((t) => t.id === form.typeId) && (
                          <option value={form.typeId}>
                            {items.find((b) => b.id === form.id)?.type}
                          </option>
                        )}
                    </select>
                  </label>
                </div>
                {form.pair && (
                  <p className="ac-meta">
                    {visibleDays.length
                      ? `Уроки: ${visibleDays.slice(0, 14).map(shortDay).join(", ")}${visibleDays.length > 14 ? "…" : ""}.`
                      : "В показанном месяце уроков по этому предмету нет."}{" "}
                    Выберите день в календаре или укажите дату.
                  </p>
                )}
                {slots && (
                  <fieldset className="ac-slots">
                    <legend>
                      Урок {dayLabel(slots.day)}, {weekday(slots.day)}
                    </legend>
                    {slots.past && <p className="ac-warn">Дата уже прошла.</p>}
                    {slots.outsideYear && (
                      <p className="ac-warn">Дата вне учебного года.</p>
                    )}
                    {slots.closed.length > 0 && (
                      <p className="ac-warn">
                        Период журнала закрыт: {slots.closed.join(", ")}
                      </p>
                    )}
                    {!slots.slots.length && (
                      <p>В этот день у группы нет урока по предмету.</p>
                    )}
                    {slots.slots.map((s) => (
                      <label key={s.lessonId} className="ac-check">
                        <input
                          type="radio"
                          name="lesson"
                          checked={form.lessonId === s.lessonId}
                          disabled={Boolean(s.booked)}
                          onChange={() =>
                            setForm((f) => ({ ...f, lessonId: s.lessonId }))
                          }
                        />
                        {clock(s.start, s.end)} ·{" "}
                        {s.teachers || "учитель не назначен"}
                        {s.substitute ? " (замена)" : ""}
                        {s.booked
                          ? ` — занято: ${s.booked.short}${s.booked.topic ? ` «${s.booked.topic}»` : ""}`
                          : ""}
                      </label>
                    ))}
                  </fieldset>
                )}
                {form.mode === "create" && (
                  <>
                    <label className="ac-wide">
                      Тема
                      <input
                        maxLength={200}
                        value={form.topic}
                        onChange={(e) =>
                          setForm((f) => ({ ...f, topic: e.target.value }))
                        }
                      />
                    </label>
                    <label className="ac-wide">
                      Заметка для сотрудников
                      <textarea
                        maxLength={1000}
                        value={form.note}
                        onChange={(e) =>
                          setForm((f) => ({ ...f, note: e.target.value }))
                        }
                      />
                    </label>
                    <label className="ac-check">
                      <input
                        type="checkbox"
                        checked={form.publish}
                        onChange={(e) =>
                          setForm((f) => ({ ...f, publish: e.target.checked }))
                        }
                      />
                      Сразу показать ученикам и родителям
                    </label>
                  </>
                )}
                {form.mode === "move" && (
                  <label className="ac-wide">
                    Причина переноса
                    <input
                      maxLength={500}
                      value={form.reason}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, reason: e.target.value }))
                      }
                    />
                  </label>
                )}
                {issues.length > 0 && (
                  <div role="alert" className="ac-issues">
                    <strong>Нагрузка класса превышена</strong>
                    <ul>
                      {issues.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                    {slots?.canOverride ? (
                      <label className="ac-wide">
                        Причина превышения
                        <input
                          maxLength={500}
                          value={form.override}
                          onChange={(e) =>
                            setForm((f) => ({ ...f, override: e.target.value }))
                          }
                        />
                      </label>
                    ) : (
                      <p>
                        Выберите другую дату или обратитесь к администрации.
                      </p>
                    )}
                  </div>
                )}
                <div className="ac-controls">
                  <button disabled={!canSubmit} onClick={() => void submit()}>
                    {form.mode === "create" ? "Назначить" : "Перенести"}
                  </button>
                  <button
                    onClick={() => {
                      if (leave()) resetForm();
                    }}
                  >
                    Отмена
                  </button>
                </div>
              </section>
            )}

            {selected && (
              <BookingCard
                booking={selected}
                workspace={workspace}
                types={options.types}
                admin={options.admin}
                busy={busy}
                onPublish={() =>
                  void act(
                    selected,
                    "publish",
                    { version: selected.version },
                    "Работа опубликована, семьи получили уведомление",
                  )
                }
                onMove={() => openMove(selected)}
                onCancel={() => void cancel(selected)}
                onEdit={(body) =>
                  void act(
                    selected,
                    "edit",
                    { version: selected.version, ...body },
                    "Работа изменена",
                  )
                }
                onClose={() => setSelectedId("")}
              />
            )}

            {!shown.length && !loading && (
              <p>Нет назначенных работ за этот период.</p>
            )}
            {range && (
              <CalendarGrid
                weeks={
                  view === "month"
                    ? weeks
                    : [
                        Array.from({ length: 7 }, (_, i) =>
                          addDays(weekStart, i),
                        ),
                      ]
                }
                month={view === "month" ? month : ""}
                items={shown}
                events={events}
                today={options.today}
                lessonDays={lessonDays.filter((d) => d >= options.today)}
                canPick={options.canBook && formOpen}
                selected={selectedId}
                onSelect={setSelectedId}
                onPick={(day) => setForm((f) => ({ ...f, day, lessonId: "" }))}
              />
            )}

            {classId && view === "month" && (
              <section className="ac-print" aria-label="Печать месяца">
                <h2>
                  Контрольные работы, {className}, {monthTitle(month)}
                </h2>
                <table>
                  <thead>
                    <tr>
                      <th>Дата</th>
                      <th>Время</th>
                      <th>Предмет</th>
                      <th>Тип</th>
                      <th>Тема</th>
                      <th>Учителя</th>
                    </tr>
                  </thead>
                  <tbody>
                    {printable.map((b) => (
                      <tr key={b.id}>
                        <td>
                          {dayLabel(b.day)}, {weekday(b.day)}
                        </td>
                        <td>{clock(b.start, b.end)}</td>
                        <td>{b.subject}</td>
                        <td>{b.short}</td>
                        <td>{b.topic}</td>
                        <td>{b.teachers}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!printable.length && (
                  <p>Нет назначенных работ за этот период.</p>
                )}
              </section>
            )}
          </>
        )}
      </fieldset>
    </dialog>
  );
}

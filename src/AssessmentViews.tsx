import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "./api";
import {
  actionLabels,
  addDays,
  clock,
  dayLabel,
  eventsOn,
  groupByDay,
  loadClass,
  mondayOf,
  monthRange,
  monthTitle,
  shiftMonth,
  shortDay,
  weekday,
  weekdayNames,
  type Booking,
  type FamilyItem,
  type LoadData,
  type Options,
  type SchoolEvent,
  type WorkType,
} from "./assessmentCalendar";

const stamp = (value: string) =>
  new Date(value).toLocaleString("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  });

export function CalendarGrid({
  weeks,
  month,
  items,
  events,
  today,
  lessonDays,
  canPick,
  selected,
  onSelect,
  onPick,
}: {
  weeks: string[][];
  month: string;
  items: Booking[];
  events: SchoolEvent[];
  today: string;
  lessonDays: string[];
  canPick: boolean;
  selected: string;
  onSelect: (id: string) => void;
  onPick: (day: string) => void;
}) {
  const byDay = groupByDay(items);
  return (
    <div className="ac-month">
      {weekdayNames.map((name) => (
        <div className="ac-head" key={name} aria-hidden="true">
          {name}
        </div>
      ))}
      {weeks.map((week) => (
        <div className="ac-week" key={week[0]}>
          <h4 className="ac-week-title">Неделя с {shortDay(week[0])}</h4>
          {week.map((day) => {
            const list = byDay.get(day) || [],
              marks = eventsOn(events, day),
              lesson = lessonDays.includes(day);
            return (
              <div
                key={day}
                className={[
                  "ac-day",
                  month && day.slice(0, 7) !== month ? "other" : "",
                  day === today ? "today" : "",
                  lesson ? "lesson" : "",
                  list.length || marks.length ? "" : "empty",
                ]
                  .join(" ")
                  .trim()}
              >
                <div className="ac-daylabel">
                  {canPick ? (
                    <button
                      className="ac-pick"
                      onClick={() => onPick(day)}
                      aria-label={`Выбрать дату ${dayLabel(day)} для новой работы`}
                    >
                      {Number(day.slice(8))}
                    </button>
                  ) : (
                    <span>{Number(day.slice(8))}</span>
                  )}
                  <span className="ac-dow">
                    {weekday(day)}, {shortDay(day)}
                  </span>
                  {lesson && (
                    <span
                      className="ac-lessonmark"
                      title="Есть урок по предмету"
                    >
                      ● урок
                    </span>
                  )}
                </div>
                {marks.map((e) => (
                  <p className={`ac-event ${e.effect}`} key={e.name + e.start}>
                    {e.effect === "cancel" ? "Занятий нет: " : ""}
                    {e.name}
                  </p>
                ))}
                {list.map((b) => (
                  <button
                    key={b.id}
                    className={`ac-chip ${b.category} ${b.status}`}
                    aria-pressed={selected === b.id}
                    onClick={() => onSelect(b.id)}
                  >
                    <strong>{b.short}</strong> · {b.subject} · {b.classes}
                    <span className="ac-badge">{b.statusTitle}</span>
                    {!b.slotValid && (
                      <span className="ac-warn">⚠ {b.slotMessage}</span>
                    )}
                  </button>
                ))}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

export function BookingCard({
  booking: b,
  workspace,
  types,
  admin,
  busy,
  onPublish,
  onMove,
  onCancel,
  onEdit,
  onClose,
}: {
  booking: Booking;
  workspace: string;
  types: WorkType[];
  admin: boolean;
  busy: boolean;
  onPublish: () => void;
  onMove: () => void;
  onCancel: () => void;
  onEdit: (body: {
    typeId: string;
    topic: string;
    note: string;
    overrideReason: string;
  }) => void;
  onClose: () => void;
}) {
  const [history, setHistory] = useState<
    | {
        seq: number;
        actor: string;
        created: string;
        action: string;
        reason: string;
      }[]
    | null
  >(null);
  const [editing, setEditing] = useState<null | {
    typeId: string;
    topic: string;
    note: string;
    override: string;
  }>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setHistory(null);
    setEditing(null);
    setError("");
  }, [b.id, b.version]);
  async function showHistory() {
    setError("");
    try {
      const result = await api(
        `assessment-calendar/bookings/${b.id}/history`,
        workspace,
      );
      setHistory(result.items);
    } catch (e: any) {
      setError(e.message);
    }
  }
  const choices = types.filter((t) => t.active || t.id === b.typeId);
  return (
    <section className="ac-card" aria-label="Работа">
      <div className="ac-controls">
        <h3>
          {b.short} · {b.subject}
        </h3>
        <button onClick={onClose}>Закрыть карточку</button>
      </div>
      <p>
        <span className="ac-badge">{b.statusTitle}</span> {dayLabel(b.day)},{" "}
        {weekday(b.day)}, {clock(b.start, b.end)} · {b.type}
      </p>
      <p>
        Классы: {b.classes} · Группа: {b.group}
        {b.teachers ? ` · Учителя: ${b.teachers}` : ""}
      </p>
      {b.topic && <p>Тема: {b.topic}</p>}
      {b.note && <p className="ac-note">Заметка для сотрудников: {b.note}</p>}
      {b.override && (
        <p className="ac-warn">
          Превышение нагрузки разрешил(а) {b.overrideBy}: {b.override}
        </p>
      )}
      {b.status === "cancelled" && b.cancelReason && (
        <p>Причина отмены: {b.cancelReason}</p>
      )}
      {!b.slotValid && <p className="ac-warn">⚠ {b.slotMessage}</p>}
      <p className="ac-meta">
        Назначил(а): {b.author}, обновлено {stamp(b.updated)}
      </p>
      <div className="ac-controls">
        {b.canChange && b.status === "planned" && (
          <button disabled={busy} onClick={onPublish}>
            Опубликовать
          </button>
        )}
        {b.canChange && (
          <>
            <button disabled={busy} onClick={onMove}>
              Перенести
            </button>
            <button
              disabled={busy}
              onClick={() =>
                setEditing({
                  typeId: b.typeId,
                  topic: b.topic,
                  note: b.note,
                  override: "",
                })
              }
            >
              Изменить
            </button>
            <button disabled={busy} onClick={onCancel}>
              Отменить
            </button>
          </>
        )}
        <button disabled={busy} onClick={() => void showHistory()}>
          История
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {editing && (
        <form
          className="ac-edit"
          onSubmit={(e) => {
            e.preventDefault();
            onEdit({
              typeId: editing.typeId,
              topic: editing.topic.trim(),
              note: editing.note.trim(),
              overrideReason: editing.override.trim(),
            });
          }}
        >
          <label>
            Тип
            <select
              value={editing.typeId}
              onChange={(e) =>
                setEditing({ ...editing, typeId: e.target.value })
              }
            >
              {choices.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Тема
            <input
              maxLength={200}
              value={editing.topic}
              onChange={(e) =>
                setEditing({ ...editing, topic: e.target.value })
              }
            />
          </label>
          <label className="ac-wide">
            Заметка для сотрудников
            <textarea
              maxLength={1000}
              value={editing.note}
              onChange={(e) => setEditing({ ...editing, note: e.target.value })}
            />
          </label>
          {admin && editing.typeId !== b.typeId && (
            <label className="ac-wide">
              Причина превышения (если новый тип увеличивает нагрузку)
              <input
                maxLength={500}
                value={editing.override}
                onChange={(e) =>
                  setEditing({ ...editing, override: e.target.value })
                }
              />
            </label>
          )}
          <div className="ac-controls">
            <button disabled={busy}>Сохранить</button>
            <button type="button" onClick={() => setEditing(null)}>
              Не менять
            </button>
          </div>
        </form>
      )}
      {history && (
        <ol className="ac-history" aria-label="История работы">
          {history.map((h) => (
            <li key={h.seq}>
              {stamp(h.created)} · {h.actor} ·{" "}
              {actionLabels[h.action] || h.action}
              {h.reason ? ` — ${h.reason}` : ""}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function LoadGrid({
  workspace,
  today,
  onError,
}: {
  workspace: string;
  today: string;
  onError: (message: string) => void;
}) {
  const [start, setStart] = useState(mondayOf(today));
  const [data, setData] = useState<LoadData | null>(null);
  const [busyOnly, setBusyOnly] = useState(true);
  const [loading, setLoading] = useState(false);
  const sequence = useRef(0);
  useEffect(() => {
    const token = ++sequence.current;
    setLoading(true);
    onError("");
    api(
      `assessment-calendar/load?start=${start}&end=${addDays(start, 13)}`,
      workspace,
    )
      .then((result) => token === sequence.current && setData(result))
      .catch((e: any) => {
        if (token !== sequence.current) return;
        setData(null);
        onError(e.message);
      })
      .finally(() => token === sequence.current && setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, start]);
  useEffect(
    () => () => {
      ++sequence.current;
    },
    [],
  );
  const rows = (data?.rows || []).filter(
    (r) =>
      !busyOnly ||
      Object.values(r.cells).some((c) => c.items.length) ||
      Object.values(r.weeks).some(Boolean),
  );
  return (
    <section aria-label="Нагрузка школы">
      <div className="ac-controls">
        <button onClick={() => setStart(addDays(start, -7))}>
          ← Неделя назад
        </button>
        <strong>
          {dayLabel(start)} – {dayLabel(addDays(start, 13))}
        </strong>
        <button onClick={() => setStart(addDays(start, 7))}>
          Неделя вперёд →
        </button>
        <button onClick={() => setStart(mondayOf(today))}>Сегодня</button>
        <label className="ac-check">
          <input
            type="checkbox"
            checked={busyOnly}
            onChange={(e) => setBusyOnly(e.target.checked)}
          />
          Только классы с работами
        </label>
      </div>
      {loading && <p role="status">Загрузка…</p>}
      {data && (
        <>
          <p className="ac-meta">
            Лимит: не больше {data.maxPerDay == null ? "—" : data.maxPerDay} в
            день и {data.maxPerWeek == null ? "—" : data.maxPerWeek} в неделю.
            Красная ячейка с «!» — лимит превышен (разрешено администратором).
          </p>
          {!rows.length && <p>Нет назначенных работ за этот период.</p>}
          {rows.length > 0 && (
            <div className="ac-scroll">
              <table className="ac-load">
                <thead>
                  <tr>
                    <th scope="col">Класс</th>
                    {data.days.map((d, i) => (
                      <Fragment key={d}>
                        <th scope="col">
                          {weekday(d)} {shortDay(d)}
                        </th>
                        {i % 7 === 6 && <th scope="col">Неделя</th>}
                      </Fragment>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.classId}>
                      <th scope="row">{r.name}</th>
                      {data.days.map((d, i) => {
                        const cell = r.cells[d],
                          over = loadClass(cell?.load || 0, data.maxPerDay);
                        return (
                          <Fragment key={d}>
                            <td
                              className={over}
                              title={cell?.items
                                .map((x) => x.subject)
                                .join(", ")}
                            >
                              {cell && cell.load > 0 && (
                                <>
                                  <strong>{cell.load}</strong>
                                  {over && (
                                    <span aria-label="Превышение лимита">
                                      {" "}
                                      !
                                    </span>
                                  )}
                                  <small>
                                    {cell.items.map((x) => x.short).join(", ")}
                                  </small>
                                </>
                              )}
                            </td>
                            {i % 7 === 6 && (
                              <td
                                className={loadClass(
                                  r.weeks[data.weeks[Math.floor(i / 7)]] || 0,
                                  data.maxPerWeek,
                                )}
                              >
                                {r.weeks[data.weeks[Math.floor(i / 7)]] || ""}
                                {loadClass(
                                  r.weeks[data.weeks[Math.floor(i / 7)]] || 0,
                                  data.maxPerWeek,
                                ) && " !"}
                              </td>
                            )}
                          </Fragment>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function FamilyView({
  workspace,
  options,
  onError,
}: {
  workspace: string;
  options: Options;
  onError: (message: string) => void;
}) {
  const [student, setStudent] = useState(options.students[0]?.id || "");
  const [month, setMonth] = useState(options.today.slice(0, 7));
  const [items, setItems] = useState<FamilyItem[] | null>(null);
  const sequence = useRef(0);
  useEffect(() => {
    if (!student) return;
    const token = ++sequence.current,
      range = monthRange(month);
    onError("");
    setItems(null);
    api(
      `assessment-calendar/family?studentId=${encodeURIComponent(student)}&start=${range.start}&end=${range.end}`,
      workspace,
    )
      .then((r) => token === sequence.current && setItems(r.items))
      .catch((e: any) => token === sequence.current && onError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, student, month]);
  useEffect(
    () => () => {
      ++sequence.current;
    },
    [],
  );
  if (!options.students.length)
    return (
      <p>
        Для этой учётной записи нет доступных учеников. Календарь контрольных
        показывает работы классов и групп ребёнка.
      </p>
    );
  return (
    <section aria-label="Контрольные работы ребёнка">
      <div className="ac-controls">
        {options.students.length > 1 && (
          <label>
            Ученик
            <select
              value={student}
              onChange={(e) => setStudent(e.target.value)}
            >
              {options.students.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.className ? ` · ${s.className}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <button onClick={() => setMonth(shiftMonth(month, -1))}>←</button>
        <strong>{monthTitle(month)}</strong>
        <button onClick={() => setMonth(shiftMonth(month, 1))}>→</button>
      </div>
      {items && !items.length && <p>Нет назначенных работ за этот период.</p>}
      {items && items.length > 0 && (
        <ul className="ac-family">
          {items.map((x) => (
            <li key={x.id} className="ac-chip other">
              <strong>
                {dayLabel(x.day)}, {weekday(x.day)}, {clock(x.start, x.end)}
              </strong>
              <span>
                {x.subject} · {x.short} — {x.type}
              </span>
              {x.topic && <span>Тема: {x.topic}</span>}
              {x.teachers && <span>Учитель: {x.teachers}</span>}
              {!x.slotValid && (
                <span className="ac-warn">
                  <span className="ac-badge">Дата уточняется</span>{" "}
                  {x.slotMessage}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function Settings({
  options,
  busy,
  onSave,
}: {
  options: Options;
  busy: boolean;
  onSave: (body: Record<string, unknown>) => void;
}) {
  const initial = () => ({
    types: options.types.map((t) => ({ ...t })),
    day: options.maxPerDay == null ? "" : String(options.maxPerDay),
    week: options.maxPerWeek == null ? "" : String(options.maxPerWeek),
    windows: Object.fromEntries(
      options.windows.map((w) => [w.periodId, { start: w.start, end: w.end }]),
    ) as Record<string, { start: string; end: string }>,
    coordinators: [...options.coordinatorIds],
    reason: "",
  });
  const [form, setForm] = useState(initial);
  const [problem, setProblem] = useState("");
  const saved = new Set(options.types.map((t) => t.id));
  useEffect(() => {
    setForm(initial());
    setProblem("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.configVersion]);
  function patch(i: number, change: Partial<WorkType>) {
    setForm((f) => ({
      ...f,
      types: f.types.map((t, j) => (j === i ? { ...t, ...change } : t)),
    }));
  }
  function limit(text: string, max: number, name: string) {
    if (!text.trim()) return null;
    const value = Number(text);
    if (!Number.isInteger(value) || value < 1 || value > max)
      throw new Error(`${name}: целое число от 1 до ${max} или пусто.`);
    return value;
  }
  function save() {
    try {
      const windows = Object.entries(form.windows)
        .filter(([, w]) => w.start || w.end)
        .map(([periodId, w]) => {
          if (!w.start || !w.end)
            throw new Error("Окно СОЧ: укажите обе даты или очистите обе.");
          return { periodId, start: w.start, end: w.end };
        });
      if (form.types.some((t) => !t.title.trim() || !t.short.trim()))
        throw new Error("У каждого типа должны быть название и краткое имя.");
      setProblem("");
      onSave({
        version: options.configVersion,
        reason: form.reason.trim(),
        types: form.types.map((t) => ({
          ...t,
          title: t.title.trim(),
          short: t.short.trim(),
        })),
        maxPerDay: limit(form.day, 10, "Не больше в день"),
        maxPerWeek: limit(form.week, 30, "Не больше в неделю"),
        windows,
        coordinatorIds: form.coordinators,
      });
    } catch (e: any) {
      setProblem(e.message);
    }
  }
  return (
    <section aria-label="Настройки календаря">
      <h3>Типы работ</h3>
      <div className="ac-scroll">
        <table className="ac-types">
          <thead>
            <tr>
              <th scope="col">Код</th>
              <th scope="col">Название</th>
              <th scope="col">Кратко</th>
              <th scope="col">Категория</th>
              <th scope="col">Учитывать в нагрузке</th>
              <th scope="col">Окно СОЧ</th>
              <th scope="col">Действует</th>
              <th scope="col" />
            </tr>
          </thead>
          <tbody>
            {form.types.map((t, i) => (
              <tr key={t.id}>
                <td>{t.id}</td>
                <td>
                  <input
                    aria-label={`Название типа ${t.id}`}
                    maxLength={80}
                    value={t.title}
                    onChange={(e) => patch(i, { title: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    aria-label={`Краткое имя типа ${t.id}`}
                    maxLength={12}
                    value={t.short}
                    onChange={(e) => patch(i, { short: e.target.value })}
                  />
                </td>
                <td>
                  <select
                    aria-label={`Категория типа ${t.id}`}
                    value={t.category}
                    onChange={(e) =>
                      patch(i, {
                        category: e.target.value as WorkType["category"],
                      })
                    }
                  >
                    {Object.entries(options.categories).map(([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ))}
                  </select>
                </td>
                {(["counted", "window", "active"] as const).map((key) => (
                  <td key={key}>
                    <input
                      type="checkbox"
                      aria-label={`${key === "counted" ? "Учитывать в нагрузке" : key === "window" ? "Окно СОЧ" : "Действует"}: ${t.title || t.id}`}
                      checked={t[key]}
                      onChange={(e) => patch(i, { [key]: e.target.checked })}
                    />
                  </td>
                ))}
                <td>
                  {!saved.has(t.id) && (
                    <button
                      onClick={() =>
                        setForm((f) => ({
                          ...f,
                          types: f.types.filter((_, j) => j !== i),
                        }))
                      }
                    >
                      Убрать
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="ac-meta">
        Тип, по которому уже есть работы, нельзя удалить — только отключить.
      </p>
      <button
        disabled={form.types.length >= 30}
        onClick={() =>
          setForm((f) => ({
            ...f,
            types: [
              ...f.types,
              {
                id: "t-" + crypto.randomUUID().slice(0, 8),
                title: "",
                short: "",
                category: "other",
                counted: true,
                window: false,
                active: true,
              },
            ],
          }))
        }
      >
        Добавить тип
      </button>
      <h3>Нагрузка класса</h3>
      <div className="ac-controls">
        <label>
          Не больше в день
          <input
            type="number"
            min={1}
            max={10}
            value={form.day}
            onChange={(e) => setForm((f) => ({ ...f, day: e.target.value }))}
          />
        </label>
        <label>
          Не больше в неделю
          <input
            type="number"
            min={1}
            max={30}
            value={form.week}
            onChange={(e) => setForm((f) => ({ ...f, week: e.target.value }))}
          />
        </label>
      </div>
      <p className="ac-meta">
        Пусто — без ограничения. Работа считается для учеников, которые её
        пишут: параллельные подгруппы друг другу не мешают.
      </p>
      <h3>Окна СОЧ по учебным периодам</h3>
      {!options.periods.length && (
        <p>
          В журнале пока нет учебных периодов. Окно СОЧ можно задать после
          создания периода.
        </p>
      )}
      {options.periods.map((p) => (
        <div className="ac-controls" key={p.id}>
          <strong>
            {p.name} ({dayLabel(p.start)} – {dayLabel(p.end)})
          </strong>
          {(["start", "end"] as const).map((side) => (
            <label key={side}>
              {side === "start" ? "Окно с" : "Окно по"}
              <input
                type="date"
                min={p.start}
                max={p.end}
                value={form.windows[p.id]?.[side] || ""}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    windows: {
                      ...f.windows,
                      [p.id]: {
                        ...(f.windows[p.id] || { start: "", end: "" }),
                        [side]: e.target.value,
                      },
                    },
                  }))
                }
              />
            </label>
          ))}
        </div>
      ))}
      <h3>Ответственные за календарь</h3>
      <p className="ac-meta">
        Видят нагрузку школы и назначают работы по любым предметам. Превышение
        лимита и настройки остаются за администрацией.
      </p>
      <fieldset className="ac-people">
        {options.candidates.map((c) => (
          <label key={c.id} className="ac-check">
            <input
              type="checkbox"
              checked={form.coordinators.includes(c.id)}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  coordinators: e.target.checked
                    ? [...f.coordinators, c.id]
                    : f.coordinators.filter((id) => id !== c.id),
                }))
              }
            />
            {c.name}
          </label>
        ))}
      </fieldset>
      {problem && <p role="alert">{problem}</p>}
      <label className="ac-wide">
        Причина изменения
        <input
          maxLength={500}
          value={form.reason}
          onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
        />
      </label>
      <button disabled={busy || !form.reason.trim()} onClick={save}>
        Сохранить настройки
      </button>
    </section>
  );
}

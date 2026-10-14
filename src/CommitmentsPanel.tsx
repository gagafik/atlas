import { useEffect, useState } from "react";
import type { State, Commitment, ScheduleIssue } from "./types";
import { api } from "./api";
import { uid, named, csvCell, download } from "./utils";
import ChangePreview, { type Preview } from "./ChangePreview";
import { Multi } from "./DatedSchedule";

interface Allocation {
  status: string;
  message?: string;
  details?: string[];
  preview?: Preview;
  assigned?: number;
  locked?: number;
  fairnessOptimal?: boolean;
  firstDate?: string;
  lastDate?: string;
  minutes?: { teacherId: string; previous: number; total: number }[];
  issues?: ScheduleIssue[];
}
export default function CommitmentsPanel({
  state: s,
  workspace,
  onState,
  initialId,
  onDate,
}: {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
  initialId?: string;
  onDate: (day: string) => void;
}) {
  const all = s.commitments || [];
  const initial = all.find((x) => x.id === initialId);
  const [tab, setTab] = useState<"booking" | "duty">(
    initial?.kind || "booking",
  );
  const [value, setValue] = useState<Commitment | null>(initial || null);
  const [copies, setCopies] = useState(1),
    [step, setStep] = useState(1);
  const [from, setFrom] = useState(s.settings.yearStart),
    [to, setTo] = useState(s.settings.yearEnd);
  const [query, setQuery] = useState(""),
    [selected, setSelected] = useState<string[]>([]),
    [limit, setLimit] = useState(60);
  const [preview, setPreview] = useState<Preview | null>(null),
    [result, setResult] = useState<Allocation | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [seconds, setSeconds] = useState(10),
    [maxMinutes, setMaxMinutes] = useState(0);
  const [history, setHistory] = useState({ undo: 0, redo: 0 });
  useEffect(() => {
    let live = true;
    api("history", workspace)
      .then((r) => {
        if (live) setHistory(r);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [s.revision, workspace]);
  useEffect(() => {
    setPreview(null);
    setResult(null);
    setValue(null);
    setSelected([]);
  }, [s.revision, workspace]);
  useEffect(() => {
    if (initial) {
      setValue(initial);
      setTab(initial.kind);
    }
  }, [initialId]);
  const visible = all
    .filter(
      (x) =>
        x.kind === tab &&
        x.onDate >= from &&
        x.onDate <= to &&
        (!query ||
          `${x.name} ${x.location} ${x.teacherIds.map((id) => named(s.teachers, id)).join(" ")} ${x.roomIds.map((id) => named(s.rooms, id)).join(" ")}`
            .toLocaleLowerCase()
            .includes(query.toLocaleLowerCase())),
    )
    .sort((a, b) =>
      `${a.onDate} ${a.startTime}`.localeCompare(`${b.onDate} ${b.startTime}`),
    );
  const chosen = all.filter((x) => selected.includes(x.id));
  function close() {
    setValue(null);
    setPreview(null);
    setResult(null);
    setError("");
  }
  function create(kind: "booking" | "duty") {
    close();
    setTab(kind);
    setCopies(1);
    setStep(1);
    const today = new Date().toLocaleDateString("sv-SE");
    setValue({
      id: uid(),
      name: kind === "booking" ? "Бронирование" : "Дежурство",
      kind,
      onDate:
        today >= s.settings.yearStart && today <= s.settings.yearEnd
          ? today
          : s.settings.yearStart,
      startTime: "08:00",
      endTime: "08:20",
      roomIds: [],
      teacherIds: [],
      groupIds: [],
      profileId: s.profiles[0]?.id || null,
      location: "",
      buildingId: null,
      studentCount: null,
      requiredTeachers: kind === "duty" ? 1 : 0,
      eligibleTeacherIds: [],
      locked: false,
      note: "",
    });
  }
  async function check(removeIds?: string[]) {
    if (busy) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setPreview(
        await api("commitments/preview", workspace, "POST", {
          revision: s.revision,
          value: removeIds ? null : value,
          removeIds: removeIds || [],
          copies: removeIds ? 1 : copies,
          weekStep: step,
        }),
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function allocate() {
    if (busy) return;
    setBusy(true);
    setError("");
    setPreview(null);
    setValue(null);
    setResult(null);
    try {
      const r = await api("commitments/allocate", workspace, "POST", {
        revision: s.revision,
        commitmentIds: selected,
        seconds,
        maxMinutesPerTeacher: maxMinutes,
      });
      setResult(r);
      setPreview(r.preview || null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!preview || busy) return;
    setBusy(true);
    setError("");
    try {
      await onState(
        await api("editor/apply", workspace, "POST", {
          revision: preview.revision,
          token: preview.token,
        }),
      );
      close();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function historyAction(action: "undo" | "redo") {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await onState(
        await api(action, workspace, "POST", { revision: s.revision }),
      );
      close();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function exportRows() {
    const rows = [
      [
        "Дата",
        "Начало",
        "Конец",
        "Название",
        "Место",
        "Кабинеты",
        "Учителя",
        "Группы",
        "Требуется дежурных",
        "Закреплено",
      ],
      ...visible.map((x) => [
        x.onDate,
        x.startTime,
        x.endTime,
        x.name,
        x.location,
        x.roomIds.map((id) => named(s.rooms, id)).join(", "),
        x.teacherIds.map((id) => named(s.teachers, id)).join(", "),
        x.groupIds.map((id) => named(s.groups, id)).join(", "),
        String(x.requiredTeachers),
        x.locked ? "Да" : "Нет",
      ]),
    ];
    download(
      `${tab === "duty" ? "Дежурства" : "Бронирования"}.csv`,
      "\uFEFF" + rows.map((r) => r.map(csvCell).join(";")).join("\r\n"),
      "text/csv;charset=utf-8",
    );
  }
  return (
    <div className="commitments-panel dated-panel">
      <p className="info-line">
        Кабинеты и сотрудники проверяются вместе с фактическими уроками и
        заменами. Бронирования и дежурства действуют в указанные даты, в том
        числе во время каникул; учебные часы не меняются.
      </p>
      <div
        className="exchange-tabs"
        role="group"
        aria-label="Бронирования и дежурства"
      >
        {(
          [
            ["booking", "Бронирования"],
            ["duty", "Дежурства"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "selected" : ""}
            onClick={() => {
              setTab(id);
              setSelected([]);
              close();
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="dated-toolbar">
        <button className="primary" disabled={busy} onClick={() => create(tab)}>
          {tab === "booking" ? "Забронировать кабинет" : "Добавить дежурство"}
        </button>
        <button
          disabled={busy || !history.undo}
          onClick={() => historyAction("undo")}
        >
          Отменить действие
        </button>
        <button
          disabled={busy || !history.redo}
          onClick={() => historyAction("redo")}
        >
          Повторить действие
        </button>
        <button disabled={!visible.length} onClick={exportRows}>
          Скачать CSV
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p role="status">
          {result ? "Применяю изменения…" : "Проверяю данные / выполняю поиск…"}
        </p>
      )}
      {result && (
        <section className="dated-issues">
          <h3>
            {result.status === "success"
              ? "Дежурные распределены для предпросмотра"
              : result.message}
          </h3>
          {result.details && (
            <ul>
              {result.details.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          )}
          {result.status === "success" && (
            <>
              <p>
                Подготовлено дежурств: {result.assigned}; закреплённых
                сохранено: {result.locked}.{" "}
                {result.fairnessOptimal
                  ? "Найден оптимум по распределению минут."
                  : "Найден допустимый вариант; оптимальность не подтверждена."}{" "}
                Изменения сохранятся после применения.
              </p>
              <p>
                Учитываются дежурства {result.firstDate} — {result.lastDate},
                включая уже назначенные за этот период.
              </p>
              <details>
                <summary>Минуты дежурства по сотрудникам</summary>
                <div className="commitment-minutes">
                  {result.minutes?.map((x) => (
                    <div key={x.teacherId}>
                      <span>{named(s.teachers, x.teacherId)}</span>
                      <strong>
                        {x.previous} → {x.total} мин.
                      </strong>
                    </div>
                  ))}
                </div>
              </details>
              {!!result.issues?.length && (
                <details>
                  <summary>
                    Оставшиеся проблемы дат: {result.issues.length}
                  </summary>
                  <ul>
                    {result.issues.map((i, n) => (
                      <li key={n}>
                        {i.date} · {i.message}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </section>
      )}
      {preview ? (
        <ChangePreview
          preview={preview}
          busy={busy}
          onApply={apply}
          onCancel={() => {
            setPreview(null);
            setResult(null);
          }}
          title="Проверка бронирований и дежурств"
        />
      ) : (
        value && (
          <form
            className="dated-form"
            onSubmit={(e) => {
              e.preventDefault();
              void check();
            }}
          >
            <h3>
              {value.kind === "duty" ? "Дежурство" : "Бронирование кабинета"}
            </h3>
            <label className="field">
              <span>Название</span>
              <input
                required
                maxLength={160}
                value={value.name}
                onChange={(e) => setValue({ ...value, name: e.target.value })}
              />
            </label>
            <div className="dated-fields">
              <label className="field">
                <span>Дата события</span>
                <input
                  type="date"
                  required
                  min={s.settings.yearStart}
                  max={s.settings.yearEnd}
                  value={value.onDate}
                  onChange={(e) =>
                    setValue({ ...value, onDate: e.target.value })
                  }
                />
              </label>
              <label className="field">
                <span>Начало события</span>
                <input
                  type="time"
                  required
                  value={value.startTime}
                  onChange={(e) =>
                    setValue({ ...value, startTime: e.target.value })
                  }
                />
              </label>
              <label className="field">
                <span>Конец события</span>
                <input
                  type="time"
                  required
                  value={value.endTime}
                  onChange={(e) =>
                    setValue({ ...value, endTime: e.target.value })
                  }
                />
              </label>
            </div>
            {!all.some((x) => x.id === value.id) && (
              <div className="dated-fields">
                <label className="field">
                  <span>Всего повторений</span>
                  <input
                    type="number"
                    min={1}
                    max={53}
                    required
                    value={copies}
                    onChange={(e) => setCopies(Number(e.target.value))}
                  />
                </label>
                <label className="field">
                  <span>Повторять через недель</span>
                  <input
                    type="number"
                    min={1}
                    max={8}
                    required
                    value={step}
                    onChange={(e) => setStep(Number(e.target.value))}
                  />
                </label>
                <p className="muted">
                  Создаются отдельные записи. Проверяются все повторения; выход
                  за учебный год блокирует весь пакет.
                </p>
              </div>
            )}
            <div className="dated-fields">
              <label className="field">
                <span>Место / участок</span>
                <input
                  maxLength={160}
                  placeholder="Например, коридор 2 этажа"
                  value={value.location}
                  onChange={(e) =>
                    setValue({ ...value, location: e.target.value })
                  }
                />
              </label>
              <label className="field">
                <span>Здание места</span>
                <select
                  value={value.buildingId || ""}
                  onChange={(e) =>
                    setValue({ ...value, buildingId: e.target.value || null })
                  }
                >
                  <option value="">По кабинетам / не задано</option>
                  {s.campus?.buildings.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Число учеников / посетителей</span>
                <input
                  type="number"
                  min={0}
                  max={10000}
                  placeholder="По составу; иначе неизвестно"
                  value={value.studentCount ?? ""}
                  onChange={(e) =>
                    setValue({
                      ...value,
                      studentCount:
                        e.target.value === "" ? null : Number(e.target.value),
                    })
                  }
                />
              </label>
            </div>
            <Multi
              label="Кабинеты события"
              items={s.rooms}
              values={value.roomIds}
              onChange={(v) => setValue({ ...value, roomIds: v })}
            />
            <div className="dated-fields">
              <label className="field">
                <span>Звонки для запретов учителей</span>
                <select
                  required={
                    value.kind === "duty" || value.teacherIds.length > 0
                  }
                  value={value.profileId || ""}
                  onChange={(e) =>
                    setValue({ ...value, profileId: e.target.value || null })
                  }
                >
                  <option value="">Выберите профиль</option>
                  {s.profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              {value.kind === "duty" && (
                <label className="field">
                  <span>Нужно дежурных</span>
                  <input
                    type="number"
                    required
                    min={1}
                    max={50}
                    value={value.requiredTeachers}
                    onChange={(e) =>
                      setValue({
                        ...value,
                        requiredTeachers: Number(e.target.value),
                      })
                    }
                  />
                </label>
              )}
            </div>
            {value.kind === "duty" && (
              <details>
                <summary>
                  Кандидаты на это дежурство ·{" "}
                  {value.eligibleTeacherIds.length || "все учителя"}
                </summary>
                <p>
                  Пустой список допускает всех учителей. Состав кандидатов
                  задаёт сотрудник.
                </p>
                <Multi
                  label="Допущенные сотрудники"
                  items={s.teachers}
                  values={value.eligibleTeacherIds}
                  onChange={(v) =>
                    setValue({
                      ...value,
                      eligibleTeacherIds: v,
                      teacherIds: value.teacherIds.filter(
                        (t) => !v.length || v.includes(t),
                      ),
                    })
                  }
                />
              </details>
            )}
            <div className="dated-fields">
              <Multi
                label="Назначенные сотрудники"
                items={s.teachers.filter(
                  (t) =>
                    !value.eligibleTeacherIds.length ||
                    value.eligibleTeacherIds.includes(t.id),
                )}
                values={value.teacherIds}
                onChange={(v) => setValue({ ...value, teacherIds: v })}
              />
              <Multi
                label="Участвующие группы"
                items={s.groups.filter((g) => g.kind !== "staff")}
                values={value.groupIds}
                onChange={(v) => setValue({ ...value, groupIds: v })}
              />
            </div>
            <label className="field">
              <span>Примечание</span>
              <textarea
                rows={3}
                maxLength={4000}
                value={value.note}
                onChange={(e) => setValue({ ...value, note: e.target.value })}
              />
            </label>
            <label className="check-line">
              <input
                type="checkbox"
                checked={value.locked}
                onChange={(e) =>
                  setValue({ ...value, locked: e.target.checked })
                }
              />
              Закрепить запись и назначения
            </label>
            <p className="muted">
              Дежурство можно сохранить без полного состава сотрудников и
              распределить позже. Закреплённую запись нужно разблокировать перед
              изменением; генератор её сохраняет. Отсутствующее здание или
              численность остаются непроверенными.
            </p>
            <div className="modal-actions">
              <button type="button" disabled={busy} onClick={close}>
                Закрыть форму
              </button>
              <button className="primary" disabled={busy}>
                Проверить запись
              </button>
            </div>
          </form>
        )
      )}
      <div className="dated-fields">
        <label className="field">
          <span>С даты</span>
          <input
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              setSelected([]);
            }}
          />
        </label>
        <label className="field">
          <span>По дату</span>
          <input
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setSelected([]);
            }}
          />
        </label>
        <label className="field">
          <span>Поиск события</span>
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected([]);
            }}
            placeholder="Название, кабинет, сотрудник…"
          />
        </label>
      </div>
      {tab === "duty" && (
        <section className="dated-issues">
          <h3>Распределение дежурных</h3>
          <p>
            Поиск сохраняет время и места, учитывает уроки, замены,
            бронирования, отсутствие, запреты, обязательный обед и переходы.
            Сначала выравнивает суммарные минуты за диапазон выбранных дат,
            затем предпочитает прежние назначения. Учебная квалификация не
            определяется автоматически.
          </p>
          <div className="dated-fields">
            <label className="field">
              <span>Лимит поиска, секунд</span>
              <input
                type="number"
                min={1}
                max={30}
                value={seconds}
                onChange={(e) => setSeconds(Number(e.target.value))}
              />
            </label>
            <label className="field">
              <span>Максимум минут на сотрудника за выбранный период</span>
              <input
                type="number"
                min={0}
                max={10000}
                value={maxMinutes}
                onChange={(e) => setMaxMinutes(Number(e.target.value))}
              />
              <small>
                0 — без предела. Уже назначенные дежурства входят в сумму; поиск
                не добавляет минуты сотруднику, уже превысившему предел.
              </small>
            </label>
          </div>
          <button
            className="primary"
            disabled={
              busy ||
              !chosen.length ||
              chosen.length > 200 ||
              chosen.some((x) => x.kind !== "duty")
            }
            onClick={allocate}
          >
            Распределить выбранные дежурства ({chosen.length})
          </button>
        </section>
      )}
      <div className="dated-toolbar">
        <label className="check-line">
          <input
            type="checkbox"
            disabled={!visible.length || busy}
            checked={
              !!visible.length && visible.every((x) => selected.includes(x.id))
            }
            onChange={(e) =>
              setSelected(e.target.checked ? visible.map((x) => x.id) : [])
            }
          />
          Выбрать все найденные ({visible.length})
        </label>
        <button
          disabled={busy || !selected.length || selected.length > 500}
          onClick={() => {
            setValue(null);
            void check(selected);
          }}
        >
          Удалить выбранные ({selected.length})
        </button>
      </div>
      {!visible.length && (
        <p className="empty-state">По выбранным условиям записей нет.</p>
      )}
      <div className="commitment-list">
        {visible.slice(0, limit).map((x) => (
          <article key={x.id}>
            <label className="check-line">
              <input
                aria-label={`Выбрать ${x.name} ${x.onDate}`}
                type="checkbox"
                checked={selected.includes(x.id)}
                disabled={busy}
                onChange={(e) =>
                  setSelected(
                    e.target.checked
                      ? [...selected, x.id]
                      : selected.filter((id) => id !== x.id),
                  )
                }
              />
            </label>
            <div>
              <strong>
                {x.name}
                {x.locked ? " · закреплено" : ""}
              </strong>
              <p>
                {x.onDate} · {x.startTime}–{x.endTime} ·{" "}
                {x.location ||
                  x.roomIds.map((id) => named(s.rooms, id)).join(", ")}
              </p>
              <p>
                {x.teacherIds.map((id) => named(s.teachers, id)).join(", ") ||
                  "Сотрудники не назначены"}
                {x.kind === "duty" &&
                  ` · ${x.teacherIds.length} из ${x.requiredTeachers}`}
              </p>
              {!!x.groupIds.length && (
                <small>
                  {x.groupIds.map((id) => named(s.groups, id)).join(", ")}
                </small>
              )}
            </div>
            <div className="dated-actions">
              <button
                disabled={busy}
                onClick={() => {
                  close();
                  setCopies(1);
                  setValue(x);
                }}
              >
                Изменить запись
              </button>
              <button onClick={() => onDate(x.onDate)}>Открыть день</button>
            </div>
          </article>
        ))}
      </div>
      {visible.length > limit && (
        <button onClick={() => setLimit(limit + 60)}>Показать ещё</button>
      )}
    </div>
  );
}

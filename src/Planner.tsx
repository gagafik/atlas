import { useEffect, useMemo, useState, useRef } from "react";
import { teacherNames, roomNames, teacherIds, roomIds } from "./utils";
import type { FormEvent } from "react";
import { ArrowLeftRight, Copy, LockKeyhole, Redo2, Undo2 } from "lucide-react";
import type { State, Placement } from "./types";
import { named, placementMatches, lessonTime } from "./utils";
import { api } from "./api";
import ChangePreview from "./ChangePreview";
import type { Preview } from "./ChangePreview";

type Slot = {
  day: number;
  period: number;
  available: boolean;
  start: string;
  end: string;
  reasons: string[];
};
type Variant = { id: string; name: string; created: string; revision: number };

export default function Planner({
  state: s,
  workspace,
  onState,
  onOpen,
}: {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
  onOpen: (p: Placement) => void;
}) {
  const [view, setView] = useState("class"),
    [day, setDay] = useState(0),
    [query, setQuery] = useState("");
  const [ids, setIds] = useState<string[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null),
    [slots, setSlots] = useState<Slot[]>([]);
  const [completeHints, setCompleteHints] = useState(true),
    [linked, setLinked] = useState(true);
  const [variants, setVariants] = useState<Variant[]>([]),
    [history, setHistory] = useState({ undo: 0, redo: 0 });
  const [tab, setTab] = useState("grid"),
    [message, setMessage] = useState("");
  const [rowsLimit, setRowsLimit] = useState(20);
  const previewAnchor = useRef<HTMLDivElement>(null);
  const loads = useMemo(
    () => Object.fromEntries(s.workloads.map((w) => [w.id, w])),
    [s],
  );
  const chosen = s.placements.filter((p) => ids.includes(p.id));
  const chosenLoads = [...new Set(chosen.map((p) => p.workloadId))];
  const primary = chosen[0];
  const resources =
    view === "class" ? s.classes : view === "teacher" ? s.teachers : s.rooms;
  const rows = resources.filter((r) =>
    r.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  const periods = Math.max(0, ...s.profiles.map((p) => p.periods.length));

  async function refreshMeta() {
    const [v, h] = await Promise.all([
      api("variants", workspace),
      api("history", workspace),
    ]);
    setVariants(v);
    setHistory(h);
  }
  useEffect(() => {
    setPreview(null);
    setSlots([]);
    setIds([]);
    refreshMeta().catch((e) => setError(e.message));
  }, [s.revision, workspace]);
  useEffect(() => {
    setRowsLimit(20);
  }, [view, query]);
  useEffect(() => {
    if (preview)
      previewAnchor.current?.scrollIntoView({
        block: "start",
        behavior: "smooth",
      });
  }, [preview?.token]);
  async function run(task: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function select(id: string) {
    setIds((current) =>
      current.includes(id) ? current.filter((i) => i !== id) : [...current, id],
    );
    setSlots([]);
    setPreview(null);
  }
  async function stage(body: Record<string, unknown>) {
    await run(async () => {
      setPreview(
        await api("editor/preview", workspace, "POST", {
          revision: s.revision,
          ids,
          linked,
          ...body,
        }),
      );
    });
  }
  function bulkMove(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const data: Record<string, unknown> = {
      action: f.get("action"),
      increaseHours: f.has("increaseHours"),
    };
    for (const key of ["day", "period"])
      if (f.get(key) !== "") data[key] = Number(f.get(key));
    if (f.get("roomId") !== "")
      data.roomId = f.get("roomId") === "none" ? null : f.get("roomId");
    void stage(data);
  }
  function bulkLoads(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget),
      patch: Record<string, unknown> = {};
    for (const key of ["hours", "maxPerDay"])
      if (f.get(key) !== "") patch[key] = Number(f.get(key));
    if (f.get("teacherId") !== "")
      patch.teacherId =
        f.get("teacherId") === "none" ? null : f.get("teacherId");
    if (f.get("room") !== "") patch.roomIds = [f.get("room")];
    if (f.has("setBlock")) patch.block = String(f.get("block")).trim();
    void stage({ action: "workloads", ids: chosenLoads, patch });
  }
  function card(p: Placement) {
    const w = loads[p.workloadId],
      time = lessonTime(s, p);
    return (
      <button
        key={p.id}
        type="button"
        data-lesson-id={p.id}
        className={`planner-lesson ${ids.includes(p.id) ? "picked" : ""}`}
        aria-pressed={ids.includes(p.id)}
        disabled={busy}
        title={`${named(s.groups, w.groupId)} · ${time.start}–${time.end} · ${teacherNames(s, w)} · ${roomNames(s, w, p)}`}
        onClick={() => select(p.id)}
        onDoubleClick={() => onOpen(p)}
        style={{
          borderLeftColor: s.subjects.find(
            (subject) => subject.id === w.subjectId,
          )?.color,
        }}
      >
        <strong>
          {p.locked && <LockKeyhole size={10} />}{" "}
          {named(s.subjects, w.subjectId)}
        </strong>
        <small>
          {named(s.groups, w.groupId)} · {roomNames(s, w, p)}
        </small>
        <small>
          {time.start}–{time.end}
        </small>
      </button>
    );
  }

  return (
    <div className="planner">
      <div className="planner-heading">
        <div className="segmented">
          {[
            ["grid", "Сводная сетка"],
            ["versions", "Варианты и история"],
          ].map(([id, name]) => (
            <button
              key={id}
              className={tab === id ? "selected" : ""}
              onClick={() => setTab(id)}
            >
              {name}
            </button>
          ))}
        </div>
        <div className="toolbar-buttons">
          {(
            [
              ["undo", "Отменить", Undo2],
              ["redo", "Повторить", Redo2],
            ] as const
          ).map(([key, label, Icon]) => (
            <button
              key={key}
              disabled={busy || !history[key]}
              onClick={() =>
                run(async () => {
                  await onState(
                    await api(key, workspace, "POST", { revision: s.revision }),
                  );
                  setMessage(`${label}: готово`);
                })
              }
            >
              <Icon size={15} />
              {label} ({history[key]})
            </button>
          ))}
        </div>
      </div>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {message && (
        <p role="status" className="success-box">
          {message}
        </p>
      )}
      {busy && <p role="status">Проверяем и подготавливаем изменения…</p>}
      {tab === "versions" ? (
        <section className="planner-versions">
          <h3>Сохранённые варианты</h3>
          <p>
            Вариант содержит расписание, справочники, составы групп и параметры.
            Перед восстановлением показывается сравнение с текущей версией.
            Восстановление можно отменить.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              void run(async () => {
                await api("variants", workspace, "POST", {
                  revision: s.revision,
                  name: f.get("name"),
                });
                await refreshMeta();
                setMessage("Именованный вариант сохранён");
              });
            }}
          >
            <label>
              Название варианта
              <input
                name="name"
                required
                maxLength={160}
                placeholder="Например, до изменения нагрузки"
              />
            </label>
            <button disabled={busy} className="primary">
              Сохранить вариант
            </button>
          </form>
          {variants.length === 0 && (
            <p className="muted">Сохранённых вариантов пока нет.</p>
          )}
          {variants.map((v) => (
            <article key={v.id}>
              <div>
                <strong>{v.name}</strong>
                <small>
                  {new Date(v.created).toLocaleString("ru")} · версия{" "}
                  {v.revision}
                </small>
              </div>
              <button
                disabled={busy}
                onClick={() =>
                  run(async () =>
                    setPreview(
                      await api(`variants/${v.id}/preview`, workspace, "POST", {
                        revision: s.revision,
                      }),
                    ),
                  )
                }
              >
                Сравнить и восстановить
              </button>
              <details>
                <summary>Удаление</summary>
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api(`variants/${v.id}`, workspace, "DELETE");
                      await refreshMeta();
                    })
                  }
                >
                  Удалить этот вариант
                </button>
              </details>
            </article>
          ))}
        </section>
      ) : (
        <>
          <div className="planner-filters">
            <label>
              Строки
              <select value={view} onChange={(e) => setView(e.target.value)}>
                <option value="class">Классы</option>
                <option value="teacher">Учителя</option>
                <option value="room">Кабинеты</option>
              </select>
            </label>
            <label>
              День
              <select
                value={day}
                onChange={(e) => setDay(Number(e.target.value))}
              >
                <option value={-1}>Вся неделя</option>
                {s.settings.days.map((d, i) => (
                  <option key={i} value={i}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Поиск строк
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Название или имя"
              />
            </label>
          </div>
          <p className="muted">
            Нажмите на карточки для выбора нескольких уроков. Двойное нажатие
            открывает занятие. Номера уроков могут соответствовать разному
            времени: точные часы указаны на карточках.
          </p>
          <div className="planner-scroll">
            <table className="planner-grid">
              <thead>
                <tr>
                  <th>
                    {view === "class"
                      ? "Класс"
                      : view === "teacher"
                        ? "Учитель"
                        : "Кабинет"}
                  </th>
                  {s.settings.days.flatMap((d, di) =>
                    day !== -1 && day !== di
                      ? []
                      : Array.from({ length: periods }, (_, period) => (
                          <th key={`${di}:${period}`}>
                            <span>{d.slice(0, 2)}</span> {period}
                          </th>
                        )),
                  )}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, rowsLimit).map((row) => (
                  <tr key={row.id}>
                    <th>{row.name}</th>
                    {s.settings.days.flatMap((_, di) =>
                      day !== -1 && day !== di
                        ? []
                        : Array.from({ length: periods }, (_, period) => (
                            <td key={`${di}:${period}`}>
                              {s.placements
                                .filter(
                                  (p) =>
                                    p.day === di &&
                                    p.period === period &&
                                    placementMatches(
                                      s,
                                      loads[p.workloadId],
                                      p,
                                      view,
                                      row.id,
                                    ),
                                )
                                .map(card)}
                            </td>
                          )),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > rowsLimit && (
            <button onClick={() => setRowsLimit(rowsLimit + 20)}>
              Показать ещё строки ({rows.length - rowsLimit})
            </button>
          )}
          {!rows.length && <p>Строки не найдены.</p>}
          <section className="planner-selection">
            <header>
              <h3>Выбрано занятий: {ids.length}</h3>
              <button
                disabled={!ids.length || busy}
                onClick={() => {
                  setIds([]);
                  setSlots([]);
                  setPreview(null);
                }}
              >
                Снять выбор
              </button>
            </header>
            {chosen.length > 0 && (
              <>
                <label className="inline-check">
                  <input
                    type="checkbox"
                    checked={linked}
                    onChange={(e) => {
                      setLinked(e.target.checked);
                      setSlots([]);
                      setPreview(null);
                    }}
                  />{" "}
                  Включать связанные уроки синхронного блока
                </label>
                <p className="muted">
                  Связанные уроки определяются по блоку и исходному времени.
                  Кабинеты при обмене остаются у своих занятий.
                </p>
                <div className="planner-actions">
                  <button
                    disabled={busy || chosen.length !== 2}
                    onClick={() => stage({ action: "swap" })}
                  >
                    <ArrowLeftRight size={15} />
                    Обменять время
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => stage({ action: "lock" })}
                  >
                    Закрепить
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => stage({ action: "unlock" })}
                  >
                    Открепить
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => stage({ action: "unplace" })}
                  >
                    Снять с сетки
                  </button>
                  <button
                    disabled={busy || chosen.length !== 1}
                    onClick={() => onOpen(primary)}
                  >
                    Открыть карточку
                  </button>
                  <button
                    disabled={busy || chosen.length !== 1 || primary.locked}
                    onClick={() =>
                      run(async () => {
                        const result = await api(
                          "editor/slots",
                          workspace,
                          "POST",
                          {
                            revision: s.revision,
                            placementId: primary.id,
                            linked,
                          },
                        );
                        setSlots(result.slots);
                        setCompleteHints(result.completeChecks);
                      })
                    }
                  >
                    Показать доступные места
                  </button>
                </div>
                <form
                  className="planner-bulk"
                  onSubmit={bulkMove}
                  onChange={() => setPreview(null)}
                >
                  <label>
                    Действие
                    <select name="action">
                      <option value="move">Перенести</option>
                      <option value="copy">Копировать</option>
                    </select>
                  </label>
                  <label>
                    День
                    <select name="day">
                      <option value="">Без изменения</option>
                      {s.settings.days.map((d, i) => (
                        <option key={i} value={i}>
                          {d}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Номер урока
                    <select name="period">
                      <option value="">Без изменения</option>
                      {Array.from({ length: periods }, (_, i) => (
                        <option key={i} value={i}>
                          {i}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Кабинет
                    <select name="roomId">
                      <option value="">Без изменения</option>
                      <option value="none">Без кабинета</option>
                      {s.rooms.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="inline-check">
                    <input type="checkbox" name="increaseHours" /> При
                    копировании увеличить недельные часы
                  </label>
                  <button disabled={busy} className="primary">
                    <Copy size={14} />
                    Проверить изменение
                  </button>
                </form>
                <details className="planner-bulk-loads">
                  <summary>
                    Массовая правка нагрузки и синхронных блоков (
                    {chosenLoads.length})
                  </summary>
                  <p>
                    Изменит выбранные нагрузки целиком, включая их занятия в
                    другие дни. Чтобы создать синхронный блок, задайте общее
                    название; недельные часы и профиль звонков должны совпадать.
                  </p>
                  <form
                    className="planner-bulk"
                    onSubmit={bulkLoads}
                    onChange={() => setPreview(null)}
                  >
                    <label>
                      Учитель
                      <select name="teacherId">
                        <option value="">Без изменения</option>
                        <option value="none">Не задан</option>
                        {s.teachers.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Часов в неделю
                      <input
                        name="hours"
                        type="number"
                        min={1}
                        max={100}
                        placeholder="Без изменения"
                      />
                    </label>
                    <label>
                      Максимум в день
                      <input
                        name="maxPerDay"
                        type="number"
                        min={1}
                        max={12}
                        placeholder="Без изменения"
                      />
                    </label>
                    <label>
                      Допустимый кабинет
                      <select name="room">
                        <option value="">Без изменения</option>
                        {s.rooms.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="inline-check">
                      <input type="checkbox" name="setBlock" /> Изменить
                      синхронный блок
                    </label>
                    <label>
                      Название блока
                      <input
                        name="block"
                        placeholder="Пустое значение снимает блок"
                      />
                    </label>
                    <button disabled={busy} className="primary">
                      Проверить нагрузку
                    </button>
                  </form>
                </details>
              </>
            )}
          </section>
          {slots.length > 0 && (
            <section className="planner-hints">
              <h3>Места для выбранного занятия</h3>
              <p>
                Зелёный — новых нарушений не найдено. Нажмите для предпросмотра
                переноса.
                {!completeHints &&
                  " Проверка ограничена неполными исходными данными."}
              </p>
              <div className="planner-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Урок</th>
                      {s.settings.days.map((d) => (
                        <th key={d}>{d}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from(new Set(slots.map((slot) => slot.period))).map(
                      (period) => (
                        <tr key={period}>
                          <th>{period}</th>
                          {s.settings.days.map((_, d) => {
                            const slot = slots.find(
                              (x) => x.day === d && x.period === period,
                            )!;
                            return (
                              <td key={d}>
                                <button
                                  disabled={busy}
                                  className={
                                    slot.available
                                      ? "slot-free"
                                      : "slot-blocked"
                                  }
                                  title={
                                    slot.reasons.join("\n") ||
                                    "Новых нарушений не найдено"
                                  }
                                  onClick={() =>
                                    stage({ action: "move", day: d, period })
                                  }
                                >
                                  {slot.start}–{slot.end}
                                  <small>
                                    {slot.available
                                      ? "Доступно"
                                      : "Есть ограничения"}
                                  </small>
                                </button>
                              </td>
                            );
                          })}
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {primary && (
            <section className="planner-linked">
              <h3>Связанные расписания · {s.settings.days[primary.day]}</h3>
              <div>
                {[
                  ...teacherIds(loads[primary.workloadId]).map((id) => [
                    "teacher",
                    id,
                    named(s.teachers, id),
                  ]),
                  ...roomIds(loads[primary.workloadId], primary).map((id) => [
                    "room",
                    id,
                    named(s.rooms, id),
                  ]),
                ]
                  .filter(([, id]) => id)
                  .map(([mode, id, name]) => (
                    <article key={`${mode}:${id}`}>
                      <h4>
                        {mode === "teacher" ? "Учитель" : "Кабинет"}: {name}
                      </h4>
                      {s.placements
                        .filter(
                          (p) =>
                            p.day === primary.day &&
                            placementMatches(
                              s,
                              loads[p.workloadId],
                              p,
                              mode!,
                              id!,
                            ),
                        )
                        .sort((a, b) =>
                          lessonTime(s, a).start.localeCompare(
                            lessonTime(s, b).start,
                          ),
                        )
                        .map(card)}
                    </article>
                  ))}
              </div>
            </section>
          )}
        </>
      )}
      <div ref={previewAnchor} />
      {preview && (
        <ChangePreview
          key={preview.token}
          preview={preview}
          busy={busy}
          onCancel={() => setPreview(null)}
          onApply={() =>
            run(async () => {
              const saved = await api("editor/apply", workspace, "POST", {
                revision: s.revision,
                token: preview.token,
              });
              await onState(saved);
              setPreview(null);
              setMessage("Изменения применены. Их можно отменить.");
            })
          }
        />
      )}
    </div>
  );
}

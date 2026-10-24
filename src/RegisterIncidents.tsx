import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { displayTime } from "./meetings";
import { Fact, HistoryList, Picker, TimeInput } from "./RegisterParts";
import {
  incidentKinds,
  noticeMethods,
  severities,
  statuses,
  type Ctx,
  type History,
  type Incident,
  type IncidentData,
  type IncidentRow,
  type Notice,
} from "./registerTypes";

type Draft = {
  id: string;
  version: number;
  data: IncidentData;
  kept: Record<string, string>;
};
const blank = (): IncidentData => ({
  kind: "accident",
  severity: "minor",
  occurredAt: new Date().toISOString(),
  place: "",
  roomId: "",
  studentIds: [],
  staffIds: [],
  otherPersons: "",
  description: "",
  firstAid: "",
  parentNotices: [],
  followUp: "",
  familySummary: "",
});

export default function RegisterIncidents({ ctx }: { ctx: Ctx }) {
  const { options, workspace, run, mutate } = ctx;
  const [status, setStatus] = useState(""),
    [year, setYear] = useState(""),
    [yearText, setYearText] = useState(""),
    [query, setQuery] = useState(""),
    [q, setQ] = useState("");
  const [items, setItems] = useState<IncidentRow[] | null>(null),
    [next, setNext] = useState<number | null>(null),
    [selected, setSelected] = useState<Incident | null>(null),
    [draft, setDraft] = useState<Draft | null>(null),
    [history, setHistory] = useState<History | null>(null);
  const sequence = useRef(0);
  const students = options.students ?? [];
  const path = (before?: number) =>
    `registers/incidents?status=${status}&year=${year}&q=${encodeURIComponent(q)}${before ? "&before=" + before : ""}`;
  async function load() {
    const n = ++sequence.current;
    const r = await api(path(), workspace);
    if (n !== sequence.current) return;
    setItems(r.items);
    setNext(r.nextBefore);
  }
  useEffect(() => {
    void run(load);
    return () => {
      ++sequence.current;
    };
  }, [workspace, status, year, q]);
  const leave = () =>
    !draft || window.confirm("Отбросить несохранённую запись?");
  function show(r: Incident) {
    ctx.dirty(false);
    setDraft(null);
    setHistory(null);
    setSelected(r);
  }
  async function open(id: string) {
    if (!leave()) return;
    show(await api("registers/incidents/" + id, workspace));
  }
  function startEdit(r: Incident) {
    const kept: Record<string, string> = {};
    for (const p of [...r.students, ...r.staff]) kept[p.id] = p.name;
    setDraft({
      id: r.id,
      version: r.version,
      data: structuredClone(r.data),
      kept,
    });
    setHistory(null);
    ctx.dirty(false);
  }
  const edit = (patch: Partial<IncidentData>) => {
    if (draft) {
      setDraft({ ...draft, data: { ...draft.data, ...patch } });
      ctx.dirty(true);
    }
  };
  const setNotice = (i: number, patch: Partial<Notice>) =>
    draft &&
    edit({
      parentNotices: draft.data.parentNotices.map((n, k) =>
        k === i ? { ...n, ...patch } : n,
      ),
    });
  async function save() {
    if (!draft) return;
    const reason = draft.id ? window.prompt("Причина изменения записи") : "";
    if (reason === null) return;
    if (draft.id && reason.trim().length < 3)
      throw new Error("Укажите причину изменения.");
    const r: Incident = await mutate(
      "incidents" + (draft.id ? "/" + draft.id : ""),
      { version: draft.version, data: draft.data, reason: reason.trim() },
      draft.id ? "PUT" : "POST",
    );
    await load();
    show(r);
    ctx.say(
      draft.id
        ? "Запись изменена."
        : `Запись зарегистрирована под номером ${r.number}.`,
    );
  }
  const prompts: Record<string, string> = {
    close: "Как завершена работа по записи? Это пояснение попадёт в историю.",
    reopen: "Почему запись нужно открыть повторно?",
    publish_summary: "Почему сводку можно показать семьям?",
    withdraw_summary: "Почему сводку нужно снять с публикации?",
  };
  async function act(action: string) {
    if (!selected) return;
    const reason = window.prompt(prompts[action]);
    if (reason === null) return;
    if (reason.trim().length < 3)
      throw new Error("Укажите пояснение не короче трёх символов.");
    const r: Incident = await mutate(`incidents/${selected.id}/action`, {
      version: selected.version,
      action,
      reason: reason.trim(),
    });
    await load();
    show(r);
    ctx.say("Действие записано в историю.");
  }
  async function showHistory(more = false) {
    if (!selected) return;
    const r: History = await api(
      `registers/history/incidents/${selected.id}${more && history?.nextBefore ? "?before=" + history.nextBefore : ""}`,
      workspace,
    );
    setHistory(
      more && history ? { ...r, items: [...history.items, ...r.items] } : r,
    );
  }
  const names = (id: string) =>
    draft
      ? students.find((s) => s.id === id)?.name || draft.kept[id] || "Ученик"
      : "";
  const rooms = options.rooms ?? [];
  return (
    <section aria-label="Журнал происшествий">
      <form
        className="rg-controls rg-noprint"
        onSubmit={(e) => {
          e.preventDefault();
          if (leave()) setQ(query.trim());
        }}
      >
        <label>
          Состояние
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Все</option>
            <option value="open">{statuses.open}</option>
            <option value="closed">{statuses.closed}</option>
          </select>
        </label>
        <label>
          Год
          <input
            inputMode="numeric"
            maxLength={4}
            placeholder="Все годы"
            value={yearText}
            onChange={(e) => {
              const v = e.target.value;
              if (!/^\d{0,4}$/.test(v)) return;
              setYearText(v);
              if (v.length === 4 || !v) setYear(v);
            }}
          />
        </label>
        <label>
          Найти
          <input
            value={query}
            maxLength={200}
            placeholder="Номер, место или описание"
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <button>Найти</button>
        <button
          type="button"
          onClick={() => {
            if (!leave()) return;
            ctx.dirty(false);
            setSelected(null);
            setHistory(null);
            setDraft({ id: "", version: 0, data: blank(), kept: {} });
          }}
        >
          Зарегистрировать происшествие
        </button>
      </form>
      <div className="rg-split">
        <div className="rg-list rg-noprint">
          {items && !items.length && <p>Записей по этому фильтру нет.</p>}
          {items?.map((r) => (
            <button
              key={r.id}
              aria-current={selected?.id === r.id}
              onClick={() => void run(() => open(r.id))}
            >
              <strong>№ {r.number}</strong>
              <span>
                {incidentKinds[r.kind]} · {statuses[r.status]}
                {r.summaryPublished ? " · сводка опубликована" : ""}
              </span>
              <span>
                {displayTime(r.occurredAt)} · {r.place}
              </span>
              <span>Учеников: {r.studentCount}</span>
            </button>
          ))}
          {next && (
            <button
              onClick={() =>
                void run(async () => {
                  const r = await api(path(next), workspace);
                  setItems([...(items ?? []), ...r.items]);
                  setNext(r.nextBefore);
                })
              }
            >
              Ещё записи
            </button>
          )}
        </div>
        <div className="rg-detail">
          {draft && (
            <form
              className="rg-form"
              onSubmit={(e) => {
                e.preventDefault();
                void run(save);
              }}
            >
              <h3>
                {draft.id ? "Изменить запись" : "Новая запись о происшествии"}
              </h3>
              <div className="rg-grid">
                <label>
                  Вид
                  <select
                    value={draft.data.kind}
                    onChange={(e) => edit({ kind: e.target.value })}
                  >
                    {Object.entries(incidentKinds).map(([v, n]) => (
                      <option key={v} value={v}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Тяжесть
                  <select
                    value={draft.data.severity}
                    onChange={(e) => edit({ severity: e.target.value })}
                  >
                    {Object.entries(severities).map(([v, n]) => (
                      <option key={v} value={v}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
                <TimeInput
                  label="Когда произошло"
                  required
                  value={draft.data.occurredAt}
                  onChange={(iso) => edit({ occurredAt: iso })}
                />
                <label>
                  Где произошло
                  <input
                    required
                    minLength={2}
                    maxLength={200}
                    value={draft.data.place}
                    placeholder="Например, спортзал"
                    onChange={(e) => edit({ place: e.target.value })}
                  />
                </label>
                <label>
                  Кабинет из расписания
                  <select
                    value={draft.data.roomId}
                    onChange={(e) => edit({ roomId: e.target.value })}
                  >
                    <option value="">Не кабинет</option>
                    {rooms.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                      </option>
                    ))}
                    {draft.data.roomId &&
                      !rooms.some((r) => r.id === draft.data.roomId) && (
                        <option value={draft.data.roomId}>
                          {selected?.roomName || "Кабинет из записи"}
                        </option>
                      )}
                  </select>
                </label>
              </div>
              <Picker
                label="Ученики"
                groupLabel="Класс"
                choices={students.map((s) => ({
                  id: s.id,
                  name: s.name,
                  group: s.className,
                }))}
                chosen={draft.data.studentIds}
                kept={draft.kept}
                onChange={(ids) =>
                  edit({
                    studentIds: ids,
                    parentNotices: draft.data.parentNotices.filter((n) =>
                      ids.includes(n.studentId),
                    ),
                  })
                }
              />
              <Picker
                label="Сотрудники"
                choices={(options.staff ?? []).map((s) => ({
                  id: s.id,
                  name: s.name,
                }))}
                chosen={draft.data.staffIds}
                kept={draft.kept}
                onChange={(ids) => edit({ staffIds: ids })}
              />
              <label>
                Другие лица
                <input
                  maxLength={500}
                  value={draft.data.otherPersons}
                  placeholder="Например, посторонний взрослый"
                  onChange={(e) => edit({ otherPersons: e.target.value })}
                />
              </label>
              <label>
                Что произошло
                <textarea
                  required
                  minLength={10}
                  maxLength={4000}
                  value={draft.data.description}
                  onChange={(e) => edit({ description: e.target.value })}
                />
              </label>
              <label>
                Первая помощь и действия
                <textarea
                  maxLength={2000}
                  value={draft.data.firstAid}
                  onChange={(e) => edit({ firstAid: e.target.value })}
                />
              </label>
              <fieldset className="rg-picker">
                <legend>Сообщения родителям</legend>
                {!draft.data.studentIds.length && (
                  <p>Добавьте учеников, чтобы записать сообщения родителям.</p>
                )}
                {draft.data.parentNotices.map((n, i) => (
                  <div key={i} className="rg-notice">
                    <label>
                      Ученик
                      <select
                        value={n.studentId}
                        onChange={(e) =>
                          setNotice(i, { studentId: e.target.value })
                        }
                      >
                        {draft.data.studentIds.map((id) => (
                          <option key={id} value={id}>
                            {names(id)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Кто сообщил
                      <input
                        required
                        minLength={2}
                        maxLength={160}
                        value={n.by}
                        onChange={(e) => setNotice(i, { by: e.target.value })}
                      />
                    </label>
                    <TimeInput
                      label="Когда"
                      required
                      value={n.at}
                      onChange={(iso) => setNotice(i, { at: iso })}
                    />
                    <label>
                      Способ
                      <select
                        value={n.method}
                        onChange={(e) =>
                          setNotice(i, { method: e.target.value })
                        }
                      >
                        {Object.entries(noticeMethods).map(([v, name]) => (
                          <option key={v} value={v}>
                            {name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Примечание
                      <input
                        maxLength={300}
                        value={n.note}
                        onChange={(e) => setNotice(i, { note: e.target.value })}
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() =>
                        edit({
                          parentNotices: draft.data.parentNotices.filter(
                            (_, k) => k !== i,
                          ),
                        })
                      }
                    >
                      Убрать
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  disabled={!draft.data.studentIds.length}
                  onClick={() =>
                    edit({
                      parentNotices: [
                        ...draft.data.parentNotices,
                        {
                          studentId: draft.data.studentIds[0],
                          by: ctx.user.name,
                          at: new Date().toISOString(),
                          method: "phone",
                          note: "",
                        },
                      ],
                    })
                  }
                >
                  Добавить сообщение
                </button>
              </fieldset>
              <label>
                Дальнейшие меры
                <textarea
                  maxLength={4000}
                  value={draft.data.followUp}
                  onChange={(e) => edit({ followUp: e.target.value })}
                />
              </label>
              <label>
                Краткая сводка для семей
                <textarea
                  maxLength={1000}
                  value={draft.data.familySummary}
                  onChange={(e) => edit({ familySummary: e.target.value })}
                />
                <small>
                  Родители увидят этот текст только после публикации
                  администрацией и только если школа включила такие сводки. Не
                  указывайте других учеников и медицинские подробности.
                </small>
              </label>
              <div className="rg-actions">
                <button>
                  {draft.id ? "Сохранить изменения" : "Зарегистрировать"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (!leave()) return;
                    ctx.dirty(false);
                    setDraft(null);
                  }}
                >
                  Отмена
                </button>
              </div>
            </form>
          )}
          {!draft && selected && (
            <>
              <article className="rg-print rg-record">
                <h3>
                  Запись о происшествии № {selected.number}
                  <small> · {statuses[selected.status]}</small>
                </h3>
                <dl className="rg-facts">
                  <Fact label="Вид">{incidentKinds[selected.data.kind]}</Fact>
                  <Fact label="Тяжесть">
                    {severities[selected.data.severity]}
                  </Fact>
                  <Fact label="Когда">
                    {displayTime(selected.data.occurredAt)}
                  </Fact>
                  <Fact label="Где">
                    {selected.data.place}
                    {selected.roomName
                      ? ` (кабинет ${selected.roomName}${selected.roomCurrent ? "" : ", убран из расписания"})`
                      : ""}
                  </Fact>
                  <Fact label="Ученики">
                    {selected.students
                      .map(
                        (s) =>
                          `${s.name}${s.className ? `, ${s.className}` : ""}${s.current ? "" : " (больше не учится)"}`,
                      )
                      .join("\n")}
                  </Fact>
                  <Fact label="Сотрудники">
                    {selected.staff
                      .map(
                        (s) =>
                          `${s.name}${s.current ? "" : " (больше не работает)"}`,
                      )
                      .join("\n")}
                  </Fact>
                  <Fact label="Другие лица">{selected.data.otherPersons}</Fact>
                  <Fact label="Что произошло">{selected.data.description}</Fact>
                  <Fact label="Первая помощь и действия">
                    {selected.data.firstAid}
                  </Fact>
                  <Fact label="Сообщения родителям">
                    {selected.data.parentNotices
                      .map((n) => {
                        const pupil =
                          selected.students.find((s) => s.id === n.studentId)
                            ?.name || "Ученик";
                        return `${pupil}: ${n.by}, ${displayTime(n.at)}, ${noticeMethods[n.method]}${n.note ? ` — ${n.note}` : ""}`;
                      })
                      .join("\n")}
                  </Fact>
                  <Fact label="Дальнейшие меры">{selected.data.followUp}</Fact>
                  <Fact label="Сводка для семей">
                    {selected.data.familySummary
                      ? `${selected.data.familySummary}\n${selected.summaryPublished ? "Опубликована " + displayTime(selected.publishedAt) : "Не опубликована"}`
                      : ""}
                  </Fact>
                  <Fact label="Записал">{selected.author}</Fact>
                  {selected.closedAt && (
                    <Fact label="Закрыта">{`${displayTime(selected.closedAt)}, ${selected.closedBy}`}</Fact>
                  )}
                </dl>
                <p className="rg-sign">
                  Ответственный: ______________________ Дата: ______________
                </p>
              </article>
              <div className="rg-actions rg-noprint">
                {selected.status === "open" && (
                  <button onClick={() => startEdit(selected)}>Изменить</button>
                )}
                {selected.status === "open" && (
                  <button onClick={() => void run(() => act("close"))}>
                    Закрыть запись
                  </button>
                )}
                {selected.canReopen && (
                  <button onClick={() => void run(() => act("reopen"))}>
                    Открыть повторно
                  </button>
                )}
                {selected.canPublish && !selected.summaryPublished && (
                  <button
                    onClick={() => void run(() => act("publish_summary"))}
                  >
                    Опубликовать сводку
                  </button>
                )}
                {selected.canPublish && selected.summaryPublished && (
                  <button
                    onClick={() => void run(() => act("withdraw_summary"))}
                  >
                    Снять сводку
                  </button>
                )}
                <button onClick={() => void run(() => showHistory())}>
                  История
                </button>
                <button onClick={() => window.print()}>Печать записи</button>
              </div>
              {history && (
                <HistoryList
                  history={history}
                  onMore={() => void run(() => showHistory(true))}
                />
              )}
            </>
          )}
          {!draft && !selected && (
            <p className="rg-noprint">
              Выберите запись или зарегистрируйте новое происшествие.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

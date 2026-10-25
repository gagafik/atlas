import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { api } from "./api";
import { clockTime, displayTime, schoolInput, schoolISO } from "./meetings";
import { HistoryList } from "./RegisterParts";
import {
  allPages,
  documentTypes,
  exportHref,
  staySince,
  statuses,
  type Ctx,
  type History,
  type Visitor,
  type VisitorData,
} from "./registerTypes";

type List = {
  items: Visitor[];
  nextBefore: number | null;
  count: number;
  inside: number;
};
type Draft = {
  id: string;
  version: number;
  data: VisitorData;
  badge: string;
  arrivedAt: string;
  original: string;
};
const blank = (): Draft => ({
  id: "",
  version: 0,
  data: {
    name: "",
    purpose: "",
    hostId: "",
    hostText: "",
    documentType: "",
    note: "",
  },
  badge: "",
  arrivedAt: "",
  original: "",
});
const byArrival = (a: Visitor, b: Visitor) =>
  a.arrived.localeCompare(b.arrived);

export default function RegisterVisitors({ ctx }: { ctx: Ctx }) {
  const { options, workspace, run, mutate } = ctx;
  const [view, setView] = useState<"inside" | "day">("inside"),
    [day, setDay] = useState(options.today),
    [query, setQuery] = useState(""),
    [q, setQ] = useState("");
  const [list, setList] = useState<List | null>(null),
    [draft, setDraft] = useState<Draft | null>(null),
    [history, setHistory] = useState<{ id: string; data: History } | null>(
      null,
    ),
    [departAt, setDepartAt] = useState<Record<string, string>>({}),
    [now, setNow] = useState(Date.now());
  const sequence = useRef(0);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);
  const path = (before?: number, limit?: number) =>
    `registers/visitors?${view === "inside" ? "inside=true" : "day=" + day}&q=${encodeURIComponent(q)}${before ? "&before=" + before : ""}${limit ? "&limit=" + limit : ""}`;
  async function load() {
    const n = ++sequence.current;
    const r: List = await api(path(), workspace);
    if (n === sequence.current) setList(r);
  }
  useEffect(() => {
    setHistory(null);
    void run(load, true);
    return () => {
      ++sequence.current;
    };
  }, [workspace, view, day, q]);
  const edit = (patch: Partial<Draft>) => {
    if (draft) {
      setDraft({ ...draft, ...patch });
      ctx.dirty(true);
    }
  };
  const editData = (patch: Partial<VisitorData>) =>
    draft && edit({ data: { ...draft.data, ...patch } });
  function close() {
    if (draft && !window.confirm("Отбросить несохранённую запись?")) return;
    ctx.dirty(false);
    setDraft(null);
  }
  function open(v: Visitor) {
    ctx.dirty(false);
    setDraft({
      id: v.id,
      version: v.version,
      data: { ...v.data },
      badge: v.badge,
      arrivedAt: v.arrived,
      original: v.arrived,
    });
  }
  async function save() {
    if (!draft) return;
    const reason = draft.id ? window.prompt("Причина исправления записи") : "";
    if (reason === null) return;
    if (draft.id && reason.trim().length < 3)
      throw new Error("Укажите причину исправления.");
    // The arrival time is sent only when it was changed: old entries keep their exact time.
    const changed = draft.arrivedAt && draft.arrivedAt !== draft.original;
    await mutate(
      "visitors" + (draft.id ? "/" + draft.id : ""),
      {
        version: draft.version,
        reason: reason.trim(),
        data: {
          ...draft.data,
          badge: draft.badge,
          arrivedAt: changed ? draft.arrivedAt : undefined,
        },
      },
      draft.id ? "PUT" : "POST",
    );
    ctx.dirty(false);
    setDraft(null);
    await load();
    ctx.say(draft.id ? "Запись исправлена." : "Приход записан.");
  }
  async function depart(v: Visitor) {
    await mutate(`visitors/${v.id}/action`, {
      version: v.version,
      action: "depart",
      at: departAt[v.id] ? schoolISO(departAt[v.id]) : undefined,
    });
    await load();
    ctx.say("Уход отмечен.");
  }
  async function annul(v: Visitor) {
    const reason = window.prompt(
      "Почему запись нужно аннулировать? Она останется в журнале.",
    );
    if (reason === null) return;
    if (reason.trim().length < 3)
      throw new Error("Укажите причину аннулирования.");
    await mutate(`visitors/${v.id}/action`, {
      version: v.version,
      action: "void",
      reason: reason.trim(),
    });
    await load();
    ctx.say("Запись аннулирована.");
  }
  // The printout must hold the whole day: fetch the pages that are not on screen yet.
  async function printDay() {
    if (list?.nextBefore) {
      const full = await allPages(list, (b) => api(path(b, 500), workspace));
      flushSync(() => setList(full));
    }
    window.print();
  }
  const moreButton = list?.nextBefore ? (
    <button
      className="rg-noprint"
      onClick={() =>
        void run(async () => {
          const r: List = await api(path(list.nextBefore!), workspace);
          setList({ ...r, items: [...list.items, ...r.items] });
        })
      }
    >
      Ещё записи
    </button>
  ) : null;
  async function showHistory(id: string, more = false) {
    const r: History = await api(
      `registers/history/visitors/${id}${more && history?.data.nextBefore ? "?before=" + history.data.nextBefore : ""}`,
      workspace,
    );
    setHistory({
      id,
      data:
        more && history
          ? { ...r, items: [...history.data.items, ...r.items] }
          : r,
    });
  }
  const buttons = (v: Visitor, inside: boolean) => (
    <div className="rg-actions rg-noprint">
      {v.status === "inside" && (
        <button onClick={() => void run(() => depart(v))}>Ушёл</button>
      )}
      {v.status !== "voided" && (
        <button onClick={() => open(v)}>Исправить</button>
      )}
      {v.status !== "voided" && (
        <button onClick={() => void run(() => annul(v))}>Аннулировать</button>
      )}
      <button onClick={() => void run(() => showHistory(v.id))}>История</button>
      {inside && v.overdue && (
        <label className="rg-inline">
          Время ухода
          <input
            type="datetime-local"
            value={departAt[v.id] ?? ""}
            onChange={(e) =>
              setDepartAt({ ...departAt, [v.id]: e.target.value })
            }
          />
        </label>
      )}
    </div>
  );
  const sorted = list ? [...list.items].sort(byArrival) : [];
  return (
    <section aria-label="Журнал посетителей">
      <div className="rg-controls rg-noprint">
        <nav className="rg-tabs" aria-label="Вид журнала посетителей">
          <button
            aria-pressed={view === "inside"}
            onClick={() => setView("inside")}
          >
            В здании сейчас{list ? ` (${list.inside})` : ""}
          </button>
          <button aria-pressed={view === "day"} onClick={() => setView("day")}>
            Журнал за день
          </button>
        </nav>
        <button
          disabled={Boolean(draft)}
          onClick={() => {
            ctx.dirty(false);
            setDraft(blank());
          }}
        >
          Новый посетитель
        </button>
      </div>
      {draft && (
        <form
          className="rg-form rg-noprint"
          onSubmit={(e) => {
            e.preventDefault();
            void run(save);
          }}
        >
          <h3>{draft.id ? "Исправить запись" : "Новый посетитель"}</h3>
          <div className="rg-grid">
            <label>
              Посетитель
              <input
                required
                minLength={2}
                maxLength={160}
                value={draft.data.name}
                placeholder="Фамилия и имя"
                onChange={(e) => editData({ name: e.target.value })}
              />
            </label>
            <label>
              Цель визита
              <input
                required
                minLength={2}
                maxLength={300}
                value={draft.data.purpose}
                onChange={(e) => editData({ purpose: e.target.value })}
              />
            </label>
            <label>
              К кому пришёл
              <select
                value={draft.data.hostId}
                onChange={(e) => editData({ hostId: e.target.value })}
              >
                <option value="">Сотрудник не выбран</option>
                {(options.staff ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Если сотрудника нет в списке
              <input
                maxLength={160}
                value={draft.data.hostText}
                placeholder="Например, бухгалтерия"
                onChange={(e) => editData({ hostText: e.target.value })}
              />
            </label>
            <label>
              Номер пропуска
              <input
                maxLength={20}
                pattern="[0-9A-Za-zА-Яа-яЁё\-]*"
                value={draft.badge}
                onChange={(e) => edit({ badge: e.target.value })}
              />
            </label>
            <label>
              Документ
              <select
                value={draft.data.documentType}
                onChange={(e) => editData({ documentType: e.target.value })}
              >
                {Object.entries(documentTypes).map(([v, n]) => (
                  <option key={v} value={v}>
                    {n}
                  </option>
                ))}
              </select>
              <small>Номер документа не записывается: только его тип.</small>
            </label>
            <label>
              Время прихода, если не сейчас
              <input
                type="datetime-local"
                max={schoolInput(options.now)}
                value={draft.arrivedAt ? schoolInput(draft.arrivedAt) : ""}
                onChange={(e) => {
                  try {
                    edit({
                      arrivedAt: e.target.value
                        ? schoolISO(e.target.value)
                        : "",
                    });
                  } catch {
                    // Incomplete date: wait for the whole value.
                  }
                }}
              />
            </label>
            <label>
              Примечание
              <input
                maxLength={500}
                value={draft.data.note}
                onChange={(e) => editData({ note: e.target.value })}
              />
            </label>
          </div>
          <p>Не вводите номера документов и телефонов.</p>
          <div className="rg-actions">
            <button>
              {draft.id ? "Сохранить исправление" : "Записать приход"}
            </button>
            <button type="button" onClick={close}>
              Отмена
            </button>
          </div>
        </form>
      )}
      {view === "day" && (
        <div className="rg-controls rg-noprint">
          <label>
            Дата
            <input
              type="date"
              value={day}
              max={options.today}
              onChange={(e) => e.target.value && setDay(e.target.value)}
            />
          </label>
          <a
            className="rg-link"
            href={exportHref("visitors/export", workspace, { day })}
            download
          >
            Скачать CSV за день
          </a>
          <button onClick={() => void run(printDay)}>Печать</button>
        </div>
      )}
      <form
        className="rg-controls rg-noprint"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(query.trim());
        }}
      >
        <label>
          Найти
          <input
            value={query}
            maxLength={200}
            placeholder="Имя, цель, пропуск, сотрудник"
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <button>Найти</button>
      </form>
      {list && view === "inside" && (
        <>
          <p role="status" className="rg-noprint">
            Сейчас в здании: {list.inside}
            {q && ` · найдено: ${list.count}`}
            {list.nextBefore && ` · показано: ${sorted.length}`}
          </p>
          {!sorted.length && <p>В здании посетителей нет.</p>}
          <ul className="rg-cards">
            {sorted.map((v) => (
              <li
                key={v.id}
                className={"rg-card" + (v.overdue ? " rg-warn" : "")}
              >
                <strong>{v.data.name}</strong>
                <span>
                  Пропуск: {v.badge || "—"} · к кому:{" "}
                  {v.hostName || v.data.hostText}
                </span>
                <span>Цель: {v.data.purpose}</span>
                <span>
                  Пришёл {displayTime(v.arrived)} · в здании{" "}
                  {staySince(v.arrived, now)}
                </span>
                {v.overdue && (
                  <strong className="rg-note">Уход не отмечен</strong>
                )}
                {buttons(v, true)}
              </li>
            ))}
          </ul>
          {moreButton}
        </>
      )}
      {list && view === "day" && (
        <div className="rg-print">
          <h3>Журнал посетителей за {day}</h3>
          <p>
            Записей: {sorted.length}
            {list.nextBefore &&
              " (показаны не все: нажмите «Печать» или «Ещё записи»)"}
          </p>
          {!sorted.length && <p>За этот день записей нет.</p>}
          {sorted.length > 0 && (
            <div className="rg-scroll">
              <table className="rg-table">
                <thead>
                  <tr>
                    <th>Пришёл</th>
                    <th>Ушёл</th>
                    <th>Посетитель</th>
                    <th>Цель визита</th>
                    <th>К кому</th>
                    <th>Пропуск</th>
                    <th>Документ</th>
                    <th>Состояние</th>
                    <th className="rg-noprint">Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((v) => (
                    <tr
                      key={v.id}
                      className={
                        v.status === "voided"
                          ? "rg-void"
                          : v.overdue
                            ? "rg-warn"
                            : ""
                      }
                    >
                      <td data-label="Пришёл">{clockTime(v.arrived)}</td>
                      <td data-label="Ушёл">
                        {v.departed ? clockTime(v.departed) : "—"}
                      </td>
                      <td data-label="Посетитель">{v.data.name}</td>
                      <td data-label="Цель визита">{v.data.purpose}</td>
                      <td data-label="К кому">
                        {v.hostName || v.data.hostText}
                      </td>
                      <td data-label="Пропуск">{v.badge || "—"}</td>
                      <td data-label="Документ">
                        {documentTypes[v.data.documentType] || "—"}
                      </td>
                      <td data-label="Состояние">
                        {statuses[v.status]}
                        {v.overdue && " · уход не отмечен"}
                      </td>
                      <td data-label="Действия" className="rg-noprint">
                        {buttons(v, false)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {moreButton}
        </div>
      )}
      {history && (
        <HistoryList
          history={history.data}
          onMore={() => void run(() => showHistory(history.id, true))}
        />
      )}
    </section>
  );
}

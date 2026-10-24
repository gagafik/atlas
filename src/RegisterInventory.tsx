import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Fact, HistoryList } from "./RegisterParts";
import {
  conditions,
  exportHref,
  readBase64,
  statuses,
  type Ctx,
  type History,
  type ImportPreview,
  type Item,
  type ItemData,
  type ItemRow,
  type Move,
  type Place,
} from "./registerTypes";

type Draft = { id: string; version: number; data: ItemData };
type MoveForm = {
  roomId: string;
  location: string;
  movedOn: string;
  reason: string;
};
type OffForm = { on: string; reason: string };
const blank = (): ItemData => ({
  number: "",
  title: "",
  category: "",
  roomId: "",
  location: "",
  responsibleId: "",
  condition: "good",
  acquiredOn: null,
  serial: "",
  note: "",
});
const where = (p: Place) =>
  [p.roomName, p.location].filter(Boolean).join(", ") || "—";

export default function RegisterInventory({ ctx }: { ctx: Ctx }) {
  const { options, workspace, run, mutate } = ctx;
  const [view, setView] = useState<"list" | "import">("list");
  const [query, setQuery] = useState(""),
    [q, setQ] = useState(""),
    [roomId, setRoomId] = useState(""),
    [category, setCategory] = useState(""),
    [status, setStatus] = useState("active"),
    [responsibleId, setResponsibleId] = useState("");
  const [items, setItems] = useState<ItemRow[] | null>(null),
    [next, setNext] = useState<number | null>(null),
    [selected, setSelected] = useState<Item | null>(null),
    [draft, setDraft] = useState<Draft | null>(null),
    [move, setMove] = useState<MoveForm | null>(null),
    [off, setOff] = useState<OffForm | null>(null),
    [moves, setMoves] = useState<{
      items: Move[];
      nextBefore: number | null;
    } | null>(null),
    [history, setHistory] = useState<History | null>(null);
  const [file, setFile] = useState<{
      filename: string;
      content: string;
    } | null>(null),
    [preview, setPreview] = useState<ImportPreview | null>(null),
    [reason, setReason] = useState("");
  const sequence = useRef(0);
  const rooms = options.rooms ?? [],
    staff = options.staff ?? [];
  const filters = { status, q, roomId, category, responsibleId };
  const path = (before?: number) =>
    "registers/inventory?" +
    Object.entries(filters)
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
      .join("&") +
    (before ? "&before=" + before : "");
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
  }, [workspace, q, roomId, category, status, responsibleId]);
  const busyForm = Boolean(draft || move || off);
  const leave = () =>
    !busyForm || window.confirm("Отбросить несохранённое действие?");
  function show(r: Item | null) {
    ctx.dirty(false);
    setDraft(null);
    setMove(null);
    setOff(null);
    setMoves(null);
    setHistory(null);
    setSelected(r);
  }
  function startEdit() {
    if (!selected) return;
    const current = selected;
    show(current);
    setDraft({
      id: current.id,
      version: current.version,
      data: { ...current.data },
    });
  }
  async function open(id: string) {
    if (leave()) show(await api("registers/inventory/" + id, workspace));
  }
  const edit = (patch: Partial<ItemData>) => {
    if (draft) {
      setDraft({ ...draft, data: { ...draft.data, ...patch } });
      ctx.dirty(true);
    }
  };
  async function save() {
    if (!draft) return;
    const note = draft.id ? window.prompt("Причина изменения карточки") : "";
    if (note === null) return;
    if (draft.id && note.trim().length < 3)
      throw new Error("Укажите причину изменения.");
    const r: Item = await mutate(
      "inventory" + (draft.id ? "/" + draft.id : ""),
      {
        version: draft.version,
        data: { ...draft.data, acquiredOn: draft.data.acquiredOn || null },
        reason: note.trim(),
      },
      draft.id ? "PUT" : "POST",
    );
    await load();
    show(r);
    ctx.say(draft.id ? "Карточка сохранена." : "Предмет поставлен на учёт.");
  }
  async function relocate() {
    if (!selected || !move) return;
    const r: Item = await mutate(`inventory/${selected.id}/move`, {
      version: selected.version,
      roomId: move.roomId,
      location: move.location,
      movedOn: move.movedOn || undefined,
      reason: move.reason.trim(),
    });
    await load();
    show(r);
    ctx.say("Перемещение записано.");
  }
  async function writeOff() {
    if (!selected || !off) return;
    const r: Item = await mutate(`inventory/${selected.id}/action`, {
      version: selected.version,
      action: "write_off",
      reason: off.reason.trim(),
      on: off.on || undefined,
    });
    await load();
    show(r);
    ctx.say("Предмет списан. Запись осталась в реестре.");
  }
  async function restore() {
    if (!selected) return;
    const why = window.prompt("Почему предмет нужно вернуть на учёт?");
    if (why === null) return;
    if (why.trim().length < 3)
      throw new Error("Укажите причину восстановления.");
    const r: Item = await mutate(`inventory/${selected.id}/action`, {
      version: selected.version,
      action: "restore",
      reason: why.trim(),
    });
    await load();
    show(r);
    ctx.say("Предмет возвращён на учёт.");
  }
  async function showMoves(more = false) {
    if (!selected) return;
    const r = await api(
      `registers/inventory/${selected.id}/moves${more && moves?.nextBefore ? "?before=" + moves.nextBefore : ""}`,
      workspace,
    );
    setMoves(more && moves ? { ...r, items: [...moves.items, ...r.items] } : r);
  }
  async function showHistory(more = false) {
    if (!selected) return;
    const r: History = await api(
      `registers/history/inventory/${selected.id}${more && history?.nextBefore ? "?before=" + history.nextBefore : ""}`,
      workspace,
    );
    setHistory(
      more && history ? { ...r, items: [...history.items, ...r.items] } : r,
    );
  }
  async function pick(chosen: File | undefined) {
    setPreview(null);
    setFile(null);
    if (!chosen) return;
    const content = await readBase64(chosen);
    setFile({ filename: chosen.name, content });
    setPreview(
      await api("registers/inventory/import/preview", workspace, "POST", {
        filename: chosen.name,
        content,
      }),
    );
  }
  async function importAll() {
    if (!file || !preview) return;
    const r = await mutate("inventory/import", {
      ...file,
      previewHash: preview.previewHash,
      reason: reason.trim(),
    });
    setFile(null);
    setPreview(null);
    setReason("");
    ctx.dirty(false);
    setView("list");
    await load();
    ctx.say(`Загружено предметов: ${r.count}.`);
  }
  const room = rooms.find((r) => r.id === roomId);
  return (
    <section aria-label="Школьный инвентарь">
      <nav className="rg-tabs rg-noprint" aria-label="Вид инвентаря">
        <button aria-pressed={view === "list"} onClick={() => setView("list")}>
          Предметы
        </button>
        <button
          aria-pressed={view === "import"}
          onClick={() => setView("import")}
        >
          Импорт CSV/XLSX
        </button>
      </nav>
      {view === "import" && (
        <section className="rg-form rg-noprint" aria-label="Импорт инвентаря">
          <h3>Загрузить инвентарь из файла</h3>
          <p>
            Колонки: Инвентарный номер, Наименование, Категория — обязательные;
            Кабинет, Место, Ответственный, Состояние, Дата поступления, Серийный
            номер, Примечание — по желанию. Файл, выгруженный из этого раздела,
            загружается без правок. Не более 2000 предметов. Существующие
            предметы не меняются. Если в файле есть ошибки, не сохраняется
            ничего.
          </p>
          <label>
            Файл CSV или XLSX
            <input
              type="file"
              accept=".csv,.tsv,.txt,.xlsx"
              onChange={(e) => {
                const chosen = e.target.files?.[0];
                e.target.value = "";
                void run(() => pick(chosen));
              }}
            />
          </label>
          {preview && file && (
            <>
              <h4>Предпросмотр: {file.filename}</h4>
              <p role="status">
                Готово к загрузке: {preview.items.length} · ошибок:{" "}
                {preview.errors.length}
              </p>
              {preview.warnings.map((w) => (
                <p key={w} className="rg-note">
                  {w}
                </p>
              ))}
              {preview.errors.length > 0 && (
                <div className="rg-scroll">
                  <table className="rg-table" aria-label="Ошибки файла">
                    <thead>
                      <tr>
                        <th>Строка</th>
                        <th>Что исправить</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.errors.map((e, i) => (
                        <tr key={i} className="rg-warn">
                          <td data-label="Строка">{e.line}</td>
                          <td data-label="Что исправить">{e.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {preview.items.length > 0 && (
                <div className="rg-scroll">
                  <table className="rg-table" aria-label="Предметы из файла">
                    <thead>
                      <tr>
                        <th>Строка</th>
                        <th>Номер</th>
                        <th>Наименование</th>
                        <th>Категория</th>
                        <th>Кабинет / место</th>
                        <th>Ответственный</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.items.slice(0, 200).map((i) => (
                        <tr key={i.line}>
                          <td data-label="Строка">{i.line}</td>
                          <td data-label="Номер">{i.number}</td>
                          <td data-label="Наименование">{i.title}</td>
                          <td data-label="Категория">{i.category}</td>
                          <td data-label="Кабинет / место">
                            {[i.roomName, i.location]
                              .filter(Boolean)
                              .join(", ")}
                          </td>
                          <td data-label="Ответственный">
                            {i.responsibleName || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {preview.items.length > 200 && (
                <p>Показаны первые 200 предметов из {preview.items.length}.</p>
              )}
              <label>
                Причина загрузки
                <input
                  maxLength={500}
                  value={reason}
                  placeholder="Например, начальное заполнение реестра"
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button
                disabled={
                  preview.errors.length > 0 ||
                  !preview.items.length ||
                  reason.trim().length < 3
                }
                onClick={() => void run(importAll)}
              >
                Импортировать {preview.items.length}
              </button>
              {preview.errors.length > 0 && (
                <p>
                  Исправьте файл и выберите его снова: пока есть ошибки,
                  загрузка недоступна.
                </p>
              )}
            </>
          )}
        </section>
      )}
      {view === "list" && (
        <>
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
                placeholder="Номер, название, серийный номер"
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <button>Найти</button>
            <label>
              Кабинет
              <select
                value={roomId}
                onChange={(e) => setRoomId(e.target.value)}
              >
                <option value="">Все кабинеты</option>
                {rooms.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Категория
              <input
                list="rg-categories"
                value={category}
                maxLength={60}
                placeholder="Любая"
                onChange={(e) => setCategory(e.target.value)}
              />
              <datalist id="rg-categories">
                {(options.categories ?? []).map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </label>
            <label>
              Состояние учёта
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="active">{statuses.active}</option>
                <option value="written_off">{statuses.written_off}</option>
              </select>
            </label>
            <label>
              Ответственный
              <select
                value={responsibleId}
                onChange={(e) => setResponsibleId(e.target.value)}
              >
                <option value="">Все</option>
                {staff.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          </form>
          <div className="rg-controls rg-noprint">
            <button
              onClick={() => {
                if (!leave()) return;
                show(null);
                setDraft({ id: "", version: 0, data: { ...blank(), roomId } });
              }}
            >
              Поставить предмет на учёт
            </button>
            <a
              className="rg-link"
              href={exportHref("inventory/export", workspace, filters)}
              download
            >
              Выгрузить CSV
            </a>
            <button onClick={() => window.print()} disabled={!items?.length}>
              Печать списка
            </button>
          </div>
          <div className="rg-split">
            <div className="rg-list rg-noprint">
              {items && !items.length && <p>Предметов по этому фильтру нет.</p>}
              {items?.map((i) => (
                <button
                  key={i.id}
                  aria-current={selected?.id === i.id}
                  onClick={() => void run(() => open(i.id))}
                >
                  <strong>
                    {i.number} · {i.title}
                  </strong>
                  <span>
                    {i.category} · {conditions[i.condition]}
                  </span>
                  <span>
                    {where({
                      roomId: i.roomId,
                      roomName: i.roomName,
                      location: i.location,
                    })}
                  </span>
                  {i.responsibleName && (
                    <span>Ответственный: {i.responsibleName}</span>
                  )}
                </button>
              ))}
              {next && (
                <button
                  className="rg-noprint"
                  onClick={() =>
                    void run(async () => {
                      const r = await api(path(next), workspace);
                      setItems([...(items ?? []), ...r.items]);
                      setNext(r.nextBefore);
                    })
                  }
                >
                  Ещё предметы
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
                  <h3>{draft.id ? "Изменить карточку" : "Новый предмет"}</h3>
                  <div className="rg-grid">
                    <label>
                      Инвентарный номер
                      <input
                        required
                        maxLength={40}
                        pattern="[0-9A-Za-zА-Яа-яЁё.\/_\-]+"
                        value={draft.data.number}
                        onChange={(e) => edit({ number: e.target.value })}
                      />
                    </label>
                    <label>
                      Наименование
                      <input
                        required
                        minLength={2}
                        maxLength={160}
                        value={draft.data.title}
                        onChange={(e) => edit({ title: e.target.value })}
                      />
                    </label>
                    <label>
                      Категория
                      <input
                        required
                        list="rg-categories"
                        maxLength={60}
                        value={draft.data.category}
                        onChange={(e) => edit({ category: e.target.value })}
                      />
                    </label>
                    <label>
                      Кабинет
                      <select
                        disabled={Boolean(draft.id)}
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
                              {selected?.roomName || "Кабинет из карточки"}
                            </option>
                          )}
                      </select>
                    </label>
                    <label>
                      Место хранения
                      <input
                        disabled={Boolean(draft.id)}
                        maxLength={120}
                        value={draft.data.location}
                        placeholder="Шкаф, склад…"
                        onChange={(e) => edit({ location: e.target.value })}
                      />
                    </label>
                    <label>
                      Ответственный
                      <select
                        value={draft.data.responsibleId}
                        onChange={(e) =>
                          edit({ responsibleId: e.target.value })
                        }
                      >
                        <option value="">Не назначен</option>
                        {staff.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                          </option>
                        ))}
                        {draft.data.responsibleId &&
                          !staff.some(
                            (s) => s.id === draft.data.responsibleId,
                          ) && (
                            <option value={draft.data.responsibleId}>
                              {selected?.responsibleName ||
                                "Сотрудник из карточки"}
                            </option>
                          )}
                      </select>
                    </label>
                    <label>
                      Состояние
                      <select
                        value={draft.data.condition}
                        onChange={(e) => edit({ condition: e.target.value })}
                      >
                        {Object.entries(conditions).map(([v, n]) => (
                          <option key={v} value={v}>
                            {n}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Дата поступления
                      <input
                        type="date"
                        max={options.today}
                        value={draft.data.acquiredOn ?? ""}
                        onChange={(e) =>
                          edit({ acquiredOn: e.target.value || null })
                        }
                      />
                    </label>
                    <label>
                      Серийный номер
                      <input
                        maxLength={80}
                        value={draft.data.serial}
                        onChange={(e) => edit({ serial: e.target.value })}
                      />
                    </label>
                  </div>
                  {draft.id && (
                    <p>
                      Кабинет и место меняются действием «Переместить»: так
                      сохраняется история перемещений.
                    </p>
                  )}
                  {!draft.id && <p>Нужен кабинет или место хранения.</p>}
                  <label>
                    Примечание
                    <textarea
                      maxLength={1000}
                      value={draft.data.note}
                      onChange={(e) => edit({ note: e.target.value })}
                    />
                  </label>
                  <div className="rg-actions">
                    <button>
                      {draft.id ? "Сохранить" : "Поставить на учёт"}
                    </button>
                    <button
                      type="button"
                      onClick={() => leave() && show(selected)}
                    >
                      Отмена
                    </button>
                  </div>
                </form>
              )}
              {!draft && selected && (
                <>
                  <article className="rg-record">
                    <h3>
                      {selected.data.number} · {selected.data.title}
                      <small> · {statuses[selected.status]}</small>
                    </h3>
                    <dl className="rg-facts">
                      <Fact label="Категория">{selected.data.category}</Fact>
                      <Fact label="Где находится">
                        {where({
                          roomId: selected.data.roomId,
                          roomName: selected.roomName,
                          location: selected.data.location,
                        })}
                        {selected.data.roomId && !selected.roomCurrent
                          ? " (кабинет убран из расписания)"
                          : ""}
                      </Fact>
                      <Fact label="Ответственный">
                        {selected.responsibleName}
                        {selected.data.responsibleId &&
                        !selected.responsibleCurrent
                          ? " (больше не работает)"
                          : ""}
                      </Fact>
                      <Fact label="Состояние">
                        {conditions[selected.data.condition]}
                      </Fact>
                      <Fact label="Дата поступления">
                        {selected.data.acquiredOn || ""}
                      </Fact>
                      <Fact label="Серийный номер">{selected.data.serial}</Fact>
                      <Fact label="Примечание">{selected.data.note}</Fact>
                      {selected.status === "written_off" && (
                        <Fact label="Списан">{`${selected.writtenOff.on}, ${selected.writtenOff.by}\n${selected.writtenOff.reason}`}</Fact>
                      )}
                    </dl>
                  </article>
                  {move && (
                    <form
                      className="rg-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void run(relocate);
                      }}
                    >
                      <h4>Переместить предмет</h4>
                      <div className="rg-grid">
                        <label>
                          Новый кабинет
                          <select
                            value={move.roomId}
                            onChange={(e) => {
                              setMove({ ...move, roomId: e.target.value });
                              ctx.dirty(true);
                            }}
                          >
                            <option value="">Не кабинет</option>
                            {rooms.map((r) => (
                              <option key={r.id} value={r.id}>
                                {r.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          Новое место хранения
                          <input
                            maxLength={120}
                            value={move.location}
                            onChange={(e) => {
                              setMove({ ...move, location: e.target.value });
                              ctx.dirty(true);
                            }}
                          />
                        </label>
                        <label>
                          Дата перемещения
                          <input
                            type="date"
                            max={options.today}
                            value={move.movedOn}
                            onChange={(e) => {
                              setMove({ ...move, movedOn: e.target.value });
                              ctx.dirty(true);
                            }}
                          />
                        </label>
                        <label>
                          Причина
                          <input
                            required
                            minLength={3}
                            maxLength={500}
                            value={move.reason}
                            onChange={(e) => {
                              setMove({ ...move, reason: e.target.value });
                              ctx.dirty(true);
                            }}
                          />
                        </label>
                      </div>
                      <div className="rg-actions">
                        <button>Записать перемещение</button>
                        <button
                          type="button"
                          onClick={() => {
                            setMove(null);
                            ctx.dirty(false);
                          }}
                        >
                          Отмена
                        </button>
                      </div>
                    </form>
                  )}
                  {off && (
                    <form
                      className="rg-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void run(writeOff);
                      }}
                    >
                      <h4>Списать предмет</h4>
                      <div className="rg-grid">
                        <label>
                          Дата списания
                          <input
                            type="date"
                            max={options.today}
                            value={off.on}
                            onChange={(e) => {
                              setOff({ ...off, on: e.target.value });
                              ctx.dirty(true);
                            }}
                          />
                        </label>
                        <label>
                          Причина и основание
                          <input
                            required
                            minLength={3}
                            maxLength={500}
                            value={off.reason}
                            onChange={(e) => {
                              setOff({ ...off, reason: e.target.value });
                              ctx.dirty(true);
                            }}
                          />
                        </label>
                      </div>
                      <div className="rg-actions">
                        <button>Списать</button>
                        <button
                          type="button"
                          onClick={() => {
                            setOff(null);
                            ctx.dirty(false);
                          }}
                        >
                          Отмена
                        </button>
                      </div>
                    </form>
                  )}
                  <div className="rg-actions rg-noprint">
                    {selected.status === "active" && (
                      <button onClick={startEdit}>Изменить</button>
                    )}
                    {selected.status === "active" && (
                      <button
                        onClick={() =>
                          setMove({
                            roomId: "",
                            location: "",
                            movedOn: "",
                            reason: "",
                          })
                        }
                      >
                        Переместить
                      </button>
                    )}
                    {selected.status === "active" && (
                      <button onClick={() => setOff({ on: "", reason: "" })}>
                        Списать
                      </button>
                    )}
                    {selected.status === "written_off" &&
                      ctx.user.role === "admin" && (
                        <button onClick={() => void run(restore)}>
                          Восстановить
                        </button>
                      )}
                    <button onClick={() => void run(() => showMoves())}>
                      Перемещения
                    </button>
                    <button onClick={() => void run(() => showHistory())}>
                      История
                    </button>
                  </div>
                  {moves && (
                    <section
                      className="rg-history rg-noprint"
                      aria-label="Перемещения предмета"
                    >
                      <h4>Перемещения</h4>
                      {!moves.items.length && <p>Перемещений не было.</p>}
                      <ol>
                        {moves.items.map((m) => (
                          <li key={m.seq}>
                            <strong>
                              {where(m.source)} → {where(m.target)}
                            </strong>
                            <span>
                              {m.movedOn} · {m.actor}
                            </span>
                            <p>Причина: {m.reason}</p>
                          </li>
                        ))}
                      </ol>
                      {moves.nextBefore && (
                        <button onClick={() => void run(() => showMoves(true))}>
                          Ещё записи
                        </button>
                      )}
                    </section>
                  )}
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
                  Выберите предмет или поставьте новый на учёт.
                </p>
              )}
            </div>
          </div>
          {items && (
            <div className="rg-print rg-printonly">
              <h3>
                Инвентарь{room ? `: кабинет ${room.name}` : ""} на{" "}
                {options.today}
              </h3>
              <table className="rg-table">
                <thead>
                  <tr>
                    <th>Номер</th>
                    <th>Наименование</th>
                    <th>Категория</th>
                    <th>Где находится</th>
                    <th>Ответственный</th>
                    <th>Состояние</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.id}>
                      <td>{i.number}</td>
                      <td>{i.title}</td>
                      <td>{i.category}</td>
                      <td>
                        {where({
                          roomId: i.roomId,
                          roomName: i.roomName,
                          location: i.location,
                        })}
                      </td>
                      <td>{i.responsibleName}</td>
                      <td>{conditions[i.condition]}</td>
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

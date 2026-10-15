import { useEffect, useRef, useState } from "react";
import type { Account } from "./auth";
import { api } from "./api";
import { formatMoney } from "./payments";
import {
  addDays,
  canteenActions,
  mealNames,
  mealStatuses,
  schoolDay,
  schoolMoment,
} from "./canteenHelpers";
import { CanteenSettings, MealBooking, MenuEditor } from "./CanteenEditors";
import "./Canteen.css";

type Props = { user: Account; initialWorkspace: string; onClose: () => void };
export default function Canteen(props: Props) {
  const spaces =
    props.user.role === "admin"
      ? ["school", "demo"]
      : [
          ...new Set([
            ...props.user.workspaces,
            ...props.user.grants.map((g) => g.workspace),
          ]),
        ];
  const [workspace, setWorkspace] = useState(
    spaces.includes(props.initialWorkspace)
      ? props.initialWorkspace
      : spaces[0] || "school",
  );
  return (
    <CanteenWorkspace
      key={workspace}
      {...props}
      workspace={workspace}
      spaces={spaces}
      changeWorkspace={setWorkspace}
    />
  );
}

function CanteenWorkspace({
  user,
  onClose,
  workspace,
  spaces,
  changeWorkspace,
}: Props & {
  workspace: string;
  spaces: string[];
  changeWorkspace: (w: string) => void;
}) {
  const [options, setOptions] = useState<any>(null),
    [tab, setTab] = useState("menus"),
    [start, setStart] = useState(schoolDay()),
    [end, setEnd] = useState(addDays(schoolDay(), 6));
  const [data, setData] = useState<any>({ items: [] }),
    [menu, setMenu] = useState<any>(null),
    [editor, setEditor] = useState<"new" | "edit" | null>(null),
    [copyDate, setCopyDate] = useState("");
  const [archived, setArchived] = useState(false),
    [status, setStatus] = useState("all"),
    [studentId, setStudent] = useState(""),
    [search, setSearch] = useState(""),
    [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string[]>([]),
    [batchAction, setBatchAction] = useState("cancel"),
    [override, setOverride] = useState(false),
    [reason, setReason] = useState(""),
    [preview, setPreview] = useState<any>(null);
  const [historyOrder, setHistoryOrder] = useState(""),
    [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    busyRef = useRef(false),
    replay = useRef({ key: "", id: "" });
  const leave = () =>
    !dirty ||
    window.confirm("Есть несохранённые изменения. Продолжить без сохранения?");
  const range = `start=${start}&end=${end}`;
  const url = (part: string) =>
    `/api/canteen/${part}${part.includes("?") ? "&" : "?"}workspace=${encodeURIComponent(workspace)}`;
  const read = (part: string) => api("canteen/" + part, workspace);
  async function load(id: string | null = menu?.id || null, page = offset) {
    const token = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      if (
        !start ||
        !end ||
        end < start ||
        (Date.parse(end) - Date.parse(start)) / 86400000 > 30
      )
        throw new Error("Выберите период от 1 до 31 дня.");
      const opts = await read("options");
      let result: any = { items: [] };
      if (tab === "menus")
        result = await read(
          `menus?${range}&archived=${archived}&offset=${page}`,
        );
      if (tab === "orders")
        result = await read(
          `orders?${range}&status=${status}&studentId=${encodeURIComponent(studentId)}&q=${encodeURIComponent(search)}&offset=${page}`,
        );
      if (tab === "kitchen") result = await read(`report?${range}`);
      if (tab === "history")
        result = await read(
          `history?orderId=${encodeURIComponent(historyOrder)}`,
        );
      const detail = id && tab === "menus" ? await read("menus/" + id) : null;
      if (token !== sequence.current) return;
      setOptions(opts);
      setData(result);
      setMenu(detail);
      setOffset(page);
      setSelected([]);
      setPreview(null);
      if (opts.permission === "issuer") setBatchAction("serve");
    } catch (e: any) {
      if (token === sequence.current) {
        setError(e.message);
        setData({ items: [] });
        setMenu(null);
        if ([401, 403, 404].includes(e.status)) setOptions(null);
      }
    } finally {
      if (token === sequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    dialog.current?.showModal();
    return () => {
      ++sequence.current;
    };
  }, []);
  useEffect(() => {
    void load(null, 0);
  }, [tab, start, end, archived, status, studentId, historyOrder]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty || busy) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);
  async function run(part: string, body: unknown, method = "POST") {
    if (busyRef.current) throw new Error("Дождитесь завершения действия.");
    const key = JSON.stringify([workspace, part, method, body]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api("canteen/" + part, workspace, method, {
        ...(body as object),
        requestId: replay.current.id,
      });
      if (!part.endsWith("/preview")) {
        replay.current = { key: "", id: "" };
        setDirty(false);
        setNotice("Изменения сохранены.");
      }
      return result;
    } catch (e: any) {
      setError(e.message);
      if ([401, 403, 404].includes(e.status)) {
        setOptions(null);
        setData({ items: [] });
        setMenu(null);
        setEditor(null);
      }
      throw e;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  function changeTab(value: string) {
    if (value === tab) return;
    if (!leave()) return;
    setLoading(true);
    setDirty(false);
    setEditor(null);
    setMenu(null);
    setData({ items: [] });
    setTab(value);
    setOffset(0);
    setSelected([]);
    setPreview(null);
    setError("");
    setNotice("");
  }
  async function openMenu(value: any) {
    if (!leave()) return;
    setEditor(null);
    setDirty(false);
    setCopyDate("");
    await load(value.id, offset);
  }
  async function transition(action: string) {
    if (!leave()) return;
    let explanation = "";
    if (action === "cancel") {
      explanation =
        window.prompt(
          "Основание отмены меню. Уже оформленные заказы нужно отменить отдельно.",
        ) || "";
      if (!explanation.trim()) return;
    }
    try {
      await run(`menus/${menu.id}/transition`, {
        version: menu.version,
        action,
        reason: explanation,
      });
      await load();
    } catch {
      /* displayed */
    }
  }
  async function orderAction(row: any, action: string) {
    const explanation = window.prompt("Основание исправления отметки выдачи");
    if (!explanation?.trim()) return;
    try {
      await run(`orders/${row.id}/action`, {
        version: row.version,
        action,
        reason: explanation,
      });
      await load(null, offset);
    } catch {
      /* displayed */
    }
  }
  function invalidateBatch() {
    setDirty(true);
    setPreview(null);
  }
  async function submitBatch(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const rows = data.items.filter((x: any) => selected.includes(x.id));
    if (!rows.length) return;
    const payload = {
      action: batchAction,
      orders: rows.map((x: any) => ({ id: x.id, version: x.version })),
      overrideCutoff: override,
      reason,
    };
    try {
      if (!preview) setPreview(await run("batch/preview", payload));
      else {
        await run("batch", { ...payload, previewHash: preview.previewHash });
        setPreview(null);
        setSelected([]);
        await load(null, offset);
      }
    } catch (e: any) {
      setPreview(null);
      setError(e.message);
    }
  }
  const manager = options?.permission === "manager",
    issuer = options?.permission === "issuer",
    kitchen = options?.permission === "kitchen";
  const canViewOrders =
    manager || issuer || ["parent", "student"].includes(user.role);
  const shared = {
    options,
    run,
    onDirty: () => setDirty(true),
    onError: setError,
  };
  return (
    <dialog
      ref={dialog}
      className="canteen-dialog"
      aria-labelledby="canteen-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && leave()) onClose();
      }}
    >
      <header>
        <div>
          <span className="canteen-eyebrow">АТЛАС · ШКОЛЬНОЕ ПИТАНИЕ</span>
          <h2 id="canteen-title">Столовая</h2>
          <p>Меню, заказы и выдача питания</p>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            if (leave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      {error && (
        <p className="canteen-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="canteen-notice" role="status">
          {notice}
        </p>
      )}
      {loading && (
        <p role="status" className="canteen-muted">
          Загружаю данные столовой…
        </p>
      )}
      <fieldset className="canteen-body" disabled={busy || loading}>
        <div className="canteen-toolbar">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => {
                if (leave()) changeWorkspace(e.target.value);
              }}
            >
              {spaces.map((w) => (
                <option key={w} value={w}>
                  {w === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              if (leave()) {
                setDirty(false);
                void load();
              }
            }}
          >
            Обновить
          </button>
        </div>
        {options && (
          <>
            <nav className="canteen-tabs" aria-label="Разделы столовой">
              <button
                aria-pressed={tab === "menus"}
                onClick={() => changeTab("menus")}
              >
                Меню
              </button>
              {canViewOrders && (
                <button
                  aria-pressed={tab === "orders"}
                  onClick={() => changeTab("orders")}
                >
                  Заказы и выдача
                </button>
              )}
              {options.permission && (
                <button
                  aria-pressed={tab === "kitchen"}
                  onClick={() => changeTab("kitchen")}
                >
                  Сводка кухни
                </button>
              )}
              {manager && (
                <button
                  aria-pressed={tab === "history"}
                  onClick={() => {
                    setHistoryOrder("");
                    changeTab("history");
                  }}
                >
                  История
                </button>
              )}
              {options.admin && (
                <button
                  aria-pressed={tab === "settings"}
                  onClick={() => changeTab("settings")}
                >
                  Настройки
                </button>
              )}
            </nav>
            {["menus", "orders", "kitchen"].includes(tab) && (
              <div className="canteen-toolbar canteen-period">
                <label>
                  С даты
                  <input
                    type="date"
                    value={start}
                    onChange={(e) => {
                      if (leave()) {
                        setDirty(false);
                        setStart(e.target.value);
                        setEditor(null);
                      }
                    }}
                  />
                </label>
                <label>
                  По дату
                  <input
                    type="date"
                    value={end}
                    onChange={(e) => {
                      if (leave()) {
                        setDirty(false);
                        setEnd(e.target.value);
                        setEditor(null);
                      }
                    }}
                  />
                </label>
                <span className="canteen-muted">
                  До 31 дня · время школы UTC+5
                </span>
              </div>
            )}
            {tab === "menus" && (
              <>
                <div className="canteen-toolbar">
                  {manager && (
                    <button
                      className="canteen-primary"
                      onClick={() => {
                        if (leave()) {
                          setDirty(false);
                          setEditor("new");
                          setMenu(null);
                        }
                      }}
                    >
                      Новое меню
                    </button>
                  )}
                  <label className="canteen-check">
                    <input
                      type="checkbox"
                      checked={archived}
                      onChange={(e) => {
                        if (leave()) {
                          setDirty(false);
                          setArchived(e.target.checked);
                        }
                      }}
                    />
                    Показать архив
                  </label>
                </div>
                {editor ? (
                  <MenuEditor
                    {...shared}
                    key={
                      editor === "edit" ? menu.id + ":" + menu.version : "new"
                    }
                    menu={editor === "edit" ? menu : undefined}
                    onSaved={(id) => {
                      setEditor(null);
                      void load(id, 0);
                    }}
                    onCancel={() => {
                      if (leave()) {
                        setDirty(false);
                        setEditor(null);
                      }
                    }}
                  />
                ) : (
                  <div className="canteen-layout">
                    <aside>
                      <div className="canteen-menu-list">
                        {data.items.map((m: any) => (
                          <button
                            key={m.id}
                            className={menu?.id === m.id ? "selected" : ""}
                            onClick={() => void openMenu(m)}
                          >
                            <span>
                              {m.day} · {mealNames[m.meal]}
                            </span>
                            <strong>{m.definition.title}</strong>
                            <small>{mealStatuses[m.status]}</small>
                          </button>
                        ))}
                      </div>
                      {!data.items.length && (
                        <p className="canteen-empty">
                          На выбранный период меню пока нет.
                        </p>
                      )}
                      {(offset > 0 || data.nextOffset != null) && (
                        <div className="canteen-actions">
                          <button
                            disabled={!offset}
                            onClick={() =>
                              leave() &&
                              void load(null, Math.max(0, offset - 50))
                            }
                          >
                            Назад
                          </button>
                          <span>
                            {offset + 1}–{offset + data.items.length}
                          </span>
                          <button
                            disabled={data.nextOffset == null}
                            onClick={() =>
                              leave() && void load(null, data.nextOffset)
                            }
                          >
                            Далее
                          </button>
                        </div>
                      )}
                    </aside>
                    <section>
                      {menu ? (
                        <>
                          <article className="canteen-card">
                            <span className="canteen-status">
                              {mealStatuses[menu.status]}
                            </span>
                            <h3>{menu.definition.title}</h3>
                            <p>
                              {menu.day} · {mealNames[menu.meal]} · выдача{" "}
                              {menu.definition.starts}–{menu.definition.ends}
                            </p>
                            {menu.definition.notice && (
                              <p>{menu.definition.notice}</p>
                            )}
                            <p className="canteen-muted">
                              Заказать до{" "}
                              {schoolMoment(menu.definition.orderClosesAt)}.
                              Отменить до{" "}
                              {schoolMoment(menu.definition.cancelClosesAt)}.
                            </p>
                            {menu.definition.variants.map((v: any) => (
                              <div className="canteen-dish" key={v.id}>
                                <div className="canteen-dish-title">
                                  <h4>{v.title}</h4>
                                  <strong>
                                    {v.price
                                      ? formatMoney(
                                          v.price,
                                          menu.decimals,
                                          menu.currency,
                                        )
                                      : "Включено"}
                                  </strong>
                                </div>
                                <p className="canteen-preserve">{v.dishes}</p>
                                <p>
                                  <b>Аллергены:</b>{" "}
                                  {v.allergens.length
                                    ? v.allergens.join(", ")
                                    : "не указаны школой"}
                                </p>
                                {v.notes && <p>{v.notes}</p>}
                                <small>
                                  {v.capacity == null
                                    ? "Без отдельного лимита порций"
                                    : `Осталось порций: ${Math.max(0, v.capacity - (menu.reserved?.[v.id] || 0))}`}
                                </small>
                              </div>
                            ))}
                            {manager && (
                              <div className="canteen-actions">
                                {menu.status === "draft" && (
                                  <>
                                    <button
                                      onClick={() => {
                                        if (leave()) setEditor("edit");
                                      }}
                                    >
                                      Редактировать
                                    </button>
                                    <button
                                      className="canteen-primary"
                                      onClick={() => void transition("publish")}
                                    >
                                      Опубликовать меню
                                    </button>
                                  </>
                                )}
                                {menu.status === "published" && (
                                  <button
                                    onClick={() => void transition("close")}
                                  >
                                    Закрыть приём заказов
                                  </button>
                                )}
                                {menu.status === "closed" && (
                                  <button
                                    onClick={() => void transition("reopen")}
                                  >
                                    Открыть приём заказов
                                  </button>
                                )}
                                {["draft", "published", "closed"].includes(
                                  menu.status,
                                ) && (
                                  <button
                                    onClick={() => void transition("cancel")}
                                  >
                                    Отменить меню
                                  </button>
                                )}
                                {!["draft", "archived"].includes(
                                  menu.status,
                                ) && (
                                  <button
                                    onClick={() => void transition("archive")}
                                  >
                                    В архив
                                  </button>
                                )}
                              </div>
                            )}
                            {manager && (
                              <form
                                className="canteen-copy canteen-toolbar"
                                onSubmit={async (e) => {
                                  e.preventDefault();
                                  if (!leave()) return;
                                  try {
                                    const result = await run(
                                      `menus/${menu.id}/copy`,
                                      { version: menu.version, day: copyDate },
                                    );
                                    setCopyDate("");
                                    await load(result.id, 0);
                                  } catch {
                                    /* displayed */
                                  }
                                }}
                              >
                                <label>
                                  Скопировать на дату
                                  <input
                                    type="date"
                                    required
                                    value={copyDate}
                                    onChange={(e) =>
                                      setCopyDate(e.target.value)
                                    }
                                  />
                                </label>
                                <button type="submit">Создать копию</button>
                              </form>
                            )}
                          </article>
                          {menu.status === "published" && options.canOrder && (
                            <MealBooking
                              {...shared}
                              key={menu.id + ":" + menu.version}
                              menu={menu}
                              onSaved={() => void load()}
                            />
                          )}
                          {menu.status === "published" &&
                            !options.canOrder &&
                            !kitchen && (
                              <p className="canteen-muted">
                                Заказ оформляет родитель или администрация
                                столовой.
                              </p>
                            )}
                        </>
                      ) : (
                        <div className="canteen-card canteen-empty">
                          <h3>Выберите день и приём пищи</h3>
                          <p>
                            Здесь появятся блюда, аллергены, варианты и условия
                            заказа.
                          </p>
                        </div>
                      )}
                    </section>
                  </div>
                )}
              </>
            )}
            {tab === "orders" && canViewOrders && (
              <>
                <form
                  className="canteen-toolbar"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (leave()) {
                      setDirty(false);
                      void load(null, 0);
                    }
                  }}
                >
                  <label>
                    Ученик
                    <select
                      value={studentId}
                      onChange={(e) => {
                        if (leave()) {
                          setDirty(false);
                          setStudent(e.target.value);
                        }
                      }}
                    >
                      <option value="">Все доступные ученики</option>
                      {options.students.map((p: any) => (
                        <option key={p.id} value={p.id}>
                          {p.name} · {p.className}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Статус
                    <select
                      value={status}
                      onChange={(e) => {
                        if (leave()) {
                          setDirty(false);
                          setStatus(e.target.value);
                        }
                      }}
                    >
                      <option value="all">Все статусы</option>
                      {["ordered", "served", "missed", "cancelled"].map((s) => (
                        <option key={s} value={s}>
                          {mealStatuses[s]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Поиск по имени
                    <input
                      type="search"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </label>
                  <button type="submit">Найти</button>
                  {(manager || issuer) && (
                    <a href={url(`orders/export?${range}`)} download>
                      Скачать список CSV
                    </a>
                  )}
                </form>
                <div className="canteen-order-list">
                  {data.items.map((r: any) => (
                    <article className="canteen-order" key={r.id}>
                      <label className="canteen-check">
                        <input
                          type="checkbox"
                          aria-label={`Выбрать заказ ${r.detail.studentName}`}
                          checked={selected.includes(r.id)}
                          onChange={(e) => {
                            invalidateBatch();
                            setSelected(
                              e.target.checked
                                ? [...selected, r.id]
                                : selected.filter((id) => id !== r.id),
                            );
                          }}
                        />
                        <strong>{r.detail.studentName}</strong>
                        <span>{r.detail.className}</span>
                      </label>
                      <div className="canteen-order-info">
                        <span>
                          {r.day} · {mealNames[r.meal]}
                        </span>
                        <strong>{r.detail.variant.title}</strong>
                        <span className="canteen-status">
                          {mealStatuses[r.status]}
                        </span>
                      </div>
                      <details>
                        <summary>Состав и условия заказа</summary>
                        <p className="canteen-preserve">
                          {r.detail.variant.dishes}
                        </p>
                        <p>
                          Аллергены:{" "}
                          {r.detail.variant.allergens.join(", ") ||
                            "не указаны школой"}
                        </p>
                        <p>
                          {r.detail.variant.price
                            ? formatMoney(
                                r.detail.variant.price,
                                r.detail.decimals,
                                r.detail.currency,
                              )
                            : "Включено в питание"}{" "}
                          · выдача {r.detail.starts}–{r.detail.ends}
                        </p>
                        {r.served_at && (
                          <p>Выдано {schoolMoment(r.served_at)}</p>
                        )}
                      </details>
                      {manager && (
                        <div className="canteen-actions">
                          {["served", "missed"].includes(r.status) && (
                            <button
                              onClick={() =>
                                void orderAction(r, "undo-service")
                              }
                            >
                              Исправить отметку
                            </button>
                          )}
                          <button
                            onClick={() => {
                              if (leave()) {
                                setHistoryOrder(r.id);
                                changeTab("history");
                              }
                            }}
                          >
                            История заказа
                          </button>
                        </div>
                      )}
                    </article>
                  ))}
                </div>
                {!data.items.length && (
                  <p className="canteen-empty">
                    Заказов по выбранным условиям нет.
                  </p>
                )}
                {!!data.items.length &&
                  (manager || issuer || options.canOrder) && (
                    <form className="canteen-card" onSubmit={submitBatch}>
                      <h3>Действие с выбранными заказами</h3>
                      <div className="canteen-actions">
                        <button
                          type="button"
                          onClick={() => {
                            invalidateBatch();
                            setSelected(
                              data.items
                                .filter((r: any) => r.status === "ordered")
                                .map((r: any) => r.id),
                            );
                          }}
                        >
                          Выбрать ожидающие на странице
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            invalidateBatch();
                            setSelected([]);
                          }}
                        >
                          Снять выбор
                        </button>
                        <span>Выбрано: {selected.length}</span>
                      </div>
                      <label>
                        Действие
                        <select
                          value={batchAction}
                          onChange={(e) => {
                            invalidateBatch();
                            setBatchAction(e.target.value);
                          }}
                        >
                          {(manager || options.canOrder) && (
                            <option value="cancel">
                              Отменить и вернуть начисление на счёт
                            </option>
                          )}
                          {(manager || issuer) && (
                            <>
                              <option value="serve">Отметить выдачу</option>
                              <option value="missed">Отметить неявку</option>
                            </>
                          )}
                        </select>
                      </label>
                      {manager && (
                        <>
                          <label className="canteen-check">
                            <input
                              type="checkbox"
                              checked={override}
                              onChange={(e) => {
                                invalidateBatch();
                                setOverride(e.target.checked);
                              }}
                            />
                            Исключение по сроку отмены или поздняя выдача
                          </label>
                          <label>
                            Основание школы
                            <input
                              required={override}
                              maxLength={1000}
                              value={reason}
                              onChange={(e) => {
                                invalidateBatch();
                                setReason(e.target.value);
                              }}
                            />
                          </label>
                        </>
                      )}
                      {preview && (
                        <div className="canteen-preview" role="status">
                          <strong>Будет обработано: {preview.count}</strong>
                          <ul>
                            {preview.items.map((x: any) => (
                              <li
                                key={x.studentId + ":" + x.day + ":" + x.meal}
                              >
                                {x.name} · {x.day} · {x.title} →{" "}
                                {mealStatuses[x.status]}
                              </li>
                            ))}
                          </ul>
                          {batchAction === "cancel" && (
                            <p>
                              Начисление за платный заказ будет отменено.
                              Банковский возврат не выполняется.
                            </p>
                          )}
                        </div>
                      )}
                      <button
                        className="canteen-primary"
                        disabled={
                          !selected.length ||
                          !(manager || issuer || options.canOrder)
                        }
                        type="submit"
                      >
                        {preview
                          ? "Подтвердить действие"
                          : "Проверить действие"}
                      </button>
                    </form>
                  )}
                {(offset > 0 || data.nextOffset != null) && (
                  <div className="canteen-actions">
                    <button
                      disabled={!offset}
                      onClick={() => {
                        if (leave()) {
                          setDirty(false);
                          void load(null, Math.max(0, offset - 50));
                        }
                      }}
                    >
                      Назад
                    </button>
                    <span>
                      Показано {offset + 1}–{offset + data.items.length} из{" "}
                      {data.totalCount}
                    </span>
                    <button
                      disabled={data.nextOffset == null}
                      onClick={() => {
                        if (leave()) {
                          setDirty(false);
                          void load(null, data.nextOffset);
                        }
                      }}
                    >
                      Далее
                    </button>
                  </div>
                )}
              </>
            )}
            {tab === "kitchen" && options.permission && (
              <section className="canteen-card">
                <div className="canteen-toolbar">
                  <h3>
                    {issuer
                      ? "Сводка по вашим классам"
                      : "Количество порций для кухни"}
                  </h3>
                  <a href={url(`report/export?${range}`)} download>
                    Скачать сводку CSV
                  </a>
                </div>
                <p>
                  Порции включают ожидающие заказы, уже выданное питание и
                  неявки. Отмены показаны отдельно.
                </p>
                <div className="canteen-totals">
                  {[
                    ["portions", "Порций"],
                    ["ordered", "Ожидают"],
                    ["served", "Выдано"],
                    ["missed", "Не пришли"],
                    ["cancelled", "Отменено"],
                  ].map(([k, label]) => (
                    <div key={k}>
                      <span>{label}</span>
                      <strong>
                        {data.items.reduce(
                          (sum: number, x: any) => sum + x[k],
                          0,
                        )}
                      </strong>
                    </div>
                  ))}
                </div>
                <div className="canteen-scroll">
                  <table>
                    <thead>
                      <tr>
                        {[
                          "Дата",
                          "Приём пищи",
                          "Вариант",
                          "Аллергены",
                          "Порций",
                          "Ожидают",
                          "Выдано",
                          "Не пришли",
                          "Отменено",
                        ].map((x) => (
                          <th key={x}>{x}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((x: any, i: number) => (
                        <tr key={i}>
                          <td>{x.day}</td>
                          <td>{x.meal}</td>
                          <td>{x.title}</td>
                          <td>{x.allergens.join(", ") || "—"}</td>
                          <td>{x.portions}</td>
                          <td>{x.ordered}</td>
                          <td>{x.served}</td>
                          <td>{x.missed}</td>
                          <td>{x.cancelled}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!data.items.length && (
                  <p>За выбранный период нет опубликованного меню.</p>
                )}
              </section>
            )}
            {tab === "settings" && options.admin && (
              <CanteenSettings
                {...shared}
                key={options.config.version}
                onSaved={() => void load(null, 0)}
              />
            )}
            {tab === "history" && manager && (
              <section className="canteen-card">
                <h3>{historyOrder ? "История заказа" : "История столовой"}</h3>
                {historyOrder && (
                  <button onClick={() => setHistoryOrder("")}>
                    Показать всю историю
                  </button>
                )}
                <ol className="canteen-history">
                  {data.items.map((x: any) => (
                    <li key={x.seq}>
                      <strong>{canteenActions[x.action] || "Изменение"}</strong>
                      <p>
                        {schoolMoment(x.created)} · {x.actor_name}
                      </p>
                      {x.reason && <p>{x.reason}</p>}
                    </li>
                  ))}
                </ol>
                {!data.items.length && <p>Записей пока нет.</p>}
                {data.nextBefore && (
                  <button
                    onClick={async () => {
                      setLoading(true);
                      try {
                        const page = await read(
                          `history?orderId=${encodeURIComponent(historyOrder)}&before=${data.nextBefore}`,
                        );
                        setData({
                          ...page,
                          items: [...data.items, ...page.items],
                        });
                      } catch (e: any) {
                        setError(e.message);
                        setData({ items: [] });
                      } finally {
                        setLoading(false);
                      }
                    }}
                  >
                    Более ранние записи
                  </button>
                )}
              </section>
            )}
            {!options.permission &&
              !["parent", "student"].includes(user.role) && (
                <p>
                  Для работы в столовой администратор выдаёт отдельное
                  назначение.
                </p>
              )}
          </>
        )}
      </fieldset>
    </dialog>
  );
}

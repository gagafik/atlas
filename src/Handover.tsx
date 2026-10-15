import { useEffect, useRef, useState } from "react";
import type { Account } from "./auth";
import { api } from "./api";
import {
  handoverStatuses,
  handoverActions,
  schoolDay,
  schoolMoment,
  pendingPickup,
} from "./handoverHelpers";
import {
  AdultProposalEditor,
  AdultDecisionEditor,
  PickupEditor,
  PickupActionEditor,
  TransferEditor,
  HandoverSettings,
} from "./HandoverEditors";
import "./Handover.css";

type Props = { user: Account; initialWorkspace: string; onClose: () => void };
type Editor = {
  kind: "proposal" | "decision" | "pickup" | "action" | "transfer";
  row?: any;
  action?: string;
} | null;
export default function Handover(p: Props) {
  const spaces =
    p.user.role === "admin"
      ? ["school", "demo"]
      : [
          ...new Set([
            ...p.user.workspaces,
            ...p.user.grants.map((g) => g.workspace),
          ]),
        ];
  const [workspace, setWorkspace] = useState(
    spaces.includes(p.initialWorkspace)
      ? p.initialWorkspace
      : spaces[0] || "school",
  );
  return (
    <HandoverWorkspace
      key={workspace}
      {...p}
      workspace={workspace}
      spaces={spaces}
      changeWorkspace={setWorkspace}
    />
  );
}
function HandoverWorkspace({
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
    [tab, setTab] = useState("pickups"),
    [data, setData] = useState<any>({ items: [] });
  const [start, setStart] = useState(schoolDay()),
    [end, setEnd] = useState(schoolDay()),
    [student, setStudent] = useState(""),
    [status, setStatus] = useState("all"),
    [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0),
    [before, setBefore] = useState<number | null>(null),
    [historyFilter, setHistoryFilter] = useState({ pickupId: "", adultId: "" });
  const [editor, setEditor] = useState<Editor>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [dirty, setDirty] = useState(false),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    busyRef = useRef(false),
    replay = useRef({ key: "", id: "" });
  const leave = () =>
    !busyRef.current &&
    (!dirty ||
      window.confirm(
        "Есть несохранённые изменения. Продолжить без сохранения?",
      ));
  const read = (path: string) => api("handover/" + path, workspace);
  async function load(page = 0, cursor: number | null = null) {
    const token = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      if (
        tab === "pickups" &&
        (!start ||
          !end ||
          end < start ||
          (Date.parse(end) - Date.parse(start)) / 86400000 > 30)
      )
        throw new Error("Выберите период от 1 до 31 дня.");
      const opts = await read("options");
      const params = new URLSearchParams({
        studentId: student,
        status,
        q: search,
        offset: String(page),
      });
      let result: any = { items: [] };
      if (tab === "pickups")
        result = await read(`pickups?${params}&start=${start}&end=${end}`);
      if (tab === "adults") result = await read(`adults?${params}`);
      if (tab === "history")
        result = await read(
          `history?${new URLSearchParams({ studentId: student, ...historyFilter })}${cursor ? `&before=${cursor}` : ""}`,
        );
      if (token !== sequence.current) return;
      setOptions(opts);
      setData(result);
      setOffset(page);
      setBefore(cursor);
    } catch (e: any) {
      if (token === sequence.current) {
        setError(e.message);
        setData({ items: [] });
        if ([401, 403, 404].includes(e.status)) {
          setOptions(null);
          setEditor(null);
          setDirty(false);
        }
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
    void load();
  }, [tab, start, end, student, status, historyFilter]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty || busy) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);
  async function run(part: string, body: any, method = "POST") {
    if (busyRef.current) throw new Error("Дождитесь завершения действия.");
    const key = JSON.stringify([workspace, part, method, body]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api("handover/" + part, workspace, method, {
        ...body,
        requestId: replay.current.id,
      });
      if (!part.endsWith("/review")) {
        replay.current = { key: "", id: "" };
        setDirty(false);
        setNotice("Изменения сохранены.");
      }
      return result;
    } catch (e: any) {
      setError(e.message);
      if ([401, 403, 404].includes(e.status)) {
        setData({ items: [] });
        setOptions(null);
        setEditor(null);
        setDirty(false);
      }
      throw e;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  function resetView() {
    setLoading(true);
    setData({ items: [] });
    setEditor(null);
    setDirty(false);
    setOffset(0);
    setBefore(null);
    setError("");
    setNotice("");
  }
  function changeTab(value: string) {
    if (tab === value || !leave()) return;
    resetView();
    setStatus("all");
    setSearch("");
    setHistoryFilter({ pickupId: "", adultId: "" });
    setTab(value);
  }
  function history(row: any, kind: string) {
    if (!leave()) return;
    resetView();
    setStatus("all");
    setHistoryFilter({
      pickupId: kind === "pickup" ? row.id : "",
      adultId: kind === "adult" ? row.id : "",
    });
    setTab("history");
  }
  async function openEditor(
    kind: NonNullable<Editor>["kind"],
    row?: any,
    action?: string,
  ) {
    if (!leave()) return;
    setDirty(false);
    setError("");
    setNotice("");
    setEditor(null);
    if (!row) {
      setEditor({ kind });
      return;
    }
    const token = ++sequence.current;
    setLoading(true);
    try {
      const current = await read(
        `${["decision", "pickup"].includes(kind) ? "adults" : "pickups"}/${row.id}`,
      );
      if (token === sequence.current) setEditor({ kind, row: current, action });
    } catch (e: any) {
      if (token === sequence.current) {
        setError(e.message);
        if ([401, 403, 404].includes(e.status)) {
          setOptions(null);
          setData({ items: [] });
          setEditor(null);
        }
      }
    } finally {
      if (token === sequence.current) setLoading(false);
    }
  }
  function cancelEditor() {
    if (leave()) {
      setEditor(null);
      setDirty(false);
      setError("");
    }
  }
  function saved(result?: { day: string }) {
    setEditor(null);
    setDirty(false);
    if (
      result &&
      (tab !== "pickups" ||
        start !== result.day ||
        end !== result.day ||
        status !== "all")
    ) {
      setLoading(true);
      setData({ items: [] });
      setStart(result.day);
      setEnd(result.day);
      setStatus("all");
      setSearch("");
      setTab("pickups");
    } else void load(offset, before);
  }
  async function act(row: any, action: string) {
    try {
      await run(`pickups/${row.id}/action`, { version: row.version, action });
      await load(offset, before);
    } catch {
      /* Error is displayed by run. */
    }
  }
  const manager = options?.permission === "manager",
    staff = Boolean(options?.permission);
  const canPropose = options?.config.enabled && (manager || options.parent);
  const canRequest =
    options?.config.enabled &&
    (manager || (options.parent && options.config.parentRequests));
  const ep = {
    options,
    run,
    onSaved: saved,
    onCancel: cancelEditor,
    onDirty: () => setDirty(true),
    onError: setError,
  };
  const exportHref = `/api/handover/pickups/export?${new URLSearchParams({ workspace, start, end })}`;
  return (
    <dialog
      ref={dialog}
      className="handover-dialog"
      aria-labelledby="handover-title"
      onCancel={(e) => {
        e.preventDefault();
        if (leave()) onClose();
      }}
    >
      <header>
        <div>
          <span className="handover-eyebrow">АТЛАС · СЕМЬЯ И ШКОЛА</span>
          <h2 id="handover-title">Уход детей</h2>
          <p>Заявки, разрешённые взрослые и подтверждение передачи.</p>
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
      <div role="alert" className={error ? "handover-error" : ""}>
        {error}
      </div>
      <div role="status" className={notice ? "handover-notice" : ""}>
        {notice}
      </div>
      <fieldset className="handover-body" disabled={busy || loading}>
        <div className="handover-toolbar">
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
                setEditor(null);
                setDirty(false);
                void load(offset, before);
              }
            }}
          >
            Обновить
          </button>
        </div>
        {options && (
          <>
            {!options.config.enabled && (
              <p className="handover-notice">
                Уход детей пока не включён.{" "}
                {options.admin
                  ? "Укажите школьные правила и ответственных в настройках."
                  : "Школа сообщит, когда можно будет оформлять заявки."}
              </p>
            )}
            {!staff && !options.parent && (
              <p>
                Доступ предоставляется родителям и назначенным школой
                сотрудникам.
              </p>
            )}
            {(staff || options.parent || options.admin) && (
              <>
                <nav className="handover-tabs" aria-label="Разделы ухода детей">
                  {[
                    ["pickups", "Заявки"],
                    ["adults", "Кто забирает"],
                    ["history", "История"],
                    ...(options.admin ? [["settings", "Настройки"]] : []),
                  ].map(([id, name]) => (
                    <button
                      key={id}
                      aria-pressed={tab === id}
                      onClick={() => changeTab(id)}
                    >
                      {name}
                    </button>
                  ))}
                </nav>
                {editor ? (
                  <>
                    {editor.kind === "proposal" && (
                      <AdultProposalEditor {...ep} />
                    )}
                    {editor.kind === "decision" && (
                      <AdultDecisionEditor
                        {...ep}
                        adult={editor.row}
                        action={editor.action!}
                      />
                    )}
                    {editor.kind === "pickup" && (
                      <PickupEditor {...ep} adult={editor.row} />
                    )}
                    {editor.kind === "action" && (
                      <PickupActionEditor
                        {...ep}
                        pickup={editor.row}
                        action={editor.action!}
                      />
                    )}
                    {editor.kind === "transfer" && (
                      <TransferEditor {...ep} pickup={editor.row} />
                    )}
                  </>
                ) : (
                  <>
                    {tab !== "settings" && (
                      <div className="handover-toolbar">
                        <label>
                          Ребёнок
                          <select
                            value={student}
                            onChange={(e) => {
                              resetView();
                              setStudent(e.target.value);
                            }}
                          >
                            <option value="">Все доступные дети</option>
                            {options.students.map((s: any) => (
                              <option key={s.id} value={s.id}>
                                {s.name} · {s.className}
                              </option>
                            ))}
                          </select>
                        </label>
                        {tab === "pickups" && (
                          <>
                            <label>
                              С даты
                              <input
                                type="date"
                                value={start}
                                onChange={(e) => {
                                  resetView();
                                  setStart(e.target.value);
                                }}
                              />
                            </label>
                            <label>
                              По дату
                              <input
                                type="date"
                                value={end}
                                onChange={(e) => {
                                  resetView();
                                  setEnd(e.target.value);
                                }}
                              />
                            </label>
                          </>
                        )}
                        {tab !== "history" && (
                          <>
                            <label>
                              Состояние
                              <select
                                value={status}
                                onChange={(e) => {
                                  resetView();
                                  setStatus(e.target.value);
                                }}
                              >
                                <option value="all">Все состояния</option>
                                {(tab === "adults"
                                  ? [
                                      "proposed",
                                      "approved",
                                      "rejected",
                                      "revoked",
                                    ]
                                  : [
                                      "pending",
                                      "requested",
                                      "arrived",
                                      "ready",
                                      "completed",
                                      "returned",
                                      "cancelled",
                                      "expired",
                                    ]
                                ).map((id) => (
                                  <option key={id} value={id}>
                                    {handoverStatuses[id] ||
                                      "Все незавершённые"}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <form
                              onSubmit={(e) => {
                                e.preventDefault();
                                void load();
                              }}
                            >
                              <label>
                                Поиск по имени
                                <input
                                  maxLength={120}
                                  value={search}
                                  onChange={(e) => setSearch(e.target.value)}
                                />
                              </label>
                              <button>Найти</button>
                            </form>
                          </>
                        )}
                      </div>
                    )}
                    {tab === "settings" && options.admin && (
                      <HandoverSettings
                        key={options.config.version}
                        {...ep}
                        onCancel={() => changeTab("pickups")}
                      />
                    )}
                    {tab === "adults" && (
                      <>
                        <div className="handover-actions">
                          {canPropose && (
                            <button
                              className="handover-primary"
                              disabled={!options.students.length}
                              onClick={() => void openEditor("proposal")}
                            >
                              Предложить взрослого
                            </button>
                          )}
                        </div>
                        <p>
                          Найдено разрешений: {data.totalCount ?? 0}. Для
                          изменения одобренного взрослого отзовите разрешение и
                          отправьте новое предложение.
                        </p>
                        <div className="handover-list">
                          {data.items.map((row: any) => (
                            <article className="handover-card" key={row.id}>
                              <span className="handover-status">
                                {handoverStatuses[row.status]}
                              </span>
                              <h3>{row.detail.name}</h3>
                              <p>
                                {row.detail.relationship} ·{" "}
                                {row.detail.studentName} ·{" "}
                                {row.detail.className}
                              </p>
                              <p>
                                {row.detail.startDate} — {row.detail.endDate}
                              </p>
                              <p>Родитель: {row.detail.sponsorName}</p>
                              {row.detail.contact && (
                                <p>Контакт: {row.detail.contact}</p>
                              )}
                              {row.status === "approved" &&
                                row.unavailableReason && (
                                  <p className="handover-error">
                                    {row.unavailableReason}
                                  </p>
                                )}
                              <div className="handover-actions">
                                {canRequest && row.status === "approved" && (
                                  <button
                                    onClick={() =>
                                      void openEditor("pickup", row)
                                    }
                                  >
                                    Оформить уход
                                  </button>
                                )}
                                {manager && row.status === "proposed" && (
                                  <>
                                    <button
                                      disabled={!options.config.enabled}
                                      onClick={() =>
                                        void openEditor(
                                          "decision",
                                          row,
                                          "approve",
                                        )
                                      }
                                    >
                                      Одобрить
                                    </button>
                                    <button
                                      onClick={() =>
                                        void openEditor(
                                          "decision",
                                          row,
                                          "reject",
                                        )
                                      }
                                    >
                                      Не одобрять
                                    </button>
                                  </>
                                )}
                                {row.canRevoke && (
                                  <button
                                    onClick={() =>
                                      void openEditor("decision", row, "revoke")
                                    }
                                  >
                                    Отозвать
                                  </button>
                                )}
                                {staff && (
                                  <button onClick={() => history(row, "adult")}>
                                    История разрешения
                                  </button>
                                )}
                              </div>
                            </article>
                          ))}
                        </div>
                      </>
                    )}
                    {tab === "pickups" && (
                      <>
                        <div className="handover-actions">
                          {canRequest && (
                            <button
                              className="handover-primary"
                              onClick={() => changeTab("adults")}
                            >
                              Новая заявка — выбрать взрослого
                            </button>
                          )}
                          {staff && (
                            <a href={exportHref} download>
                              Скачать все заявки за период (CSV)
                            </a>
                          )}
                        </div>
                        <p>
                          Найдено заявок: {data.totalCount ?? 0}. Время — по
                          часовому поясу школы.
                        </p>
                        <div className="handover-list">
                          {data.items.map((row: any) => {
                            const live =
                              row.day === options.today &&
                              pendingPickup(row) &&
                              !row.unavailableReason &&
                              options.config.enabled;
                            return (
                              <article className="handover-card" key={row.id}>
                                <span className="handover-status">
                                  {handoverStatuses[row.displayStatus]}
                                </span>
                                <h3>{row.detail.studentName}</h3>
                                <p>
                                  {row.detail.className} · {row.day} ·{" "}
                                  {row.expected_time}
                                </p>
                                <p>
                                  Забирает:{" "}
                                  <strong>{row.detail.adultName}</strong> ·{" "}
                                  {row.detail.relationship}
                                </p>
                                <p>{row.detail.locationName}</p>
                                {row.detail.note && (
                                  <p className="handover-preserve">
                                    {row.detail.note}
                                  </p>
                                )}
                                {row.unavailableReason && (
                                  <p className="handover-error">
                                    {row.unavailableReason}
                                  </p>
                                )}
                                {row.completed_at && (
                                  <p>
                                    Передан: {schoolMoment(row.completed_at)}
                                  </p>
                                )}
                                {row.returned_at && (
                                  <p>
                                    Вернулся: {schoolMoment(row.returned_at)}
                                  </p>
                                )}
                                <div className="handover-actions">
                                  {live &&
                                    row.status === "requested" &&
                                    row.canArrive && (
                                      <button
                                        onClick={() => void act(row, "arrive")}
                                      >
                                        Взрослый прибыл
                                      </button>
                                    )}
                                  {live && staff && (
                                    <>
                                      {row.status !== "ready" && (
                                        <button
                                          onClick={() => void act(row, "ready")}
                                        >
                                          Ребёнок готов
                                        </button>
                                      )}
                                      <button
                                        className="handover-primary"
                                        onClick={() =>
                                          void openEditor("transfer", row)
                                        }
                                      >
                                        Передать ребёнка
                                      </button>
                                    </>
                                  )}
                                  {pendingPickup(row) && row.canCancel && (
                                    <button
                                      onClick={() =>
                                        void openEditor("action", row, "cancel")
                                      }
                                    >
                                      Отменить заявку
                                    </button>
                                  )}
                                  {manager && (
                                    <>
                                      {row.status === "completed" &&
                                        row.day === options.today && (
                                          <button
                                            onClick={() =>
                                              void openEditor(
                                                "action",
                                                row,
                                                "returned",
                                              )
                                            }
                                          >
                                            Ребёнок вернулся
                                          </button>
                                        )}
                                      <button
                                        onClick={() =>
                                          void openEditor("action", row, "note")
                                        }
                                      >
                                        Примечание
                                      </button>
                                    </>
                                  )}
                                  <button
                                    onClick={() => history(row, "pickup")}
                                  >
                                    История заявки
                                  </button>
                                </div>
                              </article>
                            );
                          })}
                        </div>
                      </>
                    )}
                    {tab === "history" && (
                      <>
                        {(historyFilter.pickupId || historyFilter.adultId) && (
                          <p>
                            История выбранной записи.{" "}
                            <button
                              onClick={() => {
                                resetView();
                                setHistoryFilter({ pickupId: "", adultId: "" });
                              }}
                            >
                              Показать всю историю
                            </button>
                          </p>
                        )}
                        <ol className="handover-history">
                          {data.items.map((row: any) => (
                            <li key={row.seq}>
                              <strong>
                                {handoverActions[row.action] || row.action}
                              </strong>
                              <p>
                                {schoolMoment(row.created)}
                                {row.actor_name ? ` · ${row.actor_name}` : ""}
                              </p>
                              {row.student_id && (
                                <p>
                                  {options.students.find(
                                    (s: any) => s.id === row.student_id,
                                  )?.name || "Ученик из архивной записи"}
                                </p>
                              )}
                              {row.reason && (
                                <p className="handover-preserve">
                                  {row.reason}
                                </p>
                              )}
                            </li>
                          ))}
                        </ol>
                        <div className="handover-actions">
                          {before && (
                            <button onClick={() => void load()}>
                              К последним событиям
                            </button>
                          )}
                          {data.nextBefore && (
                            <button
                              onClick={() => void load(0, data.nextBefore)}
                            >
                              Более ранние события
                            </button>
                          )}
                        </div>
                      </>
                    )}
                    {tab !== "settings" &&
                      !data.items.length &&
                      !loading &&
                      !error && (
                        <p className="handover-empty">
                          По выбранным условиям записей пока нет.
                        </p>
                      )}
                    {["adults", "pickups"].includes(tab) && (
                      <div className="handover-actions">
                        {offset > 0 && (
                          <button
                            onClick={() => void load(Math.max(0, offset - 50))}
                          >
                            Предыдущие 50
                          </button>
                        )}
                        {data.nextOffset !== null &&
                          data.nextOffset !== undefined && (
                            <button onClick={() => void load(data.nextOffset)}>
                              Следующие 50
                            </button>
                          )}
                      </div>
                    )}
                  </>
                )}
              </>
            )}
          </>
        )}
      </fieldset>
      {loading && <p role="status">Загружаем доступные записи…</p>}
      {busy && <p role="status">Сохраняем действие…</p>}
      {!options && !loading && (
        <button onClick={() => void load()}>Повторить загрузку</button>
      )}
    </dialog>
  );
}

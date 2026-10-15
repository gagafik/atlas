import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import { displayTime, schoolInput } from "./meetings";
import {
  codes,
  absenceCodes,
  statuses,
  actions,
  duration,
  blankDay,
} from "./staffTimeTypes";
import type {
  Day,
  DayDocument,
  Leave,
  Report,
  Options,
} from "./staffTimeTypes";
import "./StaffTime.css";

export default function StaffTime({
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
  const [month, setMonth] = useState(
      schoolInput(new Date().toISOString()).slice(0, 7),
    ),
    [employee, setEmployee] = useState(user.id),
    [archive, setArchive] = useState(0);
  const [options, setOptions] = useState<Options | null>(null),
    [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<{
      id?: string;
      version: number;
      onDate: string;
      document: DayDocument;
    } | null>(null),
    [dirty, setDirty] = useState(false);
  const [leaveDraft, setLeaveDraft] = useState<{
    startDate: string;
    endDate: string;
    code: string;
    startTime: string;
    endTime: string;
    note: string;
  } | null>(null);
  const [preview, setPreview] = useState<{
    kind: "leave" | "period";
    id: string;
    version: number;
    previewHash: string;
    conflictCount?: number;
    conflicts?: { date: string; reason: string }[];
    employees?: number;
    approvedDays?: number;
    pendingDays?: number;
    pendingRequests?: number;
  } | null>(null);
  const [history, setHistory] = useState<{
    entity: string;
    id: string;
    items: {
      seq: number;
      created: string;
      actor: string;
      action: string;
      reason: string;
      document: unknown;
    }[];
    nextBefore: number | null;
  } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  const canLeave = () =>
    !dirty ||
    window.confirm("Есть несохранённые изменения. Продолжить без сохранения?");
  const clear = () => {
    setEditing(null);
    setLeaveDraft(null);
    setDirty(false);
    setPreview(null);
    setHistory(null);
  };
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  async function load() {
    const n = ++sequence.current;
    const [o, r] = await Promise.all([
      api("staff-time/options", workspace),
      api(
        `staff-time/report?month=${month}&employee=${encodeURIComponent(employee)}&archive=${archive}`,
        workspace,
      ),
    ]);
    if (n === sequence.current) {
      setOptions(o);
      setReport(r);
    }
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
      if ([401, 403, 404].includes(e.status)) {
        setReport(null);
        setOptions(null);
        clear();
      }
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    setReport(null);
    setOptions(null);
    clear();
    void run(load);
    return () => {
      ++sequence.current;
    };
  }, [workspace, month, employee, archive]);
  async function mutate(path: string, body: object, method = "POST") {
    const key = JSON.stringify([workspace, path, body, method]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const result = await api("staff-time/" + path, workspace, method, {
      ...body,
      requestId: replay.current.id,
    });
    replay.current = { key: "", id: "" };
    return result;
  }
  async function changed() {
    clear();
    await load();
    setNotice("Изменения сохранены.");
  }
  const open = report?.period.status === "open" && !archive,
    admin = !!options?.canManage,
    canEdit = open && (admin || options?.settings.selfReport);
  async function dayAction(item: Day, action: string) {
    if (!canLeave()) return;
    const reason = ["return", "reopen", "void"].includes(action)
      ? window.prompt("Причина изменения")
      : "";
    if (reason === null) return;
    await run(async () => {
      await mutate(`days/${item.id}/action`, {
        version: item.version,
        action,
        reason,
      });
      await changed();
    });
  }
  async function leaveAction(item: Leave, action: string) {
    if (!canLeave()) return;
    if (action === "approve") {
      await run(async () => {
        const p = await api(`staff-time/leave/${item.id}/preview`, workspace);
        setPreview({ ...p, kind: "leave", id: item.id, version: item.version });
      });
      return;
    }
    const reason = ["reject", "cancel"].includes(action)
      ? window.prompt("Причина решения")
      : "";
    if (reason === null) return;
    await run(async () => {
      await mutate(`leave/${item.id}/action`, {
        version: item.version,
        action,
        reason,
      });
      await changed();
    });
  }
  async function showHistory(entity: string, id: string, more = false) {
    await run(async () => {
      const r = await api(
        `staff-time/history/${entity}/${id}${more && history?.nextBefore ? "?before=" + history.nextBefore : ""}`,
        workspace,
      );
      setHistory({
        entity,
        id,
        items: more ? [...history!.items, ...r.items] : r.items,
        nextBefore: r.nextBefore,
      });
    });
  }
  function editDay(item?: Day) {
    if (!canLeave()) return;
    clear();
    setEditing(
      item
        ? {
            id: item.id,
            version: item.version,
            onDate: item.onDate,
            document: {
              fullDay: item.document.fullDay,
              segments: item.document.segments.map((s) => ({ ...s })),
              note: item.document.note,
            },
          }
        : {
            version: 0,
            onDate:
              month == options?.today.slice(0, 7)
                ? options.today
                : month + "-01",
            document: blankDay(),
          },
    );
  }
  const updateDoc = (document: DayDocument) => {
    if (editing) setEditing({ ...editing, document });
    setDirty(true);
  };
  const switchValue = (change: () => void) => {
    if (canLeave()) {
      clear();
      change();
    }
  };
  const summary = report?.summary.reduce(
    (a, p) => ({
      work: a.work + p.workMinutes,
      pending: a.pending + p.pendingDays + p.pendingRequests,
      days: a.days + p.recordedDays,
    }),
    { work: 0, pending: 0, days: 0 },
  );
  return (
    <dialog
      ref={dialog}
      className="staff-time"
      aria-labelledby="staff-time-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && canLeave()) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="staff-time-title">Табель и отсутствия</h2>
          <p>Фактически отработанное время и согласование отсутствий</p>
        </div>
        <button
          disabled={busy}
          onClick={() => {
            if (canLeave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      {error && (
        <p role="alert" className="time-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <fieldset disabled={busy}>
        <div className="time-controls">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) =>
                switchValue(() => {
                  setWorkspace(e.target.value);
                  setArchive(0);
                  setEmployee(user.id);
                })
              }
            >
              {spaces.map((s) => (
                <option key={s} value={s}>
                  {s === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Месяц
            <input
              type="month"
              required
              value={month}
              onChange={(e) => {
                if (/^\d{4}-\d{2}$/.test(e.target.value))
                  switchValue(() => {
                    setMonth(e.target.value);
                    setArchive(0);
                  });
              }}
            />
          </label>
          <label>
            Сотрудник
            <select
              value={employee}
              onChange={(e) => switchValue(() => setEmployee(e.target.value))}
            >
              {admin && <option value="">Все сотрудники</option>}
              {options?.employees.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
              {employee &&
                options &&
                !options.employees.some((p) => p.id === employee) && (
                  <option value={employee}>Архивный сотрудник</option>
                )}
            </select>
          </label>
          <label>
            Версия
            <select
              value={archive}
              onChange={(e) =>
                switchValue(() => setArchive(Number(e.target.value)))
              }
            >
              <option value="0">
                Текущая ·{" "}
                {report?.period.status === "closed" ? "закрыта" : "открыта"}
              </option>
              {report?.archives.map((v) => (
                <option key={v} value={v}>
                  Архив №{v}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              if (canLeave()) {
                clear();
                void run(load);
              }
            }}
          >
            Обновить
          </button>
          {report && (
            <a
              href={`/api/staff-time/export?workspace=${encodeURIComponent(workspace)}&month=${month}&employee=${encodeURIComponent(employee)}&archive=${archive}`}
              download
            >
              Скачать CSV
            </a>
          )}
        </div>
        {summary && (
          <div className="time-stats">
            <strong>{duration(summary.work)} подтверждено</strong>
            <span>{summary.days} заполненных дней</span>
            <span>{summary.pending} записей на проверке</span>
          </div>
        )}
        <p className="time-help">
          Дни без записей остаются незаполненными. Перерыв между интервалами не
          входит в часы. Отсутствие на весь день не пересчитывается в условные 8
          часов. Заявки на отсутствие учитываются отдельно от фактического
          табеля.
        </p>
        {admin && options && (
          <details>
            <summary>Настройки и закрытие месяца</summary>
            <p>
              Сейчас{" "}
              {options.settings.selfReport
                ? "сотрудники могут подавать часы на утверждение"
                : "табель ведёт администрация, сотрудники видят свои записи"}
              .
            </p>
            <div className="time-actions">
              <button
                onClick={() => {
                  const reason = window.prompt(
                    "Причина изменения порядка учёта",
                  );
                  if (reason)
                    void run(async () => {
                      await mutate(
                        "settings",
                        {
                          version: options.settings.version,
                          selfReport: !options.settings.selfReport,
                          reason,
                        },
                        "PUT",
                      );
                      await changed();
                    });
                }}
              >
                {options.settings.selfReport
                  ? "Отключить подачу часов"
                  : "Разрешить сотрудникам подавать часы"}
              </button>
              {!archive && report?.period.status === "open" && (
                <button
                  onClick={() =>
                    void run(async () => {
                      const p = await api(
                        `staff-time/periods/${month}/preview`,
                        workspace,
                      );
                      setPreview({ ...p, kind: "period", id: month });
                    })
                  }
                >
                  Проверить перед закрытием
                </button>
              )}
              {!archive && report?.period.status === "closed" && (
                <button
                  onClick={() => {
                    const reason = window.prompt(
                      "Причина открытия месяца для исправлений",
                    );
                    if (reason)
                      void run(async () => {
                        await mutate(`periods/${month}/action`, {
                          version: report.period.version,
                          action: "reopen",
                          reason,
                        });
                        await changed();
                      });
                  }}
                >
                  Открыть месяц для исправлений
                </button>
              )}
              <button onClick={() => void showHistory("period", month)}>
                История месяца
              </button>
              <button onClick={() => void showHistory("settings", "settings")}>
                История настроек
              </button>
            </div>
          </details>
        )}
        {preview && (
          <section className="time-preview">
            <h3>
              {preview.kind === "leave"
                ? "Проверка отсутствия"
                : "Закрытие всего месяца"}
            </h3>
            {preview.kind === "leave" ? (
              <>
                <p>Пересечений: {preview.conflictCount}</p>
                {preview.conflicts?.map((c, i) => (
                  <p key={i}>
                    {c.date}: {c.reason}
                  </p>
                ))}
              </>
            ) : (
              <>
                <p>
                  Сотрудников с записями: {preview.employees}. Подтверждённых
                  дней: {preview.approvedDays}. Незавершённых записей:{" "}
                  {(preview.pendingDays || 0) + (preview.pendingRequests || 0)}.
                </p>
                <p>
                  Закрытие фиксирует имеющиеся записи всех сотрудников. Оно не
                  подтверждает полноту табеля. Позже администратор сможет
                  открыть месяц с указанием причины.
                </p>
              </>
            )}
            <button
              disabled={
                !!(
                  preview.conflictCount ||
                  preview.pendingDays ||
                  preview.pendingRequests
                )
              }
              onClick={() =>
                void run(async () => {
                  await mutate(
                    preview.kind === "leave"
                      ? `leave/${preview.id}/action`
                      : `periods/${preview.id}/action`,
                    {
                      version: preview.version,
                      action: preview.kind === "leave" ? "approve" : "close",
                      previewHash: preview.previewHash,
                    },
                  );
                  await changed();
                })
              }
            >
              {preview.kind === "leave"
                ? "Согласовать отсутствие"
                : "Закрыть месяц"}
            </button>{" "}
            <button onClick={() => setPreview(null)}>Отмена</button>
          </section>
        )}
        {!employee && report && (
          <div className="time-table">
            <table>
              <thead>
                <tr>
                  <th>Сотрудник</th>
                  <th>Подтверждённые часы</th>
                  <th>Дни с записью</th>
                  <th>На проверке</th>
                  <th>Дни согласованного отсутствия</th>
                </tr>
              </thead>
              <tbody>
                {report.summary.map((p) => (
                  <tr key={p.employeeId}>
                    <td>
                      <button
                        onClick={() =>
                          switchValue(() => setEmployee(p.employeeId))
                        }
                      >
                        {p.name}
                      </button>
                    </td>
                    <td>{duration(p.workMinutes)}</td>
                    <td>{p.recordedDays}</td>
                    <td>{p.pendingDays + p.pendingRequests}</td>
                    <td>{p.approvedLeaveDays}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="time-actions">
          {employee && canEdit && (
            <button onClick={() => editDay()}>Добавить день</button>
          )}
          {employee && open && (
            <button
              onClick={() => {
                if (!canLeave()) return;
                clear();
                setLeaveDraft({
                  startDate: options?.today || month + "-01",
                  endDate: options?.today || month + "-01",
                  code: "leave",
                  startTime: "",
                  endTime: "",
                  note: "",
                });
              }}
            >
              Заявка на отсутствие
            </button>
          )}
        </div>
        {editing && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await mutate(
                  "days",
                  {
                    employeeId: employee,
                    onDate: editing.onDate,
                    version: editing.version,
                    document: editing.document,
                  },
                  "PUT",
                );
                await changed();
              });
            }}
          >
            <h3>{editing.id ? "Исправление дня" : "Новая запись"}</h3>
            <div className="time-controls">
              <label>
                Дата
                <input
                  type="date"
                  required
                  max={options?.today}
                  value={editing.onDate}
                  disabled={!!editing.id}
                  onChange={(e) => {
                    setEditing({ ...editing, onDate: e.target.value });
                    setDirty(true);
                  }}
                />
              </label>
              <label>
                Вид записи
                <select
                  value={editing.document.fullDay || "intervals"}
                  onChange={(e) =>
                    updateDoc({
                      ...editing.document,
                      fullDay:
                        e.target.value === "intervals" ? null : e.target.value,
                      segments:
                        e.target.value === "intervals"
                          ? blankDay().segments
                          : [],
                    })
                  }
                >
                  <option value="intervals">Интервалы дня</option>
                  {absenceCodes.map((c) => (
                    <option key={c} value={c}>
                      {codes[c]} · весь день
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {editing.document.segments.map((segment, i) => (
              <div className="time-controls time-segment" key={i}>
                <label>
                  Начало
                  <input
                    aria-label={`Начало интервала ${i + 1}`}
                    type="time"
                    required
                    value={segment.start}
                    onChange={(e) =>
                      updateDoc({
                        ...editing.document,
                        segments: editing.document.segments.map((s, j) =>
                          j === i ? { ...s, start: e.target.value } : s,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Окончание
                  <input
                    aria-label={`Окончание интервала ${i + 1}`}
                    required
                    pattern="([01][0-9]|2[0-3]):[0-5][0-9]|24:00"
                    placeholder="17:00"
                    value={segment.end}
                    onChange={(e) =>
                      updateDoc({
                        ...editing.document,
                        segments: editing.document.segments.map((s, j) =>
                          j === i ? { ...s, end: e.target.value } : s,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Учёт
                  <select
                    value={segment.code}
                    onChange={(e) =>
                      updateDoc({
                        ...editing.document,
                        segments: editing.document.segments.map((s, j) =>
                          j === i ? { ...s, code: e.target.value } : s,
                        ),
                      })
                    }
                  >
                    {Object.entries(codes).map(([c, l]) => (
                      <option key={c} value={c}>
                        {l}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() =>
                    updateDoc({
                      ...editing.document,
                      segments: editing.document.segments.filter(
                        (_, j) => j !== i,
                      ),
                    })
                  }
                >
                  Убрать интервал {i + 1}
                </button>
              </div>
            ))}
            {!editing.document.fullDay && (
              <>
                <button
                  type="button"
                  disabled={editing.document.segments.length >= 12}
                  onClick={() =>
                    updateDoc({
                      ...editing.document,
                      segments: [
                        ...editing.document.segments,
                        { start: "09:00", end: "17:00", code: "work" },
                      ],
                    })
                  }
                >
                  Ещё интервал
                </button>
                <p>
                  Можно указать 24:00 как окончание. Ночную смену разделите на
                  две даты.
                </p>
              </>
            )}
            <label>
              Примечание
              <textarea
                maxLength={1000}
                value={editing.document.note}
                onChange={(e) =>
                  updateDoc({ ...editing.document, note: e.target.value })
                }
              />
            </label>
            <div className="time-actions">
              <button>Сохранить черновик</button>
              <button
                type="button"
                onClick={() => {
                  if (canLeave()) clear();
                }}
              >
                Отмена
              </button>
            </div>
          </form>
        )}
        {leaveDraft && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await mutate("leave", {
                  ...leaveDraft,
                  employeeId: employee,
                  startTime: leaveDraft.startTime || null,
                  endTime: leaveDraft.endTime || null,
                });
                await changed();
              });
            }}
          >
            <h3>Заявка на отсутствие</h3>
            <p>
              До 31 календарного дня. Время применяется к каждому дню периода;
              оставьте оба поля пустыми для полного дня.
            </p>
            <div className="time-controls">
              {(["startDate", "endDate", "startTime", "endTime"] as const).map(
                (k, i) => (
                  <label key={k}>
                    {
                      [
                        "С даты",
                        "По дату",
                        "Начало отсутствия",
                        "Конец отсутствия",
                      ][i]
                    }
                    <input
                      type={i < 2 ? "date" : i === 2 ? "time" : "text"}
                      required={i < 2}
                      pattern={
                        i === 3
                          ? "([01][0-9]|2[0-3]):[0-5][0-9]|24:00"
                          : undefined
                      }
                      value={leaveDraft[k]}
                      onChange={(e) => {
                        setLeaveDraft({ ...leaveDraft, [k]: e.target.value });
                        setDirty(true);
                      }}
                    />
                  </label>
                ),
              )}
              <label>
                Причина
                <select
                  value={leaveDraft.code}
                  onChange={(e) => {
                    setLeaveDraft({ ...leaveDraft, code: e.target.value });
                    setDirty(true);
                  }}
                >
                  {absenceCodes.map((c) => (
                    <option key={c} value={c}>
                      {codes[c]}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              Примечание
              <textarea
                maxLength={1000}
                value={leaveDraft.note}
                onChange={(e) => {
                  setLeaveDraft({ ...leaveDraft, note: e.target.value });
                  setDirty(true);
                }}
              />
            </label>
            <div className="time-actions">
              <button>Подать заявку</button>
              <button
                type="button"
                onClick={() => {
                  if (canLeave()) clear();
                }}
              >
                Отмена
              </button>
            </div>
          </form>
        )}
        {employee && report && (
          <>
            <h3>Записи по дням</h3>
            {!report.days.length && (
              <p>
                В этом месяце нет записей. Рабочие часы не назначаются
                автоматически.
              </p>
            )}
            {report.days.map((item) => (
              <article key={item.id}>
                <h4>
                  {item.onDate} · {statuses[item.status]}
                </h4>
                <p>
                  {item.document.fullDay
                    ? `${codes[item.document.fullDay]} · весь день`
                    : item.document.segments
                        .map((s) => `${s.start}–${s.end} ${codes[s.code]}`)
                        .join("; ")}
                </p>
                <p>
                  Работа: {duration(item.totals.workMinutes)}
                  {item.status !== "approved"
                    ? " (не включено в подтверждённый итог)"
                    : ""}
                </p>
                {item.document.note && <p>{item.document.note}</p>}
                <div className="time-actions">
                  {canEdit &&
                    ["draft", "returned", "void"].includes(item.status) && (
                      <button onClick={() => editDay(item)}>Изменить</button>
                    )}
                  {canEdit && ["draft", "returned"].includes(item.status) && (
                    <button onClick={() => void dayAction(item, "submit")}>
                      На утверждение
                    </button>
                  )}
                  {open && item.status === "submitted" && (
                    <button onClick={() => void dayAction(item, "withdraw")}>
                      Отозвать
                    </button>
                  )}
                  {open &&
                    admin &&
                    ["draft", "submitted"].includes(item.status) && (
                      <button onClick={() => void dayAction(item, "approve")}>
                        Подтвердить часы
                      </button>
                    )}
                  {open && admin && item.status === "submitted" && (
                    <button onClick={() => void dayAction(item, "return")}>
                      Вернуть
                    </button>
                  )}
                  {open && admin && item.status === "approved" && (
                    <button onClick={() => void dayAction(item, "reopen")}>
                      Открыть для исправлений
                    </button>
                  )}
                  {open && admin && item.status !== "void" && (
                    <button onClick={() => void dayAction(item, "void")}>
                      Аннулировать
                    </button>
                  )}
                  <button onClick={() => void showHistory("day", item.id)}>
                    История дня
                  </button>
                </div>
              </article>
            ))}
            <h3>Согласование отсутствий</h3>
            {!report.requests.length && <p>Заявок за этот месяц нет.</p>}
            {report.requests.map((item) => (
              <article key={item.id}>
                <h4>
                  {item.startDate} — {item.endDate} · {statuses[item.status]}
                </h4>
                <p>
                  {codes[item.code]} ·{" "}
                  {item.startTime
                    ? `${item.startTime}–${item.endTime} каждый день`
                    : "полные дни"}
                </p>
                {item.note && <p>{item.note}</p>}
                <div className="time-actions">
                  {open && item.status === "submitted" && (
                    <>
                      <button
                        onClick={() => void leaveAction(item, "withdraw")}
                      >
                        Отозвать заявку
                      </button>
                      {admin && (
                        <>
                          <button
                            onClick={() => void leaveAction(item, "approve")}
                          >
                            Проверить и согласовать
                          </button>
                          <button
                            onClick={() => void leaveAction(item, "reject")}
                          >
                            Отклонить
                          </button>
                        </>
                      )}
                    </>
                  )}
                  {open && admin && item.status === "approved" && (
                    <button onClick={() => void leaveAction(item, "cancel")}>
                      Отменить согласование
                    </button>
                  )}
                  <button onClick={() => void showHistory("leave", item.id)}>
                    История заявки
                  </button>
                </div>
              </article>
            ))}
          </>
        )}
        {history && (
          <section className="time-history">
            <h3>История изменений</h3>
            {!history.items.length && <p>Изменений пока нет.</p>}
            {history.items.map((h) => (
              <article key={h.seq}>
                <strong>{actions[h.action] || h.action}</strong>
                <p>
                  {displayTime(h.created)} · {h.actor}
                </p>
                {h.reason && <p>{h.reason}</p>}
                <details>
                  <summary>Сохранённая запись</summary>
                  <HistoryRecord value={h.document} />
                </details>
              </article>
            ))}
            {history.nextBefore && (
              <button
                onClick={() =>
                  void showHistory(history.entity, history.id, true)
                }
              >
                Ранее
              </button>
            )}{" "}
            <button onClick={() => setHistory(null)}>Скрыть историю</button>
          </section>
        )}
      </fieldset>
      {busy && <p role="status">Сохраняю или загружаю данные…</p>}
    </dialog>
  );
}

function HistoryRecord({ value }: { value: unknown }) {
  const record = value as Partial<Day & Leave> & {
    selfReport?: boolean;
    month?: string;
  };
  if (record.month)
    return (
      <p>
        {record.month} ·{" "}
        {record.status === "closed" ? "Месяц закрыт" : "Месяц открыт"}
      </p>
    );
  if (typeof record.selfReport === "boolean")
    return (
      <p>
        {record.selfReport
          ? "Сотрудники могут подавать часы"
          : "Табель ведёт администрация"}
      </p>
    );
  const document = record.document;
  return (
    <div>
      <p>
        {document?.employeeName || record.employeeName} ·{" "}
        {statuses[record.status || ""]}
      </p>
      {record.onDate && (
        <p>
          {record.onDate}:{" "}
          {document?.fullDay
            ? `${codes[document.fullDay]} · весь день`
            : document?.segments
                .map((s) => `${s.start}–${s.end} ${codes[s.code]}`)
                .join("; ")}
        </p>
      )}
      {record.startDate && (
        <p>
          {record.startDate} — {record.endDate} · {codes[record.code || ""]} ·{" "}
          {record.startTime
            ? `${record.startTime}–${record.endTime}`
            : "полные дни"}
        </p>
      )}
      {(document?.note || record.note) && (
        <p>{document?.note || record.note}</p>
      )}
    </div>
  );
}

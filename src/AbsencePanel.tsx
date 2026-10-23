import { useEffect, useState } from "react";
import { api } from "./api";

type FileItem = { id: string; filename: string; size: number };
type Item = {
  id: string;
  studentId: string;
  studentName: string;
  version: number;
  status: string;
  start: string;
  end: string;
  created: string;
  reason?: string;
  files?: FileItem[];
  canReview?: boolean;
  canWithdraw?: boolean;
  periodLocks?: string[];
};
type Options = {
  parent: boolean;
  admin: boolean;
  canReview: boolean;
  students: { id: string; name: string }[];
  yearStart: string;
  yearEnd: string;
  config?: { version: number; assignments: Record<string, string[]> };
  classes?: { id: string; name: string }[];
  teachers?: { id: string; name: string; login: string }[];
};
type History = {
  id: number;
  version: number;
  created: string;
  actor: string;
  action: string;
  comment: string;
  batchId?: string | null;
};
type BulkPreview = {
  id: string;
  expires: string;
  action: string;
  comment: string;
  items: (Item & { nextStatus: string })[];
};
const actions: Record<string, string> = {
  approve: "Принять",
  reject: "Отклонить",
  revoke: "Отменить принятое решение",
};
const labels: Record<string, string> = {
  pending: "На рассмотрении",
  approved: "Принято",
  rejected: "Отклонено",
  withdrawn: "Отозвано родителем",
  revoked: "Решение отменено школой",
  submitted: "Отправлено",
};

export default function AbsencePanel({ workspace }: { workspace: string }) {
  const [expanded, setExpanded] = useState(false),
    [options, setOptions] = useState<Options | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [items, setItems] = useState<Item[]>([]),
    [next, setNext] = useState<number | null>(null),
    [filter, setFilter] = useState("all"),
    [selected, setSelected] = useState<Item | null>(null);
  const [history, setHistory] = useState<History[]>([]),
    [historyNext, setHistoryNext] = useState<number | null>(null),
    [comment, setComment] = useState("");
  const [student, setStudent] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [from, setFrom] = useState("00:00"),
    [to, setTo] = useState("23:59"),
    [reason, setReason] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID()),
    [files, setFiles] = useState<FileItem[]>([]),
    [available, setAvailable] = useState<FileItem[]>([]);
  const [preview, setPreview] = useState<
    | {
        date: string;
        start: string;
        end: string;
        subject: string;
        group: string;
      }[]
    | null
  >(null);
  const [decisionRequest, setDecisionRequest] = useState(() =>
    crypto.randomUUID(),
  );
  const [bulkIds, setBulkIds] = useState<string[]>([]),
    [bulkAction, setBulkAction] = useState("approve"),
    [bulkComment, setBulkComment] = useState(""),
    [bulkPreview, setBulkPreview] = useState<BulkPreview | null>(null),
    [bulkRequest, setBulkRequest] = useState(() => crypto.randomUUID());
  const clearBulkPreview = () => {
    setBulkPreview(null);
    setBulkRequest(crypto.randomUUID());
  };
  const clearBulk = () => {
    clearBulkPreview();
    setBulkIds([]);
  };
  const bulkEligible = (item: Item) =>
    item.status === (bulkAction === "revoke" ? "approved" : "pending");
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const loadOptions = async () => {
    const r: Options = await api("journal/absences/options", workspace);
    setOptions(r);
    setStudent((old) => old || r.students[0]?.id || "");
    setStart((old) => old || r.yearStart);
    setEnd((old) => old || r.yearStart);
  };
  const list = async (before?: number) => {
    const r = await api(
      `journal/absences?status=${filter}${before ? `&before=${before}` : ""}`,
      workspace,
    );
    setItems((old) => (before ? [...old, ...r.items] : r.items));
    setNext(r.nextBefore);
    if (!before) clearBulk();
  };
  const open = async (id: string) => {
    const r = await api(`journal/absences/${id}`, workspace);
    setSelected(r);
    setHistory([]);
    setHistoryNext(null);
    setComment("");
    setDecisionRequest(crypto.randomUUID());
  };
  const changes = async (before?: number) => {
    if (!selected) return;
    const r = await api(
      `journal/absences/${selected.id}/history${before ? `?before=${before}` : ""}`,
      workspace,
    );
    setHistory((old) => (before ? [...old, ...r.items] : r.items));
    setHistoryNext(r.nextBefore);
  };
  useEffect(() => {
    if (!expanded) return;
    clearBulk();
    let live = true;
    setBusy(true);
    setError("");
    Promise.all([
      api("journal/absences/options", workspace),
      api(`journal/absences?status=${filter}`, workspace),
    ])
      .then(([o, r]) => {
        if (!live) return;
        setOptions(o);
        setStudent((old) => old || o.students[0]?.id || "");
        setStart((old) => old || o.yearStart);
        setEnd((old) => old || o.yearStart);
        setItems(r.items);
        setNext(r.nextBefore);
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setBusy(false);
      });
    return () => {
      live = false;
    };
  }, [expanded, workspace, filter]);
  const payload = () => ({
    requestId,
    studentId: student,
    startDate: start,
    endDate: end,
    startTime: from,
    endTime: to,
    reason,
    fileIds: files.map((f) => f.id),
  });
  const reset = () => {
    setRequestId(crypto.randomUUID());
    setFiles([]);
    setAvailable([]);
    setReason("");
    setPreview(null);
  };
  const upload = async (file: File) => {
    if (files.length >= 5)
      throw new Error("К заявлению можно приложить до пяти файлов.");
    const query = new URLSearchParams({
      workspace,
      studentId: student,
      draftId: requestId,
      filename: file.name,
    });
    const r = await fetch(`/api/journal/absence-files?${query}`, {
      method: "POST",
      headers: {
        "X-Atlas-Request": "1",
        "Content-Type": "application/octet-stream",
      },
      body: file,
    });
    const value = await r.json();
    if (!r.ok)
      throw new Error(
        typeof value.detail === "string"
          ? value.detail
          : "Не удалось загрузить файл.",
      );
    setFiles((old) => [...old, value]);
  };
  const decide = async (action: string) => {
    if (
      !selected ||
      !window.confirm(
        action === "approve"
          ? "Принять объяснительную? Отсутствие на полностью включённых в период уроках будет учитываться как уважительная причина."
          : "Сохранить это решение с указанным объяснением?",
      )
    )
      return;
    const r = await api(
      `journal/absences/${selected.id}/decision`,
      workspace,
      "POST",
      {
        requestId: decisionRequest,
        version: selected.version,
        action,
        comment,
      },
    );
    setSelected(r);
    setComment("");
    setDecisionRequest(crypto.randomUUID());
    await changes();
    await list();
    setNotice(
      "Решение сохранено и учитывается при следующем просмотре посещаемости.",
    );
  };
  const fileUrl = (id: string) =>
    `/api/journal/absence-files/${id}?workspace=${encodeURIComponent(workspace)}`;
  const bulkDone = async (count: number) => {
    clearBulk();
    setBulkComment("");
    setSelected(null);
    setHistory([]);
    setNotice(
      `Сохранено решений: ${count}. Они учитываются при следующем просмотре посещаемости.`,
    );
    await list();
  };
  const prepareBulk = async () => {
    const r = await api("journal/absences/bulk/preview", workspace, "POST", {
      requestId: bulkRequest,
      items: items
        .filter((item) => bulkIds.includes(item.id))
        .map(({ id, version }) => ({ id, version })),
      action: bulkAction,
      comment: bulkComment,
    });
    if (r.status === "applied") await bulkDone(r.count);
    else setBulkPreview(r);
  };
  const applyBulk = async () => {
    if (
      !bulkPreview ||
      !window.confirm(
        `Применить решение «${actions[bulkPreview.action]}» ко всем ${bulkPreview.items.length} заявлениям из предпросмотра?`,
      )
    )
      return;
    const r = await api(
      `journal/absences/bulk/${bulkPreview.id}/apply`,
      workspace,
      "POST",
    );
    await bulkDone(r.count);
  };
  return (
    <details
      className="absence-panel"
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Отсутствия и объяснительные</summary>
      {expanded && (
        <>
          <p>
            Родитель сообщает причину; школа рассматривает заявление. Текст и
            файлы доступны автору с действующим доступом к ребёнку,
            администратору и назначенным ответственным за класс. Другим
            представителям и предметникам виден только результат в посещаемости.
          </p>
          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          {options?.admin && (
            <p>
              Назначение ответственных: «Пользователи и доступ» → «Права работы
              с классами». Рассмотрение объяснительных, сводная посещаемость и
              контроль журналов выдаются отдельно.
            </p>
          )}
          {options && !options.parent && !options.canReview && (
            <p>
              Администратор пока не назначил вас ответственным за заявления
              класса.
            </p>
          )}
          {options?.parent && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (
                  !window.confirm(
                    "Передать школе причину отсутствия и выбранные файлы? После отправки их нельзя заменить; ожидающее заявление можно отозвать.",
                  )
                )
                  return;
                void run(async () => {
                  const r = await api(
                    "journal/absences",
                    workspace,
                    "POST",
                    payload(),
                  );
                  setSelected(r);
                  setHistory([]);
                  reset();
                  await list();
                  setNotice("Заявление отправлено школе.");
                });
              }}
            >
              <h3>Сообщить об отсутствии</h3>
              <fieldset disabled={busy} className="access-fields">
                <label className="field">
                  <span>Ребёнок</span>
                  <select
                    value={student}
                    onChange={(e) => {
                      if (
                        (reason || files.length) &&
                        !window.confirm(
                          "Начать форму для другого ребёнка? Неотправленный текст будет очищен.",
                        )
                      )
                        return;
                      setStudent(e.target.value);
                      reset();
                    }}
                  >
                    {options.students.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="dated-fields">
                  <label className="field">
                    <span>Первый день отсутствия</span>
                    <input
                      type="date"
                      required
                      min={options.yearStart}
                      max={options.yearEnd}
                      value={start}
                      onChange={(e) => {
                        setStart(e.target.value);
                        setPreview(null);
                      }}
                    />
                  </label>
                  <label className="field">
                    <span>С какого времени</span>
                    <input
                      type="time"
                      required
                      value={from}
                      onChange={(e) => {
                        setFrom(e.target.value);
                        setPreview(null);
                      }}
                    />
                  </label>
                  <label className="field">
                    <span>Последний день отсутствия</span>
                    <input
                      type="date"
                      required
                      min={start}
                      max={options.yearEnd}
                      value={end}
                      onChange={(e) => {
                        setEnd(e.target.value);
                        setPreview(null);
                      }}
                    />
                  </label>
                  <label className="field">
                    <span>До какого времени</span>
                    <input
                      type="time"
                      required
                      value={to}
                      onChange={(e) => {
                        setTo(e.target.value);
                        setPreview(null);
                      }}
                    />
                  </label>
                </div>
                <p>
                  Время школы — Asia/Qyzylorda. Для полных дней оставьте
                  00:00–23:59. Период непрерывный, до 31 дня; учитываются только
                  полностью включённые в него уроки. Перенос урока за пределы
                  периода отменяет применение объяснительной к нему.
                </p>
                <label className="field">
                  <span>Причина отсутствия</span>
                  <textarea
                    required
                    maxLength={4000}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Приложить файл (необязательно, до 5 по 10 МиБ)</span>
                  <input
                    type="file"
                    accept=".pdf,.png,.jpg,.jpeg,.txt,.docx,.xlsx,.pptx"
                    disabled={!student || files.length >= 5}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      e.target.value = "";
                      if (f) void run(() => upload(f));
                    }}
                  />
                </label>
                {files.map((f) => (
                  <div className="absence-file" key={f.id}>
                    <a href={fileUrl(f.id)}>{f.filename}</a>
                    <button
                      type="button"
                      onClick={() =>
                        void run(async () => {
                          await api(
                            `journal/absence-files/${f.id}`,
                            workspace,
                            "DELETE",
                          );
                          setFiles((old) => old.filter((x) => x.id !== f.id));
                        })
                      }
                    >
                      Удалить неотправленный файл
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  disabled={!student}
                  onClick={() =>
                    void run(async () => {
                      const r = await api(
                        `journal/absence-files?${new URLSearchParams({ studentId: student, draftId: requestId })}`,
                        workspace,
                      );
                      setAvailable(r.items);
                    })
                  }
                >
                  Найти загрузки этой формы
                </button>
                {available
                  .filter((f) => !files.some((x) => x.id === f.id))
                  .map((f) => (
                    <div className="absence-file" key={f.id}>
                      <span>{f.filename}</span>
                      <button
                        type="button"
                        disabled={files.length >= 5}
                        onClick={() => setFiles((old) => [...old, f])}
                      >
                        Приложить найденный файл
                      </button>
                    </div>
                  ))}
                <p>
                  Неотправленный текст не сохраняется после закрытия формы.
                  Неиспользованные файлы очищаются через сутки при следующих
                  загрузках.
                </p>
                <div className="dated-toolbar">
                  <button
                    type="button"
                    disabled={!student || !reason.trim()}
                    onClick={() =>
                      void run(async () => {
                        const r = await api(
                          "journal/absences/preview",
                          workspace,
                          "POST",
                          payload(),
                        );
                        setPreview(r.lessons);
                      })
                    }
                  >
                    Показать уроки в периоде
                  </button>
                  <button
                    className="primary"
                    type="submit"
                    disabled={!student || !reason.trim()}
                  >
                    Отправить объяснительную
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm("Очистить неотправленную форму?"))
                        reset();
                    }}
                  >
                    Новая форма
                  </button>
                </div>
                {preview && (
                  <div className="absence-preview">
                    <p>
                      Уроков по текущему расписанию: {preview.length}. Решение
                      будет действовать в указанный период и для добавленных
                      позднее уроков.
                    </p>
                    {preview.map((r, i) => (
                      <p key={i}>
                        {r.date} · {r.start}–{r.end} · {r.subject} · {r.group}
                      </p>
                    ))}
                  </div>
                )}
              </fieldset>
            </form>
          )}
          <div className="dated-toolbar">
            <label className="field">
              <span>Статус заявления</span>
              <select
                disabled={busy}
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              >
                <option value="all">Все</option>
                {Object.entries(labels)
                  .filter(([id]) => id !== "submitted")
                  .map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
              </select>
            </label>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setSelected(null);
                  await loadOptions();
                  await list();
                })
              }
            >
              Обновить заявления
            </button>
          </div>
          {!busy && !items.length && <p>Заявлений не найдено.</p>}
          {options?.canReview && (
            <section
              className="absence-card"
              aria-label="Массовое рассмотрение"
            >
              <h3>Общее решение по заявлениям</h3>
              <p>
                Выберите до 50 заявлений ниже. Одно объяснение решения
                сохранится в истории каждого. Родители увидят только своё
                заявление.
              </p>
              <fieldset disabled={busy} className="access-fields">
                <label className="field">
                  <span>Общее действие</span>
                  <select
                    value={bulkAction}
                    onChange={(e) => {
                      setBulkAction(e.target.value);
                      clearBulk();
                    }}
                  >
                    {Object.entries(actions).map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Объяснение общего решения</span>
                  <textarea
                    value={bulkComment}
                    maxLength={2000}
                    onChange={(e) => {
                      setBulkComment(e.target.value);
                      clearBulkPreview();
                    }}
                  />
                </label>
                <p>Выбрано: {bulkIds.length} из 50.</p>
                <div className="dated-toolbar">
                  <button
                    disabled={!items.some(bulkEligible)}
                    onClick={() => {
                      setBulkIds(
                        items
                          .filter(bulkEligible)
                          .slice(0, 50)
                          .map((item) => item.id),
                      );
                      clearBulkPreview();
                    }}
                  >
                    Выбрать до 50 загруженных
                  </button>
                  <button disabled={!bulkIds.length} onClick={clearBulk}>
                    Сбросить выбор
                  </button>
                  <button
                    disabled={!bulkIds.length || !bulkComment.trim()}
                    onClick={() => void run(prepareBulk)}
                  >
                    Подготовить предпросмотр
                  </button>
                </div>
              </fieldset>
              {bulkPreview && (
                <section
                  aria-label="Предпросмотр общего решения"
                  className="absence-card"
                >
                  <h3>Проверьте общее решение</h3>
                  <p>
                    <strong>
                      {actions[bulkPreview.action]} · {bulkPreview.items.length}{" "}
                      заявлений
                    </strong>
                  </p>
                  <p className="journal-text">{bulkPreview.comment}</p>
                  <p>
                    {bulkPreview.action === "approve"
                      ? "Отсутствие на полностью включённых в период уроках будет учитываться как уважительная причина, в том числе в журналах, созданных позже."
                      : bulkPreview.action === "revoke"
                        ? "Эти объяснительные перестанут подтверждать уважительную причину. Исходные отметки учителей снова определят посещаемость."
                        : "Заявления будут отклонены. Посещаемость останется прежней."}{" "}
                    Оценки, исходные отметки и публикация журналов сохраняются.
                  </p>
                  <div className="absence-bulk-preview">
                    {bulkPreview.items.map((item) => (
                      <article className="absence-card" key={item.id}>
                        <strong>
                          {item.studentName} · {labels[item.status]} →{" "}
                          {labels[item.nextStatus]}
                        </strong>
                        <p>
                          {item.start.replace("T", " ")} —{" "}
                          {item.end.replace("T", " ")}
                        </p>
                        <p className="journal-text">{item.reason}</p>
                        {item.files?.map((file) => (
                          <p key={file.id}>
                            <a href={fileUrl(file.id)}>{file.filename}</a>
                          </p>
                        ))}
                      </article>
                    ))}
                  </div>
                  <p>
                    Предпросмотр действителен до{" "}
                    {new Date(bulkPreview.expires).toLocaleTimeString("ru-RU")}.
                    Если заявления или доступы изменились, ни одно решение не
                    сохранится. После ошибки соединения можно повторить
                    применение: решения не продублируются.
                  </p>
                  <button disabled={busy} onClick={() => void run(applyBulk)}>
                    Применить ко всем ({bulkPreview.items.length})
                  </button>
                </section>
              )}
            </section>
          )}
          {items.map((item) => (
            <article className="absence-card" key={item.id}>
              {options?.canReview && bulkEligible(item) && (
                <label className="absence-file">
                  <input
                    type="checkbox"
                    checked={bulkIds.includes(item.id)}
                    disabled={
                      busy ||
                      (!bulkIds.includes(item.id) && bulkIds.length >= 50)
                    }
                    onChange={(e) => {
                      setBulkIds((old) =>
                        e.target.checked
                          ? [...old, item.id]
                          : old.filter((id) => id !== item.id),
                      );
                      clearBulkPreview();
                    }}
                  />
                  Включить в общее решение: {item.studentName},{" "}
                  {item.start.replace("T", " ")}
                </label>
              )}
              <strong>
                {item.studentName} · {labels[item.status]}
              </strong>
              <p>
                {item.start.replace("T", " ")} — {item.end.replace("T", " ")}
              </p>
              <button
                disabled={busy}
                onClick={() => void run(() => open(item.id))}
              >
                Открыть заявление
              </button>
            </article>
          ))}
          {next !== null && (
            <button disabled={busy} onClick={() => void run(() => list(next))}>
              Предыдущие заявления
            </button>
          )}
          {selected && (
            <article className="absence-card">
              <h3>
                {selected.studentName} · {labels[selected.status]}
              </h3>
              <p>
                {selected.start.replace("T", " ")} —{" "}
                {selected.end.replace("T", " ")}
              </p>
              <p className="journal-text">{selected.reason}</p>
              {selected.files?.map((f) => (
                <p key={f.id}>
                  <a href={fileUrl(f.id)}>{f.filename}</a> ·{" "}
                  {Math.ceil(f.size / 1024)} КиБ
                </p>
              ))}
              <p>
                Принятие меняет учёт отметки «Отсутствует» на «Уважительная
                причина». Оно не ставит отсутствие вместо «Не отмечено»,
                «Присутствует» или «Опоздание» и не публикует черновики журнала.
                Причина и файлы в журнал не передаются.
              </p>
              <fieldset disabled={busy} className="access-fields">
                {(selected.canWithdraw ||
                  (selected.canReview &&
                    ["pending", "approved"].includes(selected.status))) && (
                  <>
                    <label className="field">
                      <span>Объяснение решения</span>
                      <textarea
                        maxLength={2000}
                        value={comment}
                        onChange={(e) => {
                          setComment(e.target.value);
                          setDecisionRequest(crypto.randomUUID());
                        }}
                      />
                    </label>
                    {!!selected.periodLocks?.length && selected.canReview && (
                      <p className="info-line">
                        Период журнала закрыт: {selected.periodLocks.join(", ")}
                        . Принять объяснительную или отменить решение можно
                        после того, как администратор откроет период.
                      </p>
                    )}
                    <div className="dated-toolbar">
                      {selected.canWithdraw && (
                        <button
                          disabled={!comment.trim()}
                          onClick={() => void run(() => decide("withdraw"))}
                        >
                          Отозвать заявление
                        </button>
                      )}
                      {selected.canReview && selected.status === "pending" && (
                        <>
                          <button
                            disabled={
                              !comment.trim() || !!selected.periodLocks?.length
                            }
                            onClick={() => void run(() => decide("approve"))}
                          >
                            Принять объяснительную
                          </button>
                          <button
                            disabled={!comment.trim()}
                            onClick={() => void run(() => decide("reject"))}
                          >
                            Отклонить объяснительную
                          </button>
                        </>
                      )}
                      {selected.canReview && selected.status === "approved" && (
                        <button
                          disabled={
                            !comment.trim() || !!selected.periodLocks?.length
                          }
                          onClick={() => void run(() => decide("revoke"))}
                        >
                          Отменить принятое решение
                        </button>
                      )}
                    </div>
                  </>
                )}
                <button onClick={() => void run(() => open(selected.id))}>
                  Обновить открытое заявление
                </button>
                <button onClick={() => void run(() => changes())}>
                  История заявления
                </button>
              </fieldset>
              {history.map((h) => (
                <div className="family-history" key={h.id}>
                  <p>
                    {new Date(h.created).toLocaleString("ru-RU")} · {h.actor} ·{" "}
                    {labels[h.action] || h.action} · редакция {h.version}
                    {h.batchId && " · общее решение"}
                  </p>
                  <p className="journal-text">{h.comment}</p>
                </div>
              ))}
              {historyNext !== null && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => changes(historyNext))}
                >
                  Предыдущие решения
                </button>
              )}
            </article>
          )}
        </>
      )}
    </details>
  );
}

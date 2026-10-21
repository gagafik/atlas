import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import "./BehaviourNotes.css";

type Kind = "praise" | "remark";
type Category = { id: string; kind: Kind; title: string; active: boolean };
type Student = { id: string; name: string; classId: string; className: string };
type Options = {
  admin: boolean;
  canWrite: boolean;
  family: boolean;
  canAcknowledge: boolean;
  today: string;
  kinds: Record<Kind, string>;
  remarkAck: boolean;
  categories: Category[];
  configVersion: number | null;
  classes: { id: string; name: string }[];
  students: Student[];
};
type Note = {
  id: string;
  version: number;
  status: "published" | "withdrawn";
  kind: Kind;
  day: string;
  categoryId: string;
  category: string;
  text: string;
  author: string;
  studentId: string;
  student: string;
  className?: string;
  requireAck: boolean;
  acknowledged: boolean;
  acknowledgedAt: string | null;
  acknowledgedBy: string | null;
  withdrawReason?: string;
  canChange?: boolean;
};
type History = {
  seq: number;
  actor: string;
  created: string;
  action: string;
  reason: string;
}[];
type Summary = {
  studentId: string;
  student: string;
  className: string;
  praise: number;
  remark: number;
}[];
const kindLabels: Record<Kind, string> = {
  praise: "Похвала",
  remark: "Замечание",
};
const actionLabels: Record<string, string> = {
  created: "Создана",
  edited: "Изменена",
  withdrawn: "Отозвана",
  acknowledged: "Родитель ознакомился",
};
const stamp = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("ru-RU", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : "";

export default function BehaviourNotes({
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
      : [
          ...new Set([
            ...user.workspaces,
            ...user.grants.map((g) => g.workspace),
          ]),
        ];
  const [workspace, setWorkspace] = useState(
    spaces.includes(initialWorkspace)
      ? initialWorkspace
      : spaces[0] || "school",
  );
  const [options, setOptions] = useState<Options | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [tab, setTab] = useState("notes");
  const [notes, setNotes] = useState<Note[]>([]),
    [next, setNext] = useState<number | null>(null),
    [totals, setTotals] = useState({ praise: 0, remark: 0 }),
    [summary, setSummary] = useState<Summary>([]),
    [history, setHistory] = useState<{ id: string; items: History } | null>(
      null,
    );
  const [filters, setFilters] = useState({
    classId: "",
    studentId: "",
    kind: "",
    status: "",
    start: "",
    end: "",
  });
  const [form, setForm] = useState({
    categoryId: "",
    day: "",
    text: "",
    classId: "",
    students: [] as string[],
  });
  const [categories, setCategories] = useState<Category[]>([]),
    [remarkAck, setRemarkAck] = useState(true);
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    request = useRef({ key: "", id: "" });
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  const staff = Boolean(options && (options.canWrite || options.admin));
  const dirty = Boolean(form.text.trim() || form.students.length);

  function query(
    extra: Record<string, string | number> = {},
    keys = Object.keys(filters),
  ) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...filters, ...extra }))
      if (v !== "" && (keys.includes(k) || k in extra))
        params.set(k, String(v));
    const text = params.toString();
    return text ? "?" + text : "";
  }
  async function loadNotes(opts: Options, more = false) {
    const token = sequence.current;
    if (opts.canWrite || opts.admin) {
      const result = await api(
        "behaviour/notes" + query(more && next ? { before: next } : {}),
        workspace,
      );
      if (token !== sequence.current) return;
      setNotes((old) => (more ? [...old, ...result.items] : result.items));
      setNext(result.nextBefore);
    } else if (filters.studentId) {
      const params = new URLSearchParams({ studentId: filters.studentId });
      if (more && next) params.set("before", String(next));
      const result = await api(
        "behaviour/family?" + params.toString(),
        workspace,
      );
      if (token !== sequence.current) return;
      setNotes((old) => (more ? [...old, ...result.items] : result.items));
      setNext(result.nextBefore);
      setTotals({ praise: result.praise, remark: result.remark });
    } else {
      setNotes([]);
      setNext(null);
    }
  }
  async function load() {
    const token = ++sequence.current;
    setBusy(true);
    setError("");
    try {
      const opts: Options = await api("behaviour/options", workspace);
      if (token !== sequence.current) return;
      setOptions(opts);
      setCategories(opts.categories);
      setRemarkAck(opts.remarkAck);
      setForm((f) => ({
        ...f,
        day: f.day || opts.today,
        categoryId:
          f.categoryId || opts.categories.find((c) => c.active)?.id || "",
      }));
      if (
        !opts.canWrite &&
        !opts.admin &&
        opts.students.length &&
        !opts.students.some((s) => s.id === filters.studentId)
      ) {
        setFilters((f) => ({ ...f, studentId: opts.students[0].id }));
        return;
      }
      await loadNotes(opts);
    } catch (e: any) {
      if (token === sequence.current) {
        setOptions(null);
        setNotes([]);
        setError(e.message);
      }
    } finally {
      if (token === sequence.current) setBusy(false);
    }
  }
  useEffect(() => {
    setHistory(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, filters]);
  useEffect(
    () => () => {
      ++sequence.current;
    },
    [],
  );
  async function run(action: () => Promise<void>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(message);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function refreshAfterChange() {
    try {
      if (options) await loadNotes(options);
    } catch (e: any) {
      setError("Изменение сохранено, но список не обновился: " + e.message);
    }
  }
  function leave() {
    return (
      !dirty ||
      window.confirm("Новая запись не сохранена. Отбросить её и закрыть?")
    );
  }
  async function submit() {
    const body = {
      studentIds: form.students,
      categoryId: form.categoryId,
      day: form.day,
      text: form.text,
    };
    const key = JSON.stringify(body);
    if (request.current.key !== key)
      request.current = { key, id: crypto.randomUUID() };
    await run(async () => {
      await api("behaviour/notes", workspace, "POST", {
        requestId: request.current.id,
        ...body,
      });
      request.current = { key: "", id: "" };
      setForm((f) => ({ ...f, text: "", students: [] }));
      await refreshAfterChange();
    }, "Запись сохранена, семьи получили уведомление.");
  }
  async function edit(note: Note) {
    const text = window.prompt("Комментарий к записи", note.text);
    if (text === null) return;
    const reason = window.prompt("Причина изменения");
    if (!reason?.trim()) return;
    await run(async () => {
      await api(`behaviour/notes/${note.id}/edit`, workspace, "POST", {
        version: note.version,
        categoryId: note.categoryId,
        text,
        reason,
      });
      await refreshAfterChange();
    }, "Запись изменена.");
  }
  async function withdraw(note: Note) {
    const reason = window.prompt("Причина отзыва записи");
    if (!reason?.trim()) return;
    await run(async () => {
      await api(`behaviour/notes/${note.id}/withdraw`, workspace, "POST", {
        version: note.version,
        reason,
      });
      await refreshAfterChange();
    }, "Запись отозвана и скрыта от семьи.");
  }
  async function acknowledge(note: Note) {
    await run(async () => {
      await api(`behaviour/notes/${note.id}/acknowledge`, workspace, "POST", {
        version: note.version,
      });
      await refreshAfterChange();
    }, "Подтверждение отправлено учителю.");
  }
  async function showHistory(note: Note) {
    await run(async () => {
      const result = await api(`behaviour/notes/${note.id}/history`, workspace);
      setHistory({ id: note.id, items: result.items });
    }, "");
  }
  async function loadSummary() {
    await run(async () => {
      const result = await api(
        "behaviour/summary" + query({}, ["classId", "start", "end"]),
        workspace,
      );
      setSummary(result.items);
    }, "");
  }
  async function saveConfig() {
    const reason = window.prompt("Причина изменения категорий");
    if (!reason?.trim() || !options) return;
    await run(async () => {
      await api("behaviour/config", workspace, "PUT", {
        version: options.configVersion,
        reason,
        categories,
        remarkAck,
      });
      await load();
    }, "Категории сохранены.");
  }

  const pupils = options?.students || [];
  const visiblePupils = form.classId
    ? pupils.filter((s) => s.classId === form.classId)
    : pupils;
  const exportQuery = query({ workspace }, [
    "classId",
    "studentId",
    "kind",
    "start",
    "end",
  ]);
  const exportHref = "/api/behaviour/export" + exportQuery;
  return (
    <dialog
      className="behaviour"
      ref={dialog}
      aria-labelledby="behaviour-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && leave()) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="behaviour-title">Похвалы и замечания</h2>
          <p>Записи учителей о поведении и успехах учеников</p>
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
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка…</p>}
      <fieldset disabled={busy}>
        <div className="bn-controls">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => {
                if (!leave()) return;
                setForm((f) => ({ ...f, students: [], text: "", classId: "" }));
                setFilters({
                  classId: "",
                  studentId: "",
                  kind: "",
                  status: "",
                  start: "",
                  end: "",
                });
                setWorkspace(e.target.value);
              }}
            >
              {spaces.map((s) => (
                <option key={s} value={s}>
                  {s === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => void load()}>Обновить</button>
        </div>
        {options && staff && (
          <nav className="bn-controls" aria-label="Разделы">
            {[
              ["notes", "Записи"],
              ["summary", "Сводка"],
              ...(options.admin ? [["config", "Категории"]] : []),
            ].map(([id, title]) => (
              <button
                key={id}
                aria-pressed={tab === id}
                onClick={() => {
                  setTab(id);
                  if (id === "summary") void loadSummary();
                }}
              >
                {title}
              </button>
            ))}
          </nav>
        )}
        {options && !staff && !options.students.length && (
          <p>
            Для этой учётной записи нет доступных учеников. Похвалы и замечания
            видят учителя учеников, классные руководители и семьи.
          </p>
        )}

        {options && !staff && options.students.length > 0 && (
          <section>
            <label>
              Ученик
              <select
                value={filters.studentId}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, studentId: e.target.value }))
                }
              >
                {options.students.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.className ? ` · ${s.className}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <p className="bn-totals">
              <span className="bn-praise">Похвал: {totals.praise}</span>
              <span className="bn-remark">Замечаний: {totals.remark}</span>
            </p>
          </section>
        )}

        {options && staff && tab === "notes" && options.canWrite && (
          <section className="bn-form" aria-label="Новая запись">
            <h3>Новая запись</h3>
            <div className="bn-controls">
              <label>
                Категория
                <select
                  value={form.categoryId}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, categoryId: e.target.value }))
                  }
                >
                  {(["praise", "remark"] as Kind[]).map((kind) => (
                    <optgroup key={kind} label={kindLabels[kind]}>
                      {options.categories
                        .filter((c) => c.kind === kind && c.active)
                        .map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.title}
                          </option>
                        ))}
                    </optgroup>
                  ))}
                </select>
              </label>
              <label>
                Дата
                <input
                  type="date"
                  max={options.today}
                  value={form.day}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, day: e.target.value }))
                  }
                />
              </label>
              <label>
                Класс
                <select
                  value={form.classId}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, classId: e.target.value }))
                  }
                >
                  <option value="">Все мои ученики</option>
                  {options.classes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <fieldset className="bn-pupils">
              <legend>
                Ученики (выбрано {form.students.length}, не больше 40)
              </legend>
              {visiblePupils.map((s) => (
                <label key={s.id}>
                  <input
                    type="checkbox"
                    checked={form.students.includes(s.id)}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        students: e.target.checked
                          ? [...f.students, s.id]
                          : f.students.filter((id) => id !== s.id),
                      }))
                    }
                  />
                  {s.name}
                  {s.className ? ` · ${s.className}` : ""}
                </label>
              ))}
            </fieldset>
            <label className="bn-wide">
              Комментарий для семьи
              <textarea
                maxLength={2000}
                value={form.text}
                onChange={(e) =>
                  setForm((f) => ({ ...f, text: e.target.value }))
                }
              />
            </label>
            <button
              disabled={
                !form.students.length ||
                form.students.length > 40 ||
                !form.categoryId ||
                !form.day
              }
              onClick={() => void submit()}
            >
              Сохранить и уведомить семьи
            </button>
          </section>
        )}

        {options && staff && tab === "notes" && (
          <section aria-label="Фильтры" className="bn-controls">
            <label>
              Класс
              <select
                value={filters.classId}
                onChange={(e) =>
                  setFilters((f) => ({
                    ...f,
                    classId: e.target.value,
                    studentId: "",
                  }))
                }
              >
                <option value="">Все</option>
                {options.classes.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Ученик
              <select
                value={filters.studentId}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, studentId: e.target.value }))
                }
              >
                <option value="">Все</option>
                {pupils
                  .filter(
                    (s) => !filters.classId || s.classId === filters.classId,
                  )
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Вид
              <select
                value={filters.kind}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, kind: e.target.value }))
                }
              >
                <option value="">Все</option>
                <option value="praise">Похвалы</option>
                <option value="remark">Замечания</option>
              </select>
            </label>
            <label>
              Статус
              <select
                value={filters.status}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, status: e.target.value }))
                }
              >
                <option value="">Все</option>
                <option value="published">Действующие</option>
                <option value="withdrawn">Отозванные</option>
              </select>
            </label>
            <label>
              С
              <input
                type="date"
                value={filters.start}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, start: e.target.value }))
                }
              />
            </label>
            <label>
              По
              <input
                type="date"
                value={filters.end}
                onChange={(e) =>
                  setFilters((f) => ({ ...f, end: e.target.value }))
                }
              />
            </label>
            <a href={exportHref} download>
              Скачать CSV
            </a>
          </section>
        )}

        {options && (tab === "notes" || !staff) && (
          <section aria-label="Записи">
            {!notes.length && <p>Записей пока нет.</p>}
            <ul className="bn-list">
              {notes.map((n) => (
                <li
                  key={n.id}
                  className={`bn-note bn-${n.kind}${n.status === "withdrawn" ? " bn-withdrawn" : ""}`}
                >
                  <p>
                    <strong>{kindLabels[n.kind]}</strong> · {n.category} ·{" "}
                    {new Date(n.day + "T00:00").toLocaleDateString("ru-RU")}
                  </p>
                  {staff && (
                    <p>
                      {n.student}
                      {n.className ? ` · ${n.className}` : ""}
                    </p>
                  )}
                  {n.text && <p className="bn-text">{n.text}</p>}
                  <p className="bn-meta">
                    Автор: {n.author}
                    {n.requireAck &&
                      (n.acknowledged
                        ? ` · Родитель ознакомился ${stamp(n.acknowledgedAt)} (${n.acknowledgedBy})`
                        : " · Ожидает подтверждения родителя")}
                    {n.status === "withdrawn" &&
                      ` · Отозвана: ${n.withdrawReason || ""}`}
                  </p>
                  <div className="bn-controls">
                    {options.canAcknowledge &&
                      n.requireAck &&
                      !n.acknowledged && (
                        <button onClick={() => void acknowledge(n)}>
                          Ознакомлен
                        </button>
                      )}
                    {staff && n.canChange && (
                      <>
                        <button
                          disabled={n.acknowledged}
                          onClick={() => void edit(n)}
                        >
                          Изменить
                        </button>
                        <button onClick={() => void withdraw(n)}>
                          Отозвать
                        </button>
                      </>
                    )}
                    {staff && (
                      <button onClick={() => void showHistory(n)}>
                        История
                      </button>
                    )}
                  </div>
                  {history?.id === n.id && (
                    <ol className="bn-history">
                      {history.items.map((h) => (
                        <li key={h.seq}>
                          {stamp(h.created)} · {h.actor} ·{" "}
                          {actionLabels[h.action] || h.action}
                          {h.reason ? ` · ${h.reason}` : ""}
                        </li>
                      ))}
                    </ol>
                  )}
                </li>
              ))}
            </ul>
            {next && (
              <button
                onClick={() =>
                  options && void run(() => loadNotes(options, true), "")
                }
              >
                Показать ещё
              </button>
            )}
          </section>
        )}

        {options && staff && tab === "summary" && (
          <section aria-label="Сводка">
            <p>Действующие записи за выбранный в фильтрах период и класс.</p>
            <table className="bn-table">
              <thead>
                <tr>
                  <th>Ученик</th>
                  <th>Класс</th>
                  <th>Похвалы</th>
                  <th>Замечания</th>
                </tr>
              </thead>
              <tbody>
                {summary.map((r) => (
                  <tr key={r.studentId}>
                    <td>{r.student}</td>
                    <td>{r.className}</td>
                    <td>{r.praise}</td>
                    <td>{r.remark}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!summary.length && <p>Записей за период нет.</p>}
          </section>
        )}

        {options?.admin && tab === "config" && (
          <section aria-label="Категории">
            <label className="bn-check">
              <input
                type="checkbox"
                checked={remarkAck}
                onChange={(e) => setRemarkAck(e.target.checked)}
              />
              Замечания требуют подтверждения родителя
            </label>
            <table className="bn-table">
              <thead>
                <tr>
                  <th>Вид</th>
                  <th>Название</th>
                  <th>Действует</th>
                </tr>
              </thead>
              <tbody>
                {categories.map((c, i) => (
                  <tr key={c.id}>
                    <td>
                      <select
                        value={c.kind}
                        onChange={(e) =>
                          setCategories((all) =>
                            all.map((x, j) =>
                              j === i
                                ? { ...x, kind: e.target.value as Kind }
                                : x,
                            ),
                          )
                        }
                      >
                        <option value="praise">Похвала</option>
                        <option value="remark">Замечание</option>
                      </select>
                    </td>
                    <td>
                      <input
                        maxLength={120}
                        value={c.title}
                        onChange={(e) =>
                          setCategories((all) =>
                            all.map((x, j) =>
                              j === i ? { ...x, title: e.target.value } : x,
                            ),
                          )
                        }
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={"Действует: " + c.title}
                        checked={c.active}
                        onChange={(e) =>
                          setCategories((all) =>
                            all.map((x, j) =>
                              j === i ? { ...x, active: e.target.checked } : x,
                            ),
                          )
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="bn-controls">
              <button
                disabled={categories.length >= 60}
                onClick={() =>
                  setCategories((all) => [
                    ...all,
                    {
                      id: "c-" + crypto.randomUUID().slice(0, 8),
                      kind: "praise",
                      title: "Новая категория",
                      active: true,
                    },
                  ])
                }
              >
                Добавить категорию
              </button>
              <button onClick={() => void saveConfig()}>
                Сохранить категории
              </button>
            </div>
          </section>
        )}
      </fieldset>
    </dialog>
  );
}

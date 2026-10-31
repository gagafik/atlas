import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import "./BehaviourNotes.css";

type Kind = "praise" | "remark";
type Level = "teacher" | "class_teacher" | "director";
type Category = {
  id: string;
  kind: Kind;
  title: string;
  active: boolean;
  level: Level;
  approval: boolean;
  quick: boolean;
  letter: boolean;
  // Printed in the pupil's report card by default.
  report: boolean;
};
type Student = { id: string; name: string; classId: string; className: string };
type Options = {
  admin: boolean;
  canWrite: boolean;
  family: boolean;
  canAcknowledge: boolean;
  today: string;
  levels: Record<Level, string>;
  myLevels: Level[];
  remarkAck: boolean;
  categories: Category[];
  pending: number;
  configVersion: number | null;
  classes: { id: string; name: string }[];
  students: Student[];
};
type Status = "pending" | "published" | "rejected" | "withdrawn";
type Note = {
  id: string;
  version: number;
  status: Status;
  kind: Kind;
  day: string;
  categoryId: string;
  category: string;
  text: string;
  author: string;
  level: Level;
  levelTitle: string;
  studentId: string;
  student: string;
  className?: string;
  requireAck: boolean;
  acknowledged: boolean;
  acknowledgedAt: string | null;
  acknowledgedBy: string | null;
  letter: boolean;
  letterNumber: string;
  lesson: string;
  visible?: boolean;
  printed?: boolean;
  approval?: boolean;
  decision?: string;
  approvedBy?: string;
  withdrawReason?: string;
  canChange?: boolean;
  canDecide?: boolean;
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
type Place = {
  place: number;
  studentId: string;
  student: string;
  className: string;
  count: number;
};
type Letter = {
  school: string;
  number: string;
  day: string;
  student: string;
  className: string;
  kind: string;
  title: string;
  text: string;
  level: string;
  author: string;
  approvedBy: string;
  acknowledgedAt: string | null;
  acknowledgedBy: string | null;
};
const kindLabels: Record<Kind, string> = {
  praise: "Похвала",
  remark: "Замечание",
};
const statusLabels: Record<Status, string> = {
  pending: "На согласовании",
  published: "Действует",
  rejected: "Отклонена",
  withdrawn: "Отозвана",
};
const actionLabels: Record<string, string> = {
  created: "Создана",
  edited: "Изменена",
  withdrawn: "Отозвана",
  acknowledged: "Родитель ознакомился",
  approved: "Утверждена",
  rejected: "Отклонена",
};
const stamp = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("ru-RU", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : "";
const flagLabels = {
  approval: "Согласование",
  quick: "Быстрая отметка",
  letter: "Письмо-решение",
  report: "В табель",
  active: "Действует",
};
const dayLabel = (day: string) =>
  new Date(day + "T00:00").toLocaleDateString("ru-RU");

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
    [board, setBoard] = useState<Place[]>([]),
    [boardKind, setBoardKind] = useState<Kind>("praise"),
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
    visible: true,
    printed: false,
    students: [] as string[],
  });
  const [letterView, setLetterView] = useState<Letter | null>(null);
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
  const usable = (options?.categories || []).filter(
    (c) => c.active && options?.myLevels.includes(c.level),
  );

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
  async function loadNotes(opts: Options, view = tab, more = false) {
    const token = sequence.current;
    const page: Record<string, number> = more && next ? { before: next } : {};
    if (view === "queue" && opts.admin) {
      const result = await api("behaviour/queue" + query(page, []), workspace);
      if (token !== sequence.current) return;
      setNotes((old) => (more ? [...old, ...result.items] : result.items));
      setNext(result.nextBefore);
    } else if (opts.canWrite || opts.admin) {
      const result = await api("behaviour/notes" + query(page), workspace);
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
  async function load(view = tab) {
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
          f.categoryId ||
          opts.categories.find(
            (c) => c.active && opts.myLevels.includes(c.level),
          )?.id ||
          "",
        printed: f.categoryId
          ? f.printed
          : (opts.categories.find(
              (c) => c.active && opts.myLevels.includes(c.level),
            )?.report ?? false),
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
      if (view === "board") await loadBoard(boardKind);
      else if (view === "summary") await loadSummary();
      else if (view !== "config") await loadNotes(opts, view);
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
      await load();
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
  async function submit(categoryId = form.categoryId, text = form.text) {
    const body = {
      studentIds: form.students,
      categoryId,
      day: categoryId === form.categoryId ? form.day : options!.today,
      text,
      visible: form.visible,
      // A quick mark takes the default of its own category.
      ...(categoryId === form.categoryId ? { printed: form.printed } : {}),
    };
    const key = JSON.stringify(body);
    if (request.current.key !== key)
      request.current = { key, id: crypto.randomUUID() };
    let status = "";
    await run(async () => {
      const result = await api("behaviour/notes", workspace, "POST", {
        requestId: request.current.id,
        ...body,
      });
      status = result.status;
      request.current = { key: "", id: "" };
      setForm((f) => ({ ...f, text: "", students: [] }));
      await refreshAfterChange();
    }, "");
    if (status)
      setNotice(
        status === "pending"
          ? "Мера отправлена на согласование администрации."
          : form.visible
            ? "Запись сохранена, семьи получили уведомление."
            : "Запись сохранена без показа семье.",
      );
  }
  async function edit(note: Note) {
    const text = window.prompt("Комментарий к записи", note.text);
    if (text === null) return;
    const reason = window.prompt("Причина изменения");
    if (!reason?.trim()) return;
    // OK turns the print mark over, Cancel leaves it as it is.
    const flip = window.confirm(
      note.printed
        ? "Запись печатается в табеле. Убрать её из табеля?"
        : "Запись не печатается в табеле. Печатать её в табеле?",
    );
    await run(async () => {
      await api(`behaviour/notes/${note.id}/edit`, workspace, "POST", {
        version: note.version,
        categoryId: note.categoryId,
        text,
        reason,
        ...(flip ? { printed: !note.printed } : {}),
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
  async function decide(note: Note, approve: boolean) {
    const reason = window.prompt(
      approve
        ? "Комментарий к решению (необязательно)"
        : "Причина отказа (обязательно)",
    );
    if (reason === null || (!approve && !reason.trim())) return;
    await run(
      async () => {
        await api(
          `behaviour/notes/${note.id}/${approve ? "approve" : "reject"}`,
          workspace,
          "POST",
          { version: note.version, reason },
        );
        await refreshAfterChange();
      },
      approve ? "Мера утверждена." : "Мера отклонена, автор получил решение.",
    );
  }
  async function acknowledge(note: Note) {
    await run(async () => {
      await api(`behaviour/notes/${note.id}/acknowledge`, workspace, "POST", {
        version: note.version,
      });
      await refreshAfterChange();
    }, "Подтверждение отправлено учителю.");
  }
  async function letter(note: Note) {
    await run(async () => {
      setLetterView(await api(`behaviour/notes/${note.id}/letter`, workspace));
    }, "");
  }
  async function showHistory(note: Note) {
    await run(async () => {
      const result = await api(`behaviour/notes/${note.id}/history`, workspace);
      setHistory({ id: note.id, items: result.items });
    }, "");
  }
  async function loadSummary() {
    const result = await api(
      "behaviour/summary" + query({}, ["classId", "start", "end"]),
      workspace,
    );
    setSummary(result.items);
  }
  async function loadBoard(kind: Kind) {
    const result = await api(
      "behaviour/board" + query({ kind }, ["classId", "start", "end"]),
      workspace,
    );
    setBoard(result.items);
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
  function open(view: string) {
    if (view !== tab && !leave()) return;
    setTab(view);
    setHistory(null);
    void run(() => load(view), "");
  }
  function updateCategory(i: number, change: Partial<Category>) {
    setCategories((all) =>
      all.map((x, j) => (j === i ? { ...x, ...change } : x)),
    );
  }

  const pupils = options?.students || [];
  const visiblePupils = form.classId
    ? pupils.filter((s) => s.classId === form.classId)
    : pupils;
  const exportHref =
    "/api/behaviour/export" +
    query({ workspace }, ["classId", "studentId", "kind", "start", "end"]);
  const quick = usable.filter((c) => c.quick);
  const tabs: [string, string][] = [
    ["notes", "Записи"],
    ...(options?.admin
      ? [["queue", `На согласовании (${options.pending})`] as [string, string]]
      : []),
    ["board", "Табло"],
    ["summary", "Сводка"],
    ...(options?.admin ? [["config", "Категории"] as [string, string]] : []),
  ];
  const periodFilters = (
    <>
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
          {options?.classes.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        С
        <input
          type="date"
          value={filters.start}
          onChange={(e) => setFilters((f) => ({ ...f, start: e.target.value }))}
        />
      </label>
      <label>
        По
        <input
          type="date"
          value={filters.end}
          onChange={(e) => setFilters((f) => ({ ...f, end: e.target.value }))}
        />
      </label>
    </>
  );
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
          <h2 id="behaviour-title">Похвалы, замечания и меры</h2>
          <p>Записи учителей, классных руководителей и администрации</p>
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
      {letterView && (
        <section className="bn-letter" aria-label="Письмо-решение">
          <p>{letterView.school}</p>
          <h3>
            {letterView.kind}: {letterView.title}
          </h3>
          <p>
            № {letterView.number} от {dayLabel(letterView.day)}
          </p>
          <p>
            Ученик: {letterView.student}
            {letterView.className ? `, ${letterView.className}` : ""}
          </p>
          {letterView.text && <p className="bn-text">{letterView.text}</p>}
          <p className="bn-sign">
            Назначил(а): {letterView.author} ({letterView.level})
          </p>
          {letterView.approvedBy && <p>Утвердил(а): {letterView.approvedBy}</p>}
          <p className="bn-sign">
            {letterView.acknowledgedAt
              ? `С решением ознакомлен(а): ${letterView.acknowledgedBy || ""}, ${stamp(letterView.acknowledgedAt)}`
              : "С решением ознакомлен(а): ____________________  Дата: ________"}
          </p>
          <div className="bn-controls bn-noprint">
            <button onClick={() => window.print()}>Печать</button>
            <button onClick={() => setLetterView(null)}>Закрыть письмо</button>
          </div>
        </section>
      )}
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
          <button onClick={() => void run(() => load(), "")}>Обновить</button>
        </div>
        {options && staff && (
          <nav className="bn-controls" aria-label="Разделы">
            {tabs.map(([id, title]) => (
              <button
                key={id}
                aria-pressed={tab === id}
                onClick={() => open(id)}
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
            <fieldset className="bn-pupils">
              <legend>
                Ученики (выбрано {form.students.length}, не больше 40)
              </legend>
              <label className="bn-wide-row">
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
            {quick.length > 0 && (
              <div className="bn-quick" aria-label="Быстрые отметки">
                <span>Быстрая отметка сегодня:</span>
                {quick.map((c) => (
                  <button
                    key={c.id}
                    className={`bn-chip bn-${c.kind}`}
                    disabled={
                      !form.students.length || form.students.length > 40
                    }
                    onClick={() => void submit(c.id, "")}
                  >
                    {c.kind === "praise" ? "＋ " : "− "}
                    {c.title}
                  </button>
                ))}
              </div>
            )}
            <div className="bn-controls">
              <label>
                Категория
                <select
                  value={form.categoryId}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      categoryId: e.target.value,
                      printed:
                        options.categories.find((c) => c.id === e.target.value)
                          ?.report ?? false,
                    }))
                  }
                >
                  {options.myLevels.map((level) => (
                    <optgroup key={level} label={options.levels[level]}>
                      {usable
                        .filter((c) => c.level === level)
                        .map((c) => (
                          <option key={c.id} value={c.id}>
                            {kindLabels[c.kind]}: {c.title}
                            {c.approval && !options.admin
                              ? " · согласование"
                              : ""}
                            {c.letter ? " · письмо" : ""}
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
              <label className="bn-check">
                <input
                  type="checkbox"
                  checked={form.visible}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, visible: e.target.checked }))
                  }
                />
                Показать семье
              </label>
              <label className="bn-check">
                <input
                  type="checkbox"
                  checked={form.printed}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, printed: e.target.checked }))
                  }
                />
                Печатать в табель
              </label>
            </div>
            <label className="bn-wide">
              Комментарий или основание
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
              Сохранить
            </button>
          </section>
        )}

        {options && staff && tab === "notes" && (
          <section aria-label="Фильтры" className="bn-controls">
            {periodFilters}
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
                {(Object.keys(statusLabels) as Status[]).map((s) => (
                  <option key={s} value={s}>
                    {statusLabels[s]}
                  </option>
                ))}
              </select>
            </label>
            <a href={exportHref} download>
              Скачать CSV
            </a>
          </section>
        )}

        {options && (tab === "notes" || tab === "queue" || !staff) && (
          <section aria-label="Записи">
            {!notes.length && (
              <p>
                {tab === "queue"
                  ? "Мер на согласовании нет."
                  : "Записей пока нет."}
              </p>
            )}
            <ul className="bn-list">
              {notes.map((n) => (
                <li
                  key={n.id}
                  className={`bn-note bn-${n.kind}${n.status === "withdrawn" || n.status === "rejected" ? " bn-withdrawn" : ""}`}
                >
                  <p>
                    <strong>{kindLabels[n.kind]}</strong> · {n.category} ·{" "}
                    {dayLabel(n.day)}
                    {n.level !== "teacher" && ` · ${n.levelTitle}`}
                    {staff && n.status !== "published" && (
                      <span className="bn-badge">{statusLabels[n.status]}</span>
                    )}
                    {staff && n.visible === false && (
                      <span className="bn-badge">Скрыта от семьи</span>
                    )}
                    {staff && n.printed && (
                      <span className="bn-badge">В табеле</span>
                    )}
                    {n.letterNumber && (
                      <span className="bn-badge">
                        Письмо № {n.letterNumber}
                      </span>
                    )}
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
                    {n.lesson && ` · Урок: ${n.lesson}`}
                    {n.approvedBy && ` · Утвердил(а): ${n.approvedBy}`}
                    {n.decision && ` · Решение: ${n.decision}`}
                    {n.requireAck &&
                      n.status === "published" &&
                      (n.acknowledged
                        ? ` · Родитель ознакомился ${stamp(n.acknowledgedAt)} (${n.acknowledgedBy})`
                        : " · Ожидает подтверждения родителя")}
                    {n.status === "withdrawn" &&
                      ` · Отозвана: ${n.withdrawReason || ""}`}
                  </p>
                  <div className="bn-controls">
                    {n.canDecide && (
                      <>
                        <button onClick={() => void decide(n, true)}>
                          Утвердить
                        </button>
                        <button onClick={() => void decide(n, false)}>
                          Отклонить
                        </button>
                      </>
                    )}
                    {options.canAcknowledge &&
                      n.requireAck &&
                      !n.acknowledged && (
                        <button onClick={() => void acknowledge(n)}>
                          Ознакомлен
                        </button>
                      )}
                    {n.letter && n.status === "published" && (
                      <button onClick={() => void letter(n)}>
                        Письмо-решение
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
                  options && void run(() => loadNotes(options, tab, true), "")
                }
              >
                Показать ещё
              </button>
            )}
          </section>
        )}

        {options && staff && tab === "board" && (
          <section aria-label="Табло">
            <div className="bn-controls">
              <label>
                Вид
                <select
                  value={boardKind}
                  onChange={(e) => {
                    const kind = e.target.value as Kind;
                    setBoardKind(kind);
                    void run(() => loadBoard(kind), "");
                  }}
                >
                  <option value="praise">Похвалы</option>
                  <option value="remark">Замечания</option>
                </select>
              </label>
              {periodFilters}
            </div>
            <ol className="bn-podium">
              {board
                .filter((r) => r.place <= 3)
                .map((r) => (
                  <li key={r.studentId} className={`bn-place-${r.place}`}>
                    <strong>{r.place}</strong>
                    <span>{r.student}</span>
                    <small>
                      {r.className} · {r.count}
                    </small>
                  </li>
                ))}
            </ol>
            <table className="bn-table">
              <thead>
                <tr>
                  <th>Место</th>
                  <th>Ученик</th>
                  <th>Класс</th>
                  <th>{boardKind === "praise" ? "Похвалы" : "Замечания"}</th>
                </tr>
              </thead>
              <tbody>
                {board.map((r) => (
                  <tr key={r.studentId}>
                    <td>{r.place}</td>
                    <td>{r.student}</td>
                    <td>{r.className}</td>
                    <td>{r.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!board.length && <p>Записей за период нет.</p>}
          </section>
        )}

        {options && staff && tab === "summary" && (
          <section aria-label="Сводка">
            <div className="bn-controls">{periodFilters}</div>
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
            <div className="bn-scroll">
              <table className="bn-table">
                <thead>
                  <tr>
                    <th>Вид</th>
                    <th>Название</th>
                    <th>Кто назначает</th>
                    <th>Согласование</th>
                    <th>Быстрая</th>
                    <th>Письмо</th>
                    <th>В табель</th>
                    <th>Действует</th>
                  </tr>
                </thead>
                <tbody>
                  {categories.map((c, i) => (
                    <tr key={c.id}>
                      <td>
                        <select
                          aria-label={"Вид: " + c.title}
                          value={c.kind}
                          onChange={(e) =>
                            updateCategory(i, { kind: e.target.value as Kind })
                          }
                        >
                          <option value="praise">Похвала</option>
                          <option value="remark">Замечание</option>
                        </select>
                      </td>
                      <td>
                        <input
                          aria-label="Название категории"
                          maxLength={120}
                          value={c.title}
                          onChange={(e) =>
                            updateCategory(i, { title: e.target.value })
                          }
                        />
                      </td>
                      <td>
                        <select
                          aria-label={"Кто назначает: " + c.title}
                          value={c.level}
                          onChange={(e) =>
                            updateCategory(i, {
                              level: e.target.value as Level,
                            })
                          }
                        >
                          {(Object.keys(options.levels) as Level[]).map((l) => (
                            <option key={l} value={l}>
                              {options.levels[l]}
                            </option>
                          ))}
                        </select>
                      </td>
                      {(
                        [
                          "approval",
                          "quick",
                          "letter",
                          "report",
                          "active",
                        ] as const
                      ).map((field) => (
                        <td key={field}>
                          <input
                            type="checkbox"
                            aria-label={`${flagLabels[field]}: ${c.title}`}
                            checked={c[field]}
                            onChange={(e) =>
                              updateCategory(i, { [field]: e.target.checked })
                            }
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="bn-meta">
              Меры уровня «Директор» назначает администрация. Согласование
              нужно, когда меру назначает не администратор. Мера с письмом
              получает номер при вступлении в силу и требует подтверждения
              родителя. Мера с отметкой «В табель» попадает в табель ученика за
              период, в котором она действовала: печатаются вид, категория, дата
              и номер письма, но не комментарий и не автор.
            </p>
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
                      level: "teacher",
                      approval: false,
                      quick: false,
                      letter: false,
                      report: false,
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

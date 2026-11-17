import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import { measureCategory } from "./behaviourMeasure";
import CommissionExams from "./CommissionExams";
import {
  buildQuery,
  dayLabel,
  emptyFilters,
  formDirty,
  freshForm,
  searchToApply,
  stamp,
  statusLabels,
  type Category,
  type Filters,
  type FormBase,
  type History,
  type Kind,
  type Level,
  type Note,
  type NoteForm,
  type Options,
  type Status,
} from "./behaviourNotesModel";
import BehaviourNoteForm from "./BehaviourNoteForm";
import BehaviourNotesTable from "./BehaviourNotesTable";
import Letterhead from "./Letterhead";
import "./BehaviourNotes.css";

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
const flagLabels = {
  approval: "Согласование",
  quick: "Быстрая отметка",
  letter: "Письмо-решение",
  report: "В табель",
  active: "Действует",
};
const UNSAVED_RECORD =
  "В записи есть несохранённые изменения. Уйти без сохранения?";
export default function BehaviourNotes({
  user,
  initialWorkspace,
  studentId,
  prefill,
  initialTab,
  onClose,
}: {
  user: Account;
  initialWorkspace: string;
  // The child a family portal showed: the records of a parent start with them.
  studentId?: string;
  // Opened from the gradebook: the pupils marked there, whether the form should start as an educational measure, and the course
  // (its subject) the notes are written for.
  prefill?: {
    studentIds: string[];
    measure?: boolean;
    subjectId?: string;
    subject?: string;
  };
  // A notification leads to a section of the records: «commission».
  initialTab?: string;
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
    // An action (save, decide, a tab change) locks the controls; a reload of the list after a filter change does not,
    // or the search field you type in would be disabled and lose its focus on every pause.
    [acting, setActing] = useState(false),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [tab, setTab] = useState("notes");
  const busy = acting || loading;
  const [notes, setNotes] = useState<Note[]>([]),
    [next, setNext] = useState<number | null>(null),
    [totals, setTotals] = useState({ praise: 0, remark: 0 }),
    [summary, setSummary] = useState<Summary>([]),
    [board, setBoard] = useState<Place[]>([]),
    [boardKind, setBoardKind] = useState<Kind>("praise"),
    [history, setHistory] = useState<{ id: string; items: History } | null>(
      null,
    ),
    // The record whose line is open under its row.
    [openId, setOpenId] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  // What is typed in the search field; it becomes the filter when typing stops or on Enter.
  const [search, setSearch] = useState("");
  const [form, setForm] = useState<NoteForm>({
    categoryId: "",
    day: "",
    text: "",
    classId: "",
    visible: true,
    printed: false,
    students: [],
  });
  const [letterView, setLetterView] = useState<Letter | null>(null);
  const [categories, setCategories] = useState<Category[]>([]),
    [remarkAck, setRemarkAck] = useState(true);
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    request = useRef({ key: "", id: "" }),
    // The prefill is applied once, when the options first arrive.
    prefilled = useRef(false),
    tabOpened = useRef(false),
    // The choices the form was opened with: a change of them counts as unsaved.
    formBase = useRef<FormBase | null>(null),
    newButton = useRef<HTMLButtonElement>(null);
  // The form of a new record is opened on request; a course table or the launcher opens it at once.
  const [formOpen, setFormOpen] = useState(Boolean(prefill));
  const [commissionDirty, setCommissionDirty] = useState(false);
  // The subject of the course the pupils were marked in; the author may let it go for a note that is about something else.
  const course =
    prefill?.subjectId && workspace === initialWorkspace
      ? { id: prefill.subjectId, name: prefill.subject ?? "" }
      : null;
  const [forCourse, setForCourse] = useState(true);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  const staff = Boolean(options && (options.canWrite || options.admin));
  // A commission member may have no pupils in scope: the modal then holds the commission tab only.
  const commissionTab = Boolean(
    options?.commission &&
    (options.commission.manage || options.commission.member),
  );
  const showForm = Boolean(
    formOpen && staff && tab === "notes" && options?.canWrite,
  );
  // The form takes the focus when it opens (it does so itself); the button that opened it gets it back when it closes.
  const formShown = useRef(false);
  useEffect(() => {
    if (showForm) {
      formBase.current ??= {
        categoryId: form.categoryId,
        day: form.day,
        visible: form.visible,
        printed: form.printed,
      };
    } else if (formShown.current) newButton.current?.focus();
    formShown.current = showForm;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showForm]);
  const usable = (options?.categories || []).filter(
    (c) => c.active && options?.myLevels.includes(c.level),
  );

  function query(
    extra: Record<string, string | number> = {},
    keys = Object.keys(filters),
  ) {
    return buildQuery(filters, extra, keys);
  }
  // The text of the search becomes a filter 400 ms after the last key; the list effect then reloads, and an answer to an
  // older search is dropped by the sequence check of load(). While the form of a record is open, or another tab is on
  // show, nothing is applied: the reload would clear the messages of the form and lock it. The search waits and is
  // applied when the list is back.
  useEffect(() => {
    const text = searchToApply(search, filters.q);
    if (text === null || showForm || tab !== "notes") return;
    const timer = window.setTimeout(
      () => setFilters((f) => (f.q === text ? f : { ...f, q: text })),
      400,
    );
    return () => window.clearTimeout(timer);
  }, [search, filters.q, showForm, tab]);
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
    setLoading(true);
    setError("");
    try {
      const opts: Options = await api("behaviour/options", workspace);
      if (token !== sequence.current) return;
      setOptions(opts);
      const commissioner = Boolean(
        opts.commission && (opts.commission.manage || opts.commission.member),
      );
      if (
        commissioner &&
        ((initialTab === "commission" && !tabOpened.current) ||
          (!opts.canWrite && !opts.admin))
      ) {
        tabOpened.current = true;
        setTab("commission");
        view = "commission";
      }
      setCategories(opts.categories);
      setRemarkAck(opts.remarkAck);
      const wanted = prefill && !prefilled.current ? prefill : null;
      prefilled.current = true;
      // The server checks the scope again; the pupils it does not offer are dropped here.
      const chosen =
        wanted?.studentIds.filter((id) =>
          opts.students.some((s) => s.id === id),
        ) ?? [];
      const measure = wanted?.measure
        ? measureCategory(
            opts.categories,
            opts,
            opts.students.filter((s) => chosen.includes(s.id)),
          )
        : undefined;
      if (wanted?.measure && !measure)
        setNotice("Воспитательные меры вам недоступны");
      setForm((f) => ({
        ...f,
        day: f.day || opts.today,
        students: wanted ? chosen : f.students,
        categoryId:
          measure?.id ||
          f.categoryId ||
          opts.categories.find(
            (c) => c.active && opts.myLevels.includes(c.level),
          )?.id ||
          "",
        printed: measure
          ? measure.report
          : f.categoryId
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
        setFilters((f) => ({
          ...f,
          studentId: (
            opts.students.find((s) => s.id === studentId) ?? opts.students[0]
          ).id,
        }));
        return;
      }
      if (view === "board") await loadBoard(boardKind);
      else if (view === "summary") await loadSummary();
      else if (view !== "config" && view !== "commission")
        await loadNotes(opts, view);
    } catch (e: any) {
      if (token === sequence.current) {
        setOptions(null);
        setNotes([]);
        setError(e.message);
      }
    } finally {
      if (token === sequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    setHistory(null);
    setOpenId(null);
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
    setActing(true);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(message);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setActing(false);
    }
  }
  async function refreshAfterChange() {
    try {
      await load();
    } catch (e: any) {
      setError("Изменение сохранено, но список не обновился: " + e.message);
    }
  }
  // The form holds something that would be lost, whether it is on show or not (a failed reload hides it, not its content).
  const formUnsaved = () => formDirty(form, formBase.current);
  function leave() {
    const dirty = formUnsaved();
    return (
      (!dirty && !commissionDirty) ||
      window.confirm(
        dirty
          ? UNSAVED_RECORD
          : "Форма экзамена не сохранена. Отбросить её и закрыть?",
      )
    );
  }
  // The form of the next «Новая запись» opens as a new one: nothing chosen, the defaults of the account.
  function resetForm() {
    formBase.current = null;
    setForCourse(true);
    setForm((f) =>
      options
        ? freshForm(f, options)
        : { ...f, text: "", students: [], classId: "" },
    );
  }
  // Back to the list: the form is emptied, after a question when it holds something.
  function closeForm() {
    if (formUnsaved() && !window.confirm(UNSAVED_RECORD)) return;
    resetForm();
    setFormOpen(false);
  }
  async function submit(
    categoryId = form.categoryId,
    text = form.text,
    quick = false,
  ) {
    const body = {
      studentIds: form.students,
      categoryId,
      day: categoryId === form.categoryId ? form.day : options!.today,
      text,
      visible: form.visible,
      ...(course && forCourse ? { subjectId: course.id } : {}),
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
    // A quick mark leaves the form open for the next pupil; a saved record sends the user back to the list.
    if (status && !quick) {
      resetForm();
      setFormOpen(false);
    }
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
      setOpenId(note.id);
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
    if (view !== tab) {
      resetForm();
      setFormOpen(false);
    }
    setTab(view);
    setLetterView(null);
    setHistory(null);
    setOpenId(null);
    void run(() => load(view), "");
  }
  function updateCategory(i: number, change: Partial<Category>) {
    setCategories((all) =>
      all.map((x, j) => (j === i ? { ...x, ...change } : x)),
    );
  }

  const pupils = options?.students || [];
  const exportHref =
    "/api/behaviour/export" +
    query({ workspace }, [
      "classId",
      "studentId",
      "kind",
      "start",
      "end",
      "subjectId",
      "q",
    ]);
  const quick = usable.filter((c) => c.quick);
  const tabs: [string, string][] = [
    ["notes", "Записи"],
    ...(options?.admin
      ? [["queue", `На согласовании (${options.pending})`] as [string, string]]
      : []),
    ["board", "Табло"],
    ["summary", "Сводка"],
    ...(commissionTab
      ? [["commission", "Комиссионные экзамены"] as [string, string]]
      : []),
    ...(options?.admin ? [["config", "Категории"] as [string, string]] : []),
  ];
  const shownTabs = staff ? tabs : tabs.filter(([id]) => id === "commission");
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
          <h2 id="behaviour-title">Официальные письма</h2>
          <p>
            Похвалы, замечания и меры · Записи учителей, классных руководителей
            и администрации
          </p>
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
          <Letterhead school={letterView.school} />
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
      {error && !showForm && <p role="alert">{error}</p>}
      {notice && !showForm && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка…</p>}
      <fieldset disabled={acting}>
        <div className="bn-controls">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => {
                if (!leave()) return;
                // The other workspace has its own categories and day: the form is filled again from its options.
                formBase.current = null;
                setForCourse(true);
                setForm((f) => ({
                  ...f,
                  students: [],
                  text: "",
                  classId: "",
                  categoryId: "",
                  day: "",
                  visible: true,
                  printed: false,
                }));
                setFormOpen(false);
                setFilters(emptyFilters);
                setSearch("");
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
        {options && (staff || commissionTab) && (
          <nav className="bn-controls" aria-label="Разделы">
            {shownTabs.map(([id, title]) => (
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
        {options && !staff && !commissionTab && !options.students.length && (
          <p>
            Для этой учётной записи нет доступных учеников. Похвалы и замечания
            видят учителя учеников, классные руководители и семьи.
          </p>
        )}

        {options && !staff && options.students.length > 0 && (
          <div className="bn-family">
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
          </div>
        )}
        {options?.commission?.family && !staff && filters.studentId && (
          <CommissionExams
            workspace={workspace}
            flags={options.commission}
            studentId={filters.studentId}
            hideDocument={Boolean(letterView)}
            onDocument={() => setLetterView(null)}
          />
        )}

        {showForm && options && (
          <BehaviourNoteForm
            options={options}
            form={form}
            setForm={setForm}
            usable={usable}
            quick={quick}
            course={course}
            forCourse={forCourse}
            setForCourse={setForCourse}
            error={error}
            notice={notice}
            onSubmit={(categoryId, text, isQuick) =>
              void submit(categoryId, text, isQuick)
            }
            onBack={closeForm}
          />
        )}

        <div className="bn-browse" hidden={showForm}>
          {options && staff && tab === "notes" && (
            <div className="bn-toolbar" role="group" aria-label="Фильтры">
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
                Предмет
                <select
                  value={filters.subjectId}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, subjectId: e.target.value }))
                  }
                >
                  <option value="">Все предметы</option>
                  {(options.subjects ?? []).map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                  <option value="none">Без предмета</option>
                </select>
              </label>
              <label className="bn-search">
                Поиск
                <input
                  type="search"
                  value={search}
                  maxLength={100}
                  placeholder="Текст, ученик, автор"
                  onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter") return;
                    e.preventDefault();
                    const text = searchToApply(search, filters.q);
                    if (text !== null)
                      setFilters((f) => (f.q === text ? f : { ...f, q: text }));
                  }}
                />
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
              {options.canWrite && (
                <button
                  ref={newButton}
                  className="primary"
                  onClick={() => {
                    // A message about the list does not belong to a new, empty record.
                    setError("");
                    setNotice("");
                    setFormOpen(true);
                  }}
                >
                  ＋ Новая запись
                </button>
              )}
            </div>
          )}

          {options && (tab === "notes" || tab === "queue" || !staff) && (
            <BehaviourNotesTable
              notes={notes}
              staff={staff}
              canAcknowledge={options.canAcknowledge}
              openId={openId}
              onToggle={(id) => setOpenId((now) => (now === id ? null : id))}
              history={history}
              empty={
                tab === "queue"
                  ? "Мер на согласовании нет."
                  : "Записей пока нет."
              }
              onDecide={(n, approve) => void decide(n, approve)}
              onAcknowledge={(n) => void acknowledge(n)}
              onLetter={(n) => void letter(n)}
              onEdit={(n) => void edit(n)}
              onWithdraw={(n) => void withdraw(n)}
              onHistory={(n) => void showHistory(n)}
            >
              {next && (
                <button
                  // The cursor belongs to the list on show: while another list is on its way it would continue the wrong one.
                  disabled={loading}
                  onClick={() =>
                    options && void run(() => loadNotes(options, tab, true), "")
                  }
                >
                  Показать ещё
                </button>
              )}
            </BehaviourNotesTable>
          )}
        </div>

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

        {options && commissionTab && tab === "commission" && (
          <CommissionExams
            workspace={workspace}
            flags={options.commission!}
            hideDocument={Boolean(letterView)}
            onDocument={() => setLetterView(null)}
            onDirty={setCommissionDirty}
          />
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

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { roleNames, type Account, type Role } from "./auth";
import { keepTyped } from "./formAnswers";
import "./SchoolForms.css";

type Question = {
  id: string;
  title: string;
  kind: "text" | "single" | "multiple" | "consent" | "date" | "number";
  required: boolean;
  choices: string[];
};
type Definition = {
  title: string;
  kind: string;
  description: string;
  questions: Question[];
  audience?: { roles: Role[]; classIds: string[] };
  opensAt: string | null;
  closesAt: string | null;
};
type Form = {
  id: string;
  version: number;
  status: string;
  definition: Definition;
  permission: "admin" | "manage" | "review" | null;
  contexts: { entityId: string; name: string }[];
  accepting: boolean;
  audienceCount?: number;
  counts?: Record<string, number>;
  collaborators?: {
    userId: string;
    name: string;
    permission: string;
    current: boolean;
  }[];
};
type Item = {
  id: string;
  title: string;
  status: string;
  kind: string;
  permission: string | null;
  accepting: boolean;
};
type Value = string | string[] | boolean | number | null;
type Answer = {
  id: string;
  version: number;
  status: string;
  answers: Record<string, Value>;
  comment: string;
  updated: string | null;
  entityId: string;
  name?: string;
  entityName?: string;
  className?: string;
  role?: Role;
  own?: boolean;
};
type History = {
  version: number;
  status: string;
  updated: string;
  comment: string;
}[];
type Options = {
  canCreate: boolean;
  classes: { id: string; name: string }[];
  staff: { id: string; name: string; role: Role }[];
};
type Preview = {
  version: number;
  audienceHash: string;
  accounts: number;
  responses: number;
  roles: Record<string, number>;
};
type Summary = {
  responses: number;
  questions: {
    id: string;
    title: string;
    answered: number;
    choices: Record<string, number>;
  }[];
};
const statuses: Record<string, string> = {
  draft: "Черновик",
  open: "Приём открыт",
  closed: "Приём закрыт",
  archived: "В архиве",
  submitted: "Отправлено",
  withdrawn: "Отозвано",
  returned: "На исправлении",
  approved: "Принято",
  rejected: "Отклонено",
};
const kinds: Record<string, string> = {
  form: "Форма",
  survey: "Опрос",
  consent: "Согласие",
  application: "Заявка",
};
const questionKinds: Record<Question["kind"], string> = {
  text: "Текст",
  single: "Один вариант",
  multiple: "Несколько вариантов",
  consent: "Да / Нет",
  date: "Дата",
  number: "Число",
};
const display = (v: Value | undefined) =>
  v === true
    ? "Да"
    : v === false
      ? "Нет"
      : Array.isArray(v)
        ? v.join("; ")
        : v === null || v === undefined || v === ""
          ? "—"
          : String(v);
const date = (v: string | null) =>
  v ? new Date(v).toLocaleString("ru-RU") : "Не задан";
const localTime = (v: string | null) => {
  if (!v) return "";
  const d = new Date(v);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
const utc = (v: string) => (v ? new Date(v).toISOString() : null);

export default function SchoolForms({
  user,
  initialWorkspace,
  onClose,
}: {
  user: Account;
  initialWorkspace?: string;
  onClose: () => void;
}) {
  const workspaces =
    user.role === "admin"
      ? ["school", "demo"]
      : Array.from(
          new Set([...user.workspaces, ...user.grants.map((g) => g.workspace)]),
        );
  const [workspace, setWorkspace] = useState(
    workspaces.includes(initialWorkspace || "")
      ? initialWorkspace!
      : workspaces.includes("school")
        ? "school"
        : workspaces[0] || "school",
  );
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  const [options, setOptions] = useState<Options>({
    canCreate: false,
    classes: [],
    staff: [],
  });
  const [items, setItems] = useState<Item[]>([]),
    [next, setNext] = useState<number | null>(null);
  const [form, setForm] = useState<Form | null>(null),
    [definition, setDefinition] = useState<Definition | null>(null);
  const [collaborators, setCollaborators] = useState<
    { userId: string; permission: string }[]
  >([]);
  const [definitionDirty, setDefinitionDirty] = useState(false),
    [accessDirty, setAccessDirty] = useState(false),
    [answerDirty, setAnswerDirty] = useState(false);
  const [mode, setMode] = useState<"manage" | "answer" | "responses">("manage");
  const [entity, setEntity] = useState(""),
    [answer, setAnswer] = useState<Answer | null>(null),
    [values, setValues] = useState<Record<string, Value>>({}),
    [history, setHistory] = useState<History>([]);
  const [responses, setResponses] = useState<Answer[]>([]),
    [nextResponse, setNextResponse] = useState<string | null>(null),
    [summary, setSummary] = useState<Summary | null>(null),
    [reviewHistory, setReviewHistory] = useState<Record<string, History>>({});
  const [comments, setComments] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null),
    [deadline, setDeadline] = useState("");
  const [newTitle, setNewTitle] = useState(""),
    [newKind, setNewKind] = useState("form");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const dirty =
    definitionDirty ||
    accessDirty ||
    answerDirty ||
    Object.values(comments).some(Boolean);
  const mayLeave = () =>
    !dirty ||
    window.confirm("Есть несохранённые изменения. Закрыть их без сохранения?");
  function clear() {
    setForm(null);
    setDefinition(null);
    setAnswer(null);
    setValues({});
    setResponses([]);
    setSummary(null);
    setHistory([]);
    setReviewHistory({});
    setComments({});
    setDefinitionDirty(false);
    setAccessDirty(false);
    setAnswerDirty(false);
    setPreview(null);
  }
  async function run(task: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      if ([401, 403, 404].includes(e.status)) clear();
    } finally {
      setBusy(false);
    }
  }
  async function mutate(path: string, method: string, body: unknown) {
    const key = JSON.stringify([workspace, path, method, body]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    return api(path, workspace, method, {
      ...(body as object),
      requestId: replay.current.id,
    });
  }
  async function refreshList(before?: number) {
    const result = await api(
      `forms/items${before ? `?before=${before}` : ""}`,
      workspace,
    );
    setItems((old) => (before ? [...old, ...result.items] : result.items));
    setNext(result.nextBefore);
  }
  async function loadAnswer(selected: Form, entityId: string) {
    const result = await api(
      `forms/items/${selected.id}/answer?entityId=${encodeURIComponent(entityId)}`,
      workspace,
    );
    setAnswer(result.answer);
    setValues(result.answer.answers);
    setHistory(result.history);
    setAnswerDirty(false);
    setEntity(entityId);
    return result.answer as Answer;
  }
  async function loadResponses(
    selected: Form,
    after?: string,
    reviewed?: string,
  ) {
    const result = await api(
      `forms/items/${selected.id}/responses?limit=20${after ? `&after=${encodeURIComponent(after)}` : ""}`,
      workspace,
    );
    setResponses((old) => (after ? [...old, ...result.items] : result.items));
    setNextResponse(result.nextAfter);
    if (!after) {
      setSummary(await api(`forms/items/${selected.id}/summary`, workspace));
      setReviewHistory({});
      setComments((old) =>
        reviewed
          ? Object.fromEntries(
              Object.entries(old).filter(([id]) => id !== reviewed),
            )
          : {},
      );
    }
  }
  async function open(
    id: string,
    preferred?: "manage" | "answer" | "responses",
  ) {
    const selected = (await api(`forms/items/${id}`, workspace)) as Form;
    clear();
    setForm(selected);
    setDefinition(structuredClone(selected.definition));
    setCollaborators(
      (selected.collaborators || []).map((c) => ({
        userId: c.userId,
        permission: c.permission,
      })),
    );
    setDeadline(localTime(selected.definition.closesAt));
    const view =
      preferred ||
      (selected.permission === "review"
        ? "responses"
        : selected.permission
          ? "manage"
          : "answer");
    setMode(view);
    let fresh: Answer | null = null;
    if (view === "answer" && selected.contexts.length)
      fresh = await loadAnswer(
        selected,
        selected.id === form?.id &&
          selected.contexts.some((c) => c.entityId === entity)
          ? entity
          : selected.contexts[0].entityId,
      );
    if (view === "responses") await loadResponses(selected);
    return { selected, answer: fresh };
  }
  // The fresh form replaces what was loaded, but answers typed since then stay on screen.
  async function reloadKeepingAnswers() {
    if (!form) return;
    const typed = values,
      baseline = answer?.answers || {},
      before = answer?.version;
    const { selected, answer: fresh } = await open(form.id, "answer");
    setValues((old) =>
      keepTyped(
        old,
        typed,
        baseline,
        selected.definition.questions.map((q) => q.id),
      ),
    );
    setAnswerDirty(true);
    setNotice(
      fresh && fresh.version !== before
        ? "Ответ изменён в другом окне. Ваш несохранённый текст оставлен на экране — проверьте поля перед отправкой."
        : "Загружена свежая версия формы. Несохранённые ответы оставлены на экране.",
    );
  }
  async function completed(
    id: string,
    view?: "manage" | "answer" | "responses",
    message = "Сохранено.",
  ) {
    await refreshList();
    await open(id, view);
    setNotice(message);
  }
  useEffect(() => {
    dialog.current?.showModal();
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
  useEffect(() => {
    const current = ++sequence.current;
    clear();
    setItems([]);
    setError("");
    setBusy(true);
    Promise.all([
      api("forms/options", workspace),
      api("forms/items", workspace),
    ])
      .then(([o, l]) => {
        if (current === sequence.current) {
          setOptions(o);
          setItems(l.items);
          setNext(l.nextBefore);
        }
      })
      .catch((e) => {
        if (current === sequence.current) setError(e.message);
      })
      .finally(() => {
        if (current === sequence.current) setBusy(false);
      });
    return () => {
      sequence.current++;
    };
  }, [workspace]);
  const change = (patch: Partial<Definition>) => {
    setDefinition((d) => (d ? { ...d, ...patch } : d));
    setDefinitionDirty(true);
    setPreview(null);
  };
  const question = (index: number, patch: Partial<Question>) => {
    if (definition)
      change({
        questions: definition.questions.map((q, i) =>
          i === index ? { ...q, ...patch } : q,
        ),
      });
  };
  const setValue = (id: string, value: Value) => {
    setValues((v) => ({ ...v, [id]: value }));
    setAnswerDirty(true);
  };
  async function transition(action: string) {
    if (!form) return;
    await mutate(`forms/items/${form.id}/transition`, "POST", {
      version: form.version,
      action,
      ...(action === "publish" ? { audienceHash: preview?.audienceHash } : {}),
      ...(action === "reopen" ? { closesAt: utc(deadline) } : {}),
    });
    await completed(
      form.id,
      "manage",
      action === "publish"
        ? "Форма опубликована."
        : "Состояние формы изменено.",
    );
  }
  async function sendAnswer(action: string) {
    if (!form || !answer) return;
    await mutate(`forms/items/${form.id}/answer`, "PUT", {
      version: answer.version,
      formVersion: form.version,
      entityId: entity,
      action,
      answers: values,
    });
    const selected = await api(`forms/items/${form.id}`, workspace);
    setForm(selected);
    await loadAnswer(selected, entity);
    setNotice(
      action === "save"
        ? "Черновик сохранён. Ответственные его не видят."
        : action === "withdraw"
          ? "Ответ отозван."
          : "Ответ отправлен.",
    );
  }
  async function download() {
    if (!form) return;
    const result = await fetch(
      `/api/forms/items/${form.id}/export?workspace=${workspace}`,
    );
    if (!result.ok) {
      const value = await result.json();
      throw Object.assign(
        new Error(value.detail || "Не удалось скачать результаты."),
        { status: result.status },
      );
    }
    const url = URL.createObjectURL(await result.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "school-form.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  // A returned application stays correctable after the deadline or a manual close; a decided one needs open intake.
  const returned = answer?.status === "returned";
  const decided =
    answer?.status === "approved" || answer?.status === "rejected";
  const editable =
    !!form &&
    !!answer &&
    answer.status !== "submitted" &&
    (form.accepting || (returned && ["open", "closed"].includes(form.status)));
  return (
    <dialog
      ref={dialog}
      className="school-forms-dialog"
      aria-labelledby="school-forms-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && mayLeave()) onClose();
      }}
    >
      <header className="school-forms-header">
        <h2 id="school-forms-title">Формы и опросы</h2>
        <button
          disabled={busy}
          onClick={() => {
            if (mayLeave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      <div className="school-forms-toolbar">
        <label>
          Пространство
          <select
            value={workspace}
            disabled={busy}
            onChange={(e) => {
              if (mayLeave()) setWorkspace(e.target.value);
            }}
          >
            {workspaces.map((w) => (
              <option key={w} value={w}>
                {w === "school" ? "Школа" : "Демонстрация"}
              </option>
            ))}
          </select>
        </label>
        <button disabled={busy} onClick={() => void run(() => refreshList())}>
          Обновить список
        </button>
      </div>
      {error && (
        <p className="school-forms-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="school-forms-notice" role="status">
          {notice}
        </p>
      )}
      {busy && <p role="status">Выполняю действие…</p>}
      <div className="school-forms-layout">
        <aside aria-label="Список форм">
          {options.canCreate && (
            <form
              className="school-form-create"
              onSubmit={(e) => {
                e.preventDefault();
                if (!mayLeave()) return;
                void run(async () => {
                  const created = await mutate("forms/items", "POST", {
                    title: newTitle,
                    kind: newKind,
                  });
                  setNewTitle("");
                  await completed(
                    created.id,
                    "manage",
                    "Карточка создана. Заполните форму и назначьте ответственных.",
                  );
                });
              }}
            >
              <label>
                Новая форма
                <input
                  required
                  maxLength={160}
                  value={newTitle}
                  disabled={busy}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="Название"
                />
              </label>
              <label>
                Тип
                <select
                  value={newKind}
                  onChange={(e) => setNewKind(e.target.value)}
                  disabled={busy}
                >
                  {Object.entries(kinds).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <button disabled={busy || !newTitle.trim()}>
                Создать карточку
              </button>
            </form>
          )}
          {!items.length && !busy && <p>Доступных форм пока нет.</p>}
          <div className="school-form-list">
            {items.map((item) => (
              <button
                key={item.id}
                disabled={busy}
                aria-pressed={form?.id === item.id}
                onClick={() => {
                  if (mayLeave()) void run(() => open(item.id));
                }}
              >
                <strong>{item.title}</strong>
                <small>
                  {kinds[item.kind]} · {statuses[item.status]}
                  {item.status === "open" && !item.accepting
                    ? " · Сейчас ответы не принимаются"
                    : ""}
                </small>
                {item.permission && (
                  <small>
                    {item.permission === "review"
                      ? "Просмотр и рассмотрение"
                      : "Управление формой"}
                  </small>
                )}
              </button>
            ))}
          </div>
          {next && (
            <button
              disabled={busy}
              onClick={() => void run(() => refreshList(next))}
            >
              Показать ещё
            </button>
          )}
        </aside>
        <main className="school-form-main">
          {!form && <p>Выберите форму из списка.</p>}
          {form && definition && (
            <>
              <div className="school-form-heading">
                <h3>{form.definition.title}</h3>
                <span>
                  {statuses[form.status]} · редакция {form.version}
                </span>
              </div>
              <nav className="school-forms-toolbar" aria-label="Разделы формы">
                {form.permission && form.permission !== "review" && (
                  <button
                    disabled={busy}
                    aria-pressed={mode === "manage"}
                    onClick={() => {
                      if (mayLeave()) void run(() => open(form.id, "manage"));
                    }}
                  >
                    Управление
                  </button>
                )}
                {form.permission && (
                  <button
                    disabled={busy}
                    aria-pressed={mode === "responses"}
                    onClick={() => {
                      if (mayLeave())
                        void run(() => open(form.id, "responses"));
                    }}
                  >
                    Результаты
                  </button>
                )}
                {form.contexts.length > 0 && (
                  <button
                    disabled={busy}
                    aria-pressed={mode === "answer"}
                    onClick={() => {
                      if (mayLeave()) void run(() => open(form.id, "answer"));
                    }}
                  >
                    Мой ответ
                  </button>
                )}
                <button
                  disabled={busy}
                  onClick={() => {
                    if (
                      mode === "answer" &&
                      answerDirty &&
                      !definitionDirty &&
                      !accessDirty &&
                      !Object.values(comments).some(Boolean)
                    )
                      void run(reloadKeepingAnswers);
                    else if (mayLeave()) void run(() => open(form.id, mode));
                  }}
                >
                  Загрузить свежую версию
                </button>
              </nav>
              {mode === "manage" &&
                form.permission &&
                form.permission !== "review" && (
                  <>
                    {form.status === "draft" ? (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          void run(async () => {
                            const prepared = {
                              ...definition,
                              questions: definition.questions.map((q) => ({
                                ...q,
                                choices: q.choices
                                  .map((v) => v.trim())
                                  .filter(Boolean),
                              })),
                            };
                            await mutate(`forms/items/${form.id}`, "PUT", {
                              version: form.version,
                              definition: prepared,
                            });
                            await completed(form.id, "manage");
                          });
                        }}
                      >
                        <fieldset disabled={busy || accessDirty}>
                          <legend>Содержание формы</legend>
                          <label>
                            Название
                            <input
                              required
                              maxLength={160}
                              value={definition.title}
                              onChange={(e) =>
                                change({ title: e.target.value })
                              }
                            />
                          </label>
                          <label>
                            Описание
                            <textarea
                              maxLength={6000}
                              value={definition.description}
                              onChange={(e) =>
                                change({ description: e.target.value })
                              }
                            />
                          </label>
                          <div className="school-form-dates">
                            <label>
                              Начало приёма
                              <input
                                type="datetime-local"
                                value={localTime(definition.opensAt)}
                                onChange={(e) =>
                                  change({ opensAt: utc(e.target.value) })
                                }
                              />
                            </label>
                            <label>
                              Ответить до
                              <input
                                type="datetime-local"
                                value={localTime(definition.closesAt)}
                                onChange={(e) =>
                                  change({ closesAt: utc(e.target.value) })
                                }
                              />
                            </label>
                          </div>
                          <small>
                            Время показано в часовом поясе этого устройства.
                            Пустой срок — без ограничения.
                          </small>
                          {definition.questions.map((q, index) => (
                            <section
                              className="school-form-question"
                              key={q.id}
                            >
                              <h4>Вопрос {index + 1}</h4>
                              <label>
                                Текст вопроса
                                <input
                                  required
                                  maxLength={300}
                                  value={q.title}
                                  onChange={(e) =>
                                    question(index, { title: e.target.value })
                                  }
                                />
                              </label>
                              <label>
                                Тип ответа
                                <select
                                  value={q.kind}
                                  onChange={(e) => {
                                    const kind = e.target
                                      .value as Question["kind"];
                                    question(index, {
                                      kind,
                                      choices: ["single", "multiple"].includes(
                                        kind,
                                      )
                                        ? ["Вариант 1", "Вариант 2"]
                                        : [],
                                    });
                                  }}
                                >
                                  {Object.entries(questionKinds).map(
                                    ([key, label]) => (
                                      <option key={key} value={key}>
                                        {label}
                                      </option>
                                    ),
                                  )}
                                </select>
                              </label>
                              <label className="school-form-check">
                                <input
                                  type="checkbox"
                                  checked={q.required}
                                  onChange={(e) =>
                                    question(index, {
                                      required: e.target.checked,
                                    })
                                  }
                                />
                                Обязательный вопрос
                              </label>
                              {["single", "multiple"].includes(q.kind) && (
                                <label>
                                  Варианты — по одному на строку
                                  <textarea
                                    value={q.choices.join("\n")}
                                    onChange={(e) =>
                                      question(index, {
                                        choices: e.target.value.split("\n"),
                                      })
                                    }
                                  />
                                </label>
                              )}
                              <div className="school-forms-toolbar">
                                <button
                                  type="button"
                                  disabled={index === 0}
                                  onClick={() => {
                                    const qs = [...definition.questions];
                                    [qs[index - 1], qs[index]] = [
                                      qs[index],
                                      qs[index - 1],
                                    ];
                                    change({ questions: qs });
                                  }}
                                >
                                  Выше
                                </button>
                                <button
                                  type="button"
                                  disabled={
                                    index === definition.questions.length - 1
                                  }
                                  onClick={() => {
                                    const qs = [...definition.questions];
                                    [qs[index + 1], qs[index]] = [
                                      qs[index],
                                      qs[index + 1],
                                    ];
                                    change({ questions: qs });
                                  }}
                                >
                                  Ниже
                                </button>
                                <button
                                  type="button"
                                  onClick={() =>
                                    change({
                                      questions: definition.questions.filter(
                                        (_, i) => i !== index,
                                      ),
                                    })
                                  }
                                >
                                  Удалить вопрос
                                </button>
                              </div>
                            </section>
                          ))}
                          <button
                            type="button"
                            disabled={definition.questions.length >= 30}
                            onClick={() =>
                              change({
                                questions: [
                                  ...definition.questions,
                                  {
                                    id: crypto.randomUUID(),
                                    title: "",
                                    kind: "text",
                                    required: true,
                                    choices: [],
                                  },
                                ],
                              })
                            }
                          >
                            Добавить вопрос
                          </button>
                          {form.permission === "admin" &&
                            definition.audience && (
                              <fieldset>
                                <legend>Аудитория</legend>
                                <p>
                                  Классы ограничивают учеников и родителей.
                                  Выбранные сотрудники получают форму независимо
                                  от класса.
                                </p>
                                <div className="school-form-checks">
                                  {(Object.keys(roleNames) as Role[]).map(
                                    (role) => (
                                      <label
                                        className="school-form-check"
                                        key={role}
                                      >
                                        <input
                                          type="checkbox"
                                          checked={definition.audience!.roles.includes(
                                            role,
                                          )}
                                          onChange={(e) =>
                                            change({
                                              audience: {
                                                ...definition.audience!,
                                                roles: e.target.checked
                                                  ? [
                                                      ...definition.audience!
                                                        .roles,
                                                      role,
                                                    ]
                                                  : definition.audience!.roles.filter(
                                                      (r) => r !== role,
                                                    ),
                                              },
                                            })
                                          }
                                        />
                                        {roleNames[role]}
                                      </label>
                                    ),
                                  )}
                                </div>
                                <p>
                                  Без выбранных классов — все действующие
                                  ученики выбранных ролей.
                                </p>
                                <div className="school-form-checks">
                                  {options.classes.map((c) => (
                                    <label
                                      className="school-form-check"
                                      key={c.id}
                                    >
                                      <input
                                        type="checkbox"
                                        checked={definition.audience!.classIds.includes(
                                          c.id,
                                        )}
                                        onChange={(e) =>
                                          change({
                                            audience: {
                                              ...definition.audience!,
                                              classIds: e.target.checked
                                                ? [
                                                    ...definition.audience!
                                                      .classIds,
                                                    c.id,
                                                  ]
                                                : definition.audience!.classIds.filter(
                                                    (id) => id !== c.id,
                                                  ),
                                            },
                                          })
                                        }
                                      />
                                      {c.name}
                                    </label>
                                  ))}
                                </div>
                              </fieldset>
                            )}
                          {form.permission !== "admin" && (
                            <p>
                              Аудиторию задаёт администратор. Перед публикацией
                              проверьте число адресатов.
                            </p>
                          )}
                          <button
                            className="primary"
                            disabled={!definitionDirty}
                          >
                            Сохранить форму
                          </button>
                        </fieldset>
                      </form>
                    ) : (
                      <>
                        <p>{form.definition.description}</p>
                        <p>
                          Начало: {date(form.definition.opensAt)}. Окончание:{" "}
                          {date(form.definition.closesAt)}.
                        </p>
                        <p>
                          Вопросы после публикации не меняются. Ответы привязаны
                          к опубликованной форме.
                        </p>
                        <ol>
                          {form.definition.questions.map((q) => (
                            <li key={q.id}>
                              {q.title} · {questionKinds[q.kind]}
                              {q.required ? " · обязательный" : ""}
                            </li>
                          ))}
                        </ol>
                      </>
                    )}
                    {form.permission === "admin" && (
                      <fieldset disabled={busy || definitionDirty}>
                        <legend>Ответственные за эту форму</legend>
                        <p>
                          Управление включает подготовку и публикацию формы,
                          чтение ответов и решения по заявкам. Просмотр и
                          рассмотрение не дают менять форму. Аудиторию и
                          сотрудников назначает администратор.
                        </p>
                        {form.collaborators?.some((c) => !c.current) && (
                          <p className="school-forms-error">
                            Часть назначений утратила силу после изменения
                            доступа аккаунта. Проверьте и сохраните список
                            заново.
                          </p>
                        )}
                        {collaborators.map((c, index) => (
                          <div className="school-form-collaborator" key={index}>
                            <label>
                              Сотрудник
                              <select
                                value={c.userId}
                                onChange={(e) => {
                                  setCollaborators((v) =>
                                    v.map((x, i) =>
                                      i === index
                                        ? { ...x, userId: e.target.value }
                                        : x,
                                    ),
                                  );
                                  setAccessDirty(true);
                                }}
                              >
                                <option value="">Выберите сотрудника</option>
                                {options.staff.map((s) => (
                                  <option key={s.id} value={s.id}>
                                    {s.name} · {roleNames[s.role]}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label>
                              Доступ
                              <select
                                value={c.permission}
                                onChange={(e) => {
                                  setCollaborators((v) =>
                                    v.map((x, i) =>
                                      i === index
                                        ? { ...x, permission: e.target.value }
                                        : x,
                                    ),
                                  );
                                  setAccessDirty(true);
                                }}
                              >
                                <option value="manage">Управление</option>
                                <option value="review">
                                  Просмотр и рассмотрение
                                </option>
                              </select>
                            </label>
                            <button
                              onClick={() => {
                                setCollaborators((v) =>
                                  v.filter((_, i) => i !== index),
                                );
                                setAccessDirty(true);
                              }}
                            >
                              Убрать
                            </button>
                          </div>
                        ))}
                        <div className="school-forms-toolbar">
                          <button
                            disabled={collaborators.length >= 20}
                            onClick={() => {
                              setCollaborators((v) => [
                                ...v,
                                { userId: "", permission: "manage" },
                              ]);
                              setAccessDirty(true);
                            }}
                          >
                            Добавить ответственного
                          </button>
                          <button
                            disabled={
                              !accessDirty ||
                              collaborators.some((c) => !c.userId)
                            }
                            onClick={() =>
                              void run(async () => {
                                await mutate(
                                  `forms/items/${form.id}/access`,
                                  "PUT",
                                  { version: form.version, collaborators },
                                );
                                await completed(
                                  form.id,
                                  "manage",
                                  "Доступ к форме сохранён.",
                                );
                              })
                            }
                          >
                            Сохранить доступ
                          </button>
                        </div>
                      </fieldset>
                    )}
                    {dirty && (
                      <p className="school-forms-notice">
                        Сохраните изменения перед публикацией или изменением
                        состояния.
                      </p>
                    )}
                    <fieldset disabled={busy || dirty}>
                      <legend>Публикация и состояние</legend>
                      {form.status === "draft" && (
                        <>
                          <button
                            onClick={() =>
                              void run(async () =>
                                setPreview(
                                  await api(
                                    `forms/items/${form.id}/preview`,
                                    workspace,
                                  ),
                                ),
                              )
                            }
                          >
                            Проверить аудиторию
                          </button>
                          {preview && (
                            <div className="school-form-preview">
                              <p>
                                Аккаунтов: <strong>{preview.accounts}</strong>.
                                Возможных ответов:{" "}
                                <strong>{preview.responses}</strong>.
                              </p>
                              <p>
                                {Object.entries(preview.roles)
                                  .map(
                                    ([role, count]) =>
                                      `${roleNames[role as Role]}: ${count}`,
                                  )
                                  .join(" · ")}
                              </p>
                              <p>
                                Родитель отвечает отдельно за каждого своего
                                ребёнка. Ответы разных родителей сохраняются
                                отдельно.
                              </p>
                              <button
                                className="primary"
                                disabled={!preview.responses}
                                onClick={() =>
                                  void run(() => transition("publish"))
                                }
                              >
                                Опубликовать для этой аудитории
                              </button>
                            </div>
                          )}
                        </>
                      )}
                      {form.status === "open" && (
                        <button
                          onClick={() => void run(() => transition("close"))}
                        >
                          Закрыть приём
                        </button>
                      )}
                      {["open", "closed"].includes(form.status) && (
                        <div className="school-forms-toolbar">
                          <label>
                            Новый срок ответа
                            <input
                              type="datetime-local"
                              value={deadline}
                              onChange={(e) => setDeadline(e.target.value)}
                            />
                          </label>
                          <button
                            onClick={() => void run(() => transition("reopen"))}
                          >
                            Открыть приём с этим сроком
                          </button>
                        </div>
                      )}
                      {form.status !== "archived" && (
                        <button
                          onClick={() => {
                            if (
                              window.confirm(
                                "Перенести форму в архив и прекратить приём ответов?",
                              )
                            )
                              void run(() => transition("archive"));
                          }}
                        >
                          В архив
                        </button>
                      )}
                      {form.permission === "admin" && (
                        <button
                          onClick={() =>
                            void run(async () => {
                              const copied = await mutate(
                                `forms/items/${form.id}/copy`,
                                "POST",
                                { version: form.version },
                              );
                              await completed(
                                copied.id,
                                "manage",
                                "Создан новый черновик. Ответы и ответственные не копировались.",
                              );
                            })
                          }
                        >
                          Скопировать в новый черновик
                        </button>
                      )}
                    </fieldset>
                  </>
                )}
              {mode === "answer" && (
                <>
                  <p>{form.definition.description}</p>
                  <p>
                    Именной ответ от аккаунта <strong>{user.name}</strong>.
                    Ответить до: {date(form.definition.closesAt)}.
                  </p>
                  <label>
                    За кого отвечаете
                    <select
                      value={entity}
                      disabled={busy}
                      onChange={(e) => {
                        if (mayLeave())
                          void run(() => loadAnswer(form, e.target.value));
                      }}
                    >
                      {form.contexts.map((c) => (
                        <option key={c.entityId} value={c.entityId}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!form.accepting &&
                    (editable ? (
                      <p className="school-forms-notice">
                        Заявку, возвращённую на исправление, можно отправить
                        повторно и после окончания приёма.
                      </p>
                    ) : (
                      <p className="school-forms-notice">
                        Сейчас ответы не принимаются. Начало:{" "}
                        {date(form.definition.opensAt)}.
                      </p>
                    ))}
                  {decided && editable && (
                    <p className="school-forms-notice">
                      Решение по прошлой заявке сохранено в истории. Отправка
                      создаст новую заявку.
                    </p>
                  )}
                  {answer && (
                    <>
                      <p>
                        Статус: <strong>{statuses[answer.status]}</strong>.{" "}
                        {answer.updated && `Изменено ${date(answer.updated)}.`}
                      </p>
                      {answer.comment && (
                        <p className="school-forms-notice">
                          Комментарий ответственного: {answer.comment}
                        </p>
                      )}
                      <fieldset disabled={busy || !editable}>
                        <legend>Ваши ответы</legend>
                        {form.definition.questions.map((q, index) => (
                          <div className="school-form-question" key={q.id}>
                            <label htmlFor={`answer-${q.id}`}>
                              {index + 1}. {q.title}
                              {q.required ? " *" : ""}
                            </label>
                            {q.kind === "text" ? (
                              <textarea
                                id={`answer-${q.id}`}
                                value={String(values[q.id] ?? "")}
                                maxLength={4000}
                                onChange={(e) => setValue(q.id, e.target.value)}
                              />
                            ) : q.kind === "single" ? (
                              <select
                                id={`answer-${q.id}`}
                                value={String(values[q.id] ?? "")}
                                onChange={(e) => setValue(q.id, e.target.value)}
                              >
                                <option value="">Выберите вариант</option>
                                {q.choices.map((c) => (
                                  <option key={c}>{c}</option>
                                ))}
                              </select>
                            ) : q.kind === "multiple" ? (
                              <div role="group" aria-label={q.title}>
                                {q.choices.map((c) => (
                                  <label key={c} className="school-form-check">
                                    <input
                                      type="checkbox"
                                      checked={
                                        Array.isArray(values[q.id]) &&
                                        (values[q.id] as string[]).includes(c)
                                      }
                                      onChange={(e) => {
                                        const selected = Array.isArray(
                                          values[q.id],
                                        )
                                          ? (values[q.id] as string[])
                                          : [];
                                        setValue(
                                          q.id,
                                          e.target.checked
                                            ? [...selected, c]
                                            : selected.filter((v) => v !== c),
                                        );
                                      }}
                                    />
                                    {c}
                                  </label>
                                ))}
                              </div>
                            ) : q.kind === "consent" ? (
                              <select
                                id={`answer-${q.id}`}
                                value={
                                  values[q.id] === true
                                    ? "yes"
                                    : values[q.id] === false
                                      ? "no"
                                      : ""
                                }
                                onChange={(e) =>
                                  setValue(
                                    q.id,
                                    e.target.value === ""
                                      ? null
                                      : e.target.value === "yes",
                                  )
                                }
                              >
                                <option value="">Выберите ответ</option>
                                <option value="yes">
                                  Да, согласен / согласна
                                </option>
                                <option value="no">
                                  Нет, не согласен / не согласна
                                </option>
                              </select>
                            ) : (
                              <input
                                id={`answer-${q.id}`}
                                type={q.kind === "date" ? "date" : "number"}
                                step={q.kind === "number" ? "any" : undefined}
                                value={String(values[q.id] ?? "")}
                                onChange={(e) =>
                                  setValue(
                                    q.id,
                                    e.target.value === ""
                                      ? null
                                      : q.kind === "number"
                                        ? Number(e.target.value)
                                        : e.target.value,
                                  )
                                }
                              />
                            )}
                          </div>
                        ))}
                        <div className="school-forms-toolbar">
                          <button
                            onClick={() => void run(() => sendAnswer("save"))}
                          >
                            Сохранить черновик
                          </button>
                          <button
                            className="primary"
                            onClick={() => void run(() => sendAnswer("submit"))}
                          >
                            {decided
                              ? "Отправить новую заявку"
                              : "Отправить ответ"}
                          </button>
                        </div>
                        <p>* Обязательный вопрос. Черновик виден только вам.</p>
                      </fieldset>
                      {answer.status === "submitted" && form.accepting && (
                        <button
                          disabled={busy}
                          onClick={() => {
                            if (window.confirm("Отозвать ответ для изменения?"))
                              void run(() => sendAnswer("withdraw"));
                          }}
                        >
                          Отозвать ответ
                        </button>
                      )}
                      {history.length > 0 && (
                        <details>
                          <summary>История ответа</summary>
                          <ul>
                            {history.map((h) => (
                              <li key={h.version}>
                                {date(h.updated)} · {statuses[h.status]}
                                {h.comment ? ` · ${h.comment}` : ""}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </>
                  )}
                </>
              )}
              {mode === "responses" && form.permission && (
                <>
                  <p>
                    Адресатов при публикации: {form.audienceCount ?? 0}. Ответы
                    родителей за разных детей и ответы разных родителей
                    считаются отдельно.
                  </p>
                  <p>
                    Черновики не видны ответственным. Отозванные ответы
                    исключены из сводки и CSV.
                  </p>
                  <div className="school-forms-toolbar">
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (mayLeave()) void run(() => loadResponses(form));
                      }}
                    >
                      Обновить результаты
                    </button>
                    <button disabled={busy} onClick={() => void run(download)}>
                      Скачать CSV
                    </button>
                  </div>
                  {summary && (
                    <details>
                      <summary>Сводка ответов: {summary.responses}</summary>
                      {summary.questions.map((q) => (
                        <div className="school-form-question" key={q.id}>
                          <strong>{q.title}</strong>
                          <p>Ответили: {q.answered}</p>
                          {Object.entries(q.choices).map(([choice, count]) => (
                            <p key={choice}>
                              {choice}: <strong>{count}</strong>
                            </p>
                          ))}
                        </div>
                      ))}
                    </details>
                  )}
                  {!responses.length && <p>Отправленных ответов пока нет.</p>}
                  {responses.map((r) => (
                    <article className="school-form-response" key={r.id}>
                      <h4>
                        {r.name}
                        {r.entityName ? ` · ${r.entityName}` : ""}
                        {r.className ? ` · ${r.className}` : ""}
                      </h4>
                      <p>
                        {r.role ? roleNames[r.role] : ""} · {statuses[r.status]}{" "}
                        · {date(r.updated)}
                      </p>
                      {r.comment && <p>Решение: {r.comment}</p>}
                      {r.status !== "withdrawn" && (
                        <details>
                          <summary>Открыть ответ</summary>
                          <dl>
                            {form.definition.questions.map((q) => (
                              <div key={q.id}>
                                <dt>{q.title}</dt>
                                <dd>{display(r.answers[q.id])}</dd>
                              </div>
                            ))}
                          </dl>
                        </details>
                      )}
                      {form.definition.kind === "application" &&
                        r.status === "submitted" &&
                        form.status !== "archived" &&
                        r.own && (
                          <p>Свою заявку рассматривает другой ответственный.</p>
                        )}
                      {form.definition.kind === "application" &&
                        r.status === "submitted" &&
                        form.status !== "archived" &&
                        !r.own && (
                          <fieldset disabled={busy}>
                            <legend>Решение по заявке</legend>
                            <label>
                              Комментарий
                              <textarea
                                maxLength={2000}
                                value={comments[r.id] || ""}
                                onChange={(e) =>
                                  setComments((v) => ({
                                    ...v,
                                    [r.id]: e.target.value,
                                  }))
                                }
                              />
                            </label>
                            <div className="school-forms-toolbar">
                              {(
                                ["approved", "rejected", "returned"] as const
                              ).map((action) => (
                                <button
                                  key={action}
                                  onClick={() =>
                                    void run(async () => {
                                      await mutate(
                                        `forms/items/${form.id}/responses/${r.id}/review`,
                                        "POST",
                                        {
                                          version: r.version,
                                          formVersion: form.version,
                                          action,
                                          comment: comments[r.id] || "",
                                        },
                                      );
                                      await loadResponses(
                                        form,
                                        undefined,
                                        r.id,
                                      );
                                      setNotice("Решение сохранено.");
                                    })
                                  }
                                >
                                  {action === "approved"
                                    ? "Принять"
                                    : action === "rejected"
                                      ? "Отклонить"
                                      : "Вернуть на исправление"}
                                </button>
                              ))}
                            </div>
                          </fieldset>
                        )}
                      <button
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            const result = await api(
                              `forms/items/${form.id}/responses/${r.id}/history`,
                              workspace,
                            );
                            setReviewHistory((v) => ({ ...v, [r.id]: result }));
                          })
                        }
                      >
                        История решений
                      </button>
                      {reviewHistory[r.id] && (
                        <ul>
                          {reviewHistory[r.id].map((h) => (
                            <li key={h.version}>
                              {date(h.updated)} · {statuses[h.status]}
                              {h.comment ? ` · ${h.comment}` : ""}
                            </li>
                          ))}
                        </ul>
                      )}
                    </article>
                  ))}
                  {nextResponse && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(() => loadResponses(form, nextResponse))
                      }
                    >
                      Ещё ответы
                    </button>
                  )}
                </>
              )}
            </>
          )}
        </main>
      </div>
    </dialog>
  );
}

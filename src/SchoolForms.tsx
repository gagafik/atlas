import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { roleNames, type Account, type Role } from "./auth";
import {
  choiceClosed,
  choiceLocked,
  holdsChoice,
  keepTyped,
  limitsFor,
} from "./formAnswers";
import "./SchoolForms.css";

type Question = {
  id: string;
  title: string;
  kind: "text" | "single" | "multiple" | "consent" | "date" | "number";
  required: boolean;
  choices: string[];
  limits?: Record<string, number>;
};
type Preset = "roles" | "teachers" | "class_teachers" | "staff" | "saved";
type Definition = {
  title: string;
  kind: string;
  description: string;
  questions: Question[];
  audience?: {
    roles: Role[];
    classIds: string[];
    preset?: Preset;
    savedGroupId?: string | null;
  };
  opensAt: string | null;
  closesAt: string | null;
  standing?: boolean;
  anonymous?: boolean;
};
type Seats = Record<string, Record<string, number>>;
type Form = {
  id: string;
  version: number;
  status: string;
  definition: Definition;
  permission: "admin" | "manage" | "review" | null;
  contexts: { entityId: string; name: string }[];
  accepting: boolean;
  seatsLeft?: Seats;
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
  standing?: boolean;
  anonymous?: boolean;
  contexts?: { entityId: string; name: string; status: string }[];
};
type Value = string | string[] | boolean | number | null;
type Answer = {
  id: string;
  version: number;
  status: string;
  answers: Record<string, Value>;
  // The version that holds the caller's seats: `answers` may carry a private correction instead.
  heldAnswers?: Record<string, Value>;
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
  savedGroups: { id: string; name: string }[];
};
type Preview = {
  version: number;
  audienceHash: string;
  accounts: number;
  responses: number;
  roles: Record<string, number>;
  unavailable?: number;
};
type Summary = {
  responses: number;
  withheld?: boolean;
  questions: {
    id: string;
    title: string;
    answered: number;
    choices: Record<string, number>;
  }[];
};
type Pending = {
  applicable: boolean;
  anonymous: boolean;
  audience: number;
  answered: number;
  pendingCount: number;
  remindedToday: number;
  canRemind: boolean;
  items: {
    name: string;
    role: Role;
    entityName: string;
    className: string;
    remindedToday: boolean;
  }[];
  nextOffset: number | null;
};
const presets: Record<Preset, string> = {
  roles: "По ролям и классам",
  teachers: "Все учителя",
  class_teachers: "Классные руководители",
  staff: "Сотрудники",
  saved: "Сохранённая группа",
};
const statuses: Record<string, string> = {
  none: "Ждёт ответа",
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
const ANONYMOUS_MIN = 5; // as on the server: fewest people whose answers an anonymous survey shows
const audienceText = (d: Definition) => {
  const a = d.audience,
    preset = a?.preset ?? "roles";
  return `${
    preset === "roles"
      ? `роли — ${(a?.roles || []).map((r) => roleNames[r]).join(", ") || "не выбраны"}`
      : presets[preset].toLowerCase()
  }${d.standing ? "; постоянная форма" : ""}${d.anonymous ? "; анонимный опрос" : ""}.`;
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
    savedGroups: [],
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
    [withheld, setWithheld] = useState<{
      minimum: number;
      count: number;
    } | null>(null),
    [pending, setPending] = useState<Pending | null>(null),
    [seats, setSeats] = useState<Seats>({}),
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
    setWithheld(null);
    setPending(null);
    setSeats({});
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
    setWithheld(
      result.withheld ? { minimum: result.minimum, count: result.count } : null,
    );
    if (!after) {
      setSummary(await api(`forms/items/${selected.id}/summary`, workspace));
      await loadPending(selected);
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
  // Who has not answered yet: the first page replaces the list, "Показать ещё" appends the next one.
  async function loadPending(selected: Form, offset = 0) {
    const result = (await api(
      `forms/items/${selected.id}/pending?limit=50&offset=${offset}`,
      workspace,
    )) as Pending;
    setPending((old) =>
      offset && old
        ? { ...result, items: [...old.items, ...result.items] }
        : result,
    );
  }
  async function refreshSeats(id: string) {
    setSeats(await api(`forms/items/${id}/seats`, workspace));
  }
  async function open(
    id: string,
    preferred?: "manage" | "answer" | "responses",
  ) {
    const selected = (await api(`forms/items/${id}`, workspace)) as Form;
    clear();
    setForm(selected);
    setSeats(selected.seatsLeft || {});
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
      ...(action === "reopen"
        ? { closesAt: form.definition.standing ? null : utc(deadline) }
        : {}),
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
    try {
      await mutate(`forms/items/${form.id}/answer`, "PUT", {
        version: answer.version,
        formVersion: form.version,
        entityId: entity,
        action,
        answers: values,
      });
    } catch (e: any) {
      // Somebody may have taken the last seat: show the real numbers, keep what was typed and let run() show the reason.
      if (e.status === 409 && hasLimits)
        await refreshSeats(form.id).catch(() => undefined);
      throw e;
    }
    const selected = await api(`forms/items/${form.id}`, workspace);
    setForm(selected);
    // A withdrawal frees a seat and a submission takes one: show the numbers that came with the form, not those from before the action.
    setSeats(selected.seatsLeft || {});
    await loadAnswer(selected, entity);
    setNotice(
      action === "save"
        ? "Черновик сохранён. Ответственные его не видят."
        : action === "withdraw"
          ? "Ответ отозван."
          : "Ответ отправлен.",
    );
  }
  async function remind() {
    if (!form) return;
    const result = await mutate(`forms/items/${form.id}/remind`, "POST", {});
    // The same body would replay this result on the next press: a new reminder is a new request.
    replay.current = { key: "", id: "" };
    await loadPending(form);
    setNotice(
      `Напоминание отправлено: ${result.reminded}. Уже получили его сегодня: ${result.skipped}.`,
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
  const preset: Preset = definition?.audience?.preset ?? "roles";
  const hasLimits = !!form?.definition.questions.some(
    (q) => Object.keys(q.limits || {}).length,
  );
  // Seats left are read again while a limited form is being answered: others may take them meanwhile.
  useEffect(() => {
    if (mode !== "answer" || !form || !hasLimits) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible")
        void refreshSeats(form.id).catch(() => undefined);
    }, 30000);
    return () => window.clearInterval(timer);
  }, [mode, form?.id, hasLimits, workspace]);
  // A returned application stays correctable after the deadline or a manual close; a decided one needs open intake.
  const anonymousDone =
    !!form?.definition.anonymous && answer?.status === "submitted";
  // Seats left of a limited choice; a choice stays open to the answer that already holds one of its seats.
  // The held version comes from the server: a private correction typed over it holds nothing until it is sent.
  const held = (q: Question, c: string) =>
    holdsChoice(answer?.heldAnswers, q.id, c);
  const closed = (q: Question, c: string) =>
    choiceClosed(seats[q.id]?.[c], held(q, c));
  // A ticked checkbox stays clearable even when its choice is full; only adding it is refused.
  const locked = (q: Question, c: string) =>
    choiceLocked(seats[q.id]?.[c], held(q, c), holdsChoice(values, q.id, c));
  const seatNote = (q: Question, c: string) =>
    seats[q.id]?.[c] === undefined
      ? ""
      : held(q, c)
        ? " — ваше место"
        : seats[q.id][c] === 0
          ? " — мест нет"
          : ` — осталось ${seats[q.id][c]}`;
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
                  {item.standing ? " · Без срока" : ""}
                  {item.anonymous ? " · Анонимный" : ""}
                  {item.status === "open" && !item.accepting
                    ? " · Сейчас ответы не принимаются"
                    : ""}
                </small>
                {item.contexts?.map((c) => (
                  <small key={c.entityId}>
                    {c.entityId ? `${c.name}: ` : "Ваш ответ: "}
                    {statuses[c.status] || c.status}
                  </small>
                ))}
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
                              questions: definition.questions.map((q) => {
                                const choices = q.choices
                                  .map((v) => v.trim())
                                  .filter(Boolean);
                                return {
                                  ...q,
                                  choices,
                                  limits: definition.anonymous
                                    ? {}
                                    : limitsFor(choices, q.limits),
                                };
                              }),
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
                            {!definition.standing && (
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
                            )}
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
                                      limits: {},
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
                              {["single", "multiple"].includes(q.kind) &&
                                !definition.anonymous && (
                                  <div className="school-form-limits">
                                    {Array.from(
                                      new Set(
                                        q.choices
                                          .map((c) => c.trim())
                                          .filter(Boolean),
                                      ),
                                    ).map((c) => (
                                      <label key={c}>
                                        Мест: {c}
                                        <input
                                          type="number"
                                          min={1}
                                          max={5000}
                                          step={1}
                                          placeholder="без лимита"
                                          aria-label={`Мест на вариант «${c}» (пусто — без лимита)`}
                                          value={q.limits?.[c] ?? ""}
                                          onChange={(e) => {
                                            const next = { ...q.limits };
                                            if (e.target.value === "")
                                              delete next[c];
                                            else
                                              next[c] = Number(e.target.value);
                                            question(index, { limits: next });
                                          }}
                                        />
                                      </label>
                                    ))}
                                  </div>
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
                                <label>
                                  Кому
                                  <select
                                    value={preset}
                                    disabled={!!definition.standing}
                                    onChange={(e) => {
                                      const next = e.target.value as Preset;
                                      change({
                                        audience: {
                                          roles: [],
                                          classIds: [],
                                          preset: next,
                                          savedGroupId:
                                            next === "saved"
                                              ? (options.savedGroups[0]?.id ??
                                                null)
                                              : null,
                                        },
                                      });
                                    }}
                                  >
                                    {(Object.keys(presets) as Preset[]).map(
                                      (key) => (
                                        <option key={key} value={key}>
                                          {presets[key]}
                                        </option>
                                      ),
                                    )}
                                  </select>
                                </label>
                                {preset === "saved" && (
                                  <label>
                                    Сохранённая группа
                                    <select
                                      value={
                                        definition.audience.savedGroupId ?? ""
                                      }
                                      onChange={(e) =>
                                        change({
                                          audience: {
                                            ...definition.audience!,
                                            savedGroupId:
                                              e.target.value || null,
                                          },
                                        })
                                      }
                                    >
                                      <option value="">Выберите группу</option>
                                      {options.savedGroups.map((g) => (
                                        <option key={g.id} value={g.id}>
                                          {g.name}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                )}
                                {preset === "saved" &&
                                  !options.savedGroups.length && (
                                    <p>
                                      Своих сохранённых групп нет: составьте их
                                      в разделе сообщений. Группу публикует тот
                                      администратор, который её составил.
                                    </p>
                                  )}
                                {preset !== "roles" && (
                                  <p>
                                    Состав определяется по текущим данным школы.
                                    Проверьте его кнопкой «Проверить аудиторию»
                                    перед публикацией.
                                  </p>
                                )}
                                {preset === "roles" && (
                                  <>
                                    <p>
                                      Классы ограничивают учеников и родителей.
                                      Выбранные сотрудники получают форму
                                      независимо от класса.
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
                                                          ...definition
                                                            .audience!.roles,
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
                                  </>
                                )}
                                <label className="school-form-check">
                                  <input
                                    type="checkbox"
                                    checked={!!definition.standing}
                                    onChange={(e) =>
                                      change(
                                        e.target.checked
                                          ? {
                                              standing: true,
                                              anonymous: false,
                                              closesAt: null,
                                              audience:
                                                preset === "roles"
                                                  ? definition.audience
                                                  : {
                                                      roles: [],
                                                      classIds: [],
                                                      preset: "roles",
                                                      savedGroupId: null,
                                                    },
                                            }
                                          : { standing: false },
                                      )
                                    }
                                  />
                                  Постоянная форма: приём без срока, новые
                                  адресаты видят её сами
                                </label>
                                {definition.kind === "survey" && (
                                  <label className="school-form-check">
                                    <input
                                      type="checkbox"
                                      checked={!!definition.anonymous}
                                      onChange={(e) =>
                                        change(
                                          e.target.checked
                                            ? {
                                                anonymous: true,
                                                standing: false,
                                                questions:
                                                  definition.questions.map(
                                                    (q) => ({
                                                      ...q,
                                                      limits: {},
                                                    }),
                                                  ),
                                              }
                                            : { anonymous: false },
                                        )
                                      }
                                    />
                                    Анонимный опрос
                                  </label>
                                )}
                                {definition.anonymous && (
                                  <p className="school-forms-notice">
                                    Ответы хранятся без имени и результаты
                                    открываются, когда ответят {ANONYMOUS_MIN}{" "}
                                    человек, адресатов должно быть не меньше. Лимита
                                    мест, черновика и отзыва нет. Свободный
                                    текст может выдать автора: попросите не
                                    называть себя.
                                  </p>
                                )}
                              </fieldset>
                            )}
                          {form.permission !== "admin" && (
                            <p>
                              Аудиторию, постоянный приём и анонимность задаёт
                              администратор: {audienceText(definition)} Перед
                              публикацией проверьте число адресатов.
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
                          Начало: {date(form.definition.opensAt)}.{" "}
                          {form.definition.standing
                            ? "Постоянная форма: приём без срока, пока её не закроют."
                            : `Окончание: ${date(form.definition.closesAt)}.`}
                        </p>
                        {form.definition.anonymous && (
                          <p>
                            Анонимный опрос: ответы хранятся без имён,
                            результаты откроются, когда ответят {ANONYMOUS_MIN}{" "}
                            человек.
                          </p>
                        )}
                        <p>
                          Вопросы после публикации не меняются. Ответы привязаны
                          к опубликованной форме.
                        </p>
                        <ol>
                          {form.definition.questions.map((q) => (
                            <li key={q.id}>
                              {q.title} · {questionKinds[q.kind]}
                              {q.required ? " · обязательный" : ""}
                              {Object.entries(q.limits || {}).map(
                                ([choice, total]) =>
                                  ` · «${choice}»: мест ${total}, осталось ${seats[q.id]?.[choice] ?? total}`,
                              )}
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
                              {!!preview.unavailable && (
                                <p>
                                  Из сохранённой группы сейчас недоступны:{" "}
                                  <strong>{preview.unavailable}</strong>. Они не
                                  получат форму.
                                </p>
                              )}
                              <p>
                                {form.definition.anonymous
                                  ? "Опрос анонимный: ответы не связываются с именами, а кто ответил, не показывается."
                                  : "Родитель отвечает отдельно за каждого своего ребёнка. Ответы разных родителей сохраняются отдельно."}
                              </p>
                              {form.definition.standing && (
                                <p>
                                  Постоянная форма: аккаунты, появившиеся после
                                  публикации, увидят её сами, но уведомление
                                  получат только нынешние адресаты.
                                </p>
                              )}
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
                          {!form.definition.standing && (
                            <label>
                              Новый срок ответа
                              <input
                                type="datetime-local"
                                value={deadline}
                                onChange={(e) => setDeadline(e.target.value)}
                              />
                            </label>
                          )}
                          <button
                            onClick={() => void run(() => transition("reopen"))}
                          >
                            {form.definition.standing
                              ? "Открыть приём без срока"
                              : "Открыть приём с этим сроком"}
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
                    {form.definition.anonymous ? (
                      "Ответ анонимный: имя с ним не связывается. После отправки изменить его нельзя."
                    ) : (
                      <>
                        Именной ответ от аккаунта <strong>{user.name}</strong>.
                      </>
                    )}{" "}
                    {form.definition.standing
                      ? "Приём без срока."
                      : `Ответить до: ${date(form.definition.closesAt)}.`}
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
                        Статус:{" "}
                        <strong>
                          {anonymousDone
                            ? "Вы ответили"
                            : statuses[
                                form.definition.anonymous
                                  ? "none"
                                  : answer.status
                              ]}
                        </strong>
                        .{" "}
                        {!form.definition.anonymous &&
                          answer.updated &&
                          `Изменено ${date(answer.updated)}.`}
                      </p>
                      {anonymousDone && (
                        <p className="school-forms-notice">
                          Ответ принят и не связан с вашим именем. Его нельзя
                          изменить, отозвать или прочитать повторно.
                        </p>
                      )}
                      {answer.comment && (
                        <p className="school-forms-notice">
                          Комментарий ответственного: {answer.comment}
                        </p>
                      )}
                      {!anonymousDone && (
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
                                  onChange={(e) =>
                                    setValue(q.id, e.target.value)
                                  }
                                />
                              ) : q.kind === "single" ? (
                                <select
                                  id={`answer-${q.id}`}
                                  value={String(values[q.id] ?? "")}
                                  onChange={(e) =>
                                    setValue(q.id, e.target.value)
                                  }
                                >
                                  <option value="">Выберите вариант</option>
                                  {q.choices.map((c) => (
                                    <option
                                      key={c}
                                      value={c}
                                      disabled={closed(q, c)}
                                    >
                                      {c}
                                      {seatNote(q, c)}
                                    </option>
                                  ))}
                                </select>
                              ) : q.kind === "multiple" ? (
                                <div role="group" aria-label={q.title}>
                                  {q.choices.map((c) => (
                                    <label
                                      key={c}
                                      className="school-form-check"
                                    >
                                      <input
                                        type="checkbox"
                                        disabled={locked(q, c)}
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
                                      {seatNote(q, c)}
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
                            {!form.definition.anonymous && (
                              <button
                                onClick={() =>
                                  void run(() => sendAnswer("save"))
                                }
                              >
                                Сохранить черновик
                              </button>
                            )}
                            <button
                              className="primary"
                              onClick={() =>
                                void run(() => sendAnswer("submit"))
                              }
                            >
                              {decided
                                ? "Отправить новую заявку"
                                : "Отправить ответ"}
                            </button>
                          </div>
                          <p>
                            {form.definition.anonymous
                              ? "* Обязательный вопрос. Отправленный ответ изменить нельзя."
                              : "* Обязательный вопрос. Черновик виден только вам."}
                          </p>
                        </fieldset>
                      )}
                      {answer.status === "submitted" &&
                        form.accepting &&
                        !form.definition.anonymous && (
                          <button
                            disabled={busy}
                            onClick={() => {
                              if (
                                window.confirm("Отозвать ответ для изменения?")
                              )
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
                    {form.definition.standing
                      ? "Постоянная форма: адресаты определяются в момент ответа."
                      : `Адресатов при публикации: ${form.audienceCount ?? 0}.`}{" "}
                    {form.definition.anonymous
                      ? "Опрос анонимный: ответы без имён, порядок ничего не говорит о времени и авторе."
                      : "Ответы родителей за разных детей и ответы разных родителей считаются отдельно."}
                  </p>
                  <p>
                    {form.definition.anonymous
                      ? "Черновиков и отзыва в анонимном опросе нет."
                      : "Черновики не видны ответственным. Отозванные ответы исключены из сводки и CSV."}
                  </p>
                  {withheld && (
                    <p className="school-forms-notice">
                      Результаты откроются, когда ответят {withheld.minimum}{" "}
                      человек: сейчас {withheld.count}. Так ответ нельзя связать
                      с человеком.
                    </p>
                  )}
                  <div className="school-forms-toolbar">
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (mayLeave()) void run(() => loadResponses(form));
                      }}
                    >
                      Обновить результаты
                    </button>
                    {!withheld && (
                      <button
                        disabled={busy}
                        onClick={() => void run(download)}
                      >
                        Скачать CSV
                      </button>
                    )}
                  </div>
                  {pending?.applicable && (
                    <details className="school-form-pending">
                      <summary>
                        Кто не ответил: {pending.pendingCount} из{" "}
                        {pending.audience}
                      </summary>
                      {pending.anonymous ? (
                        <p>
                          Ответили {pending.answered} из {pending.audience}.
                          Имена не показываются.
                        </p>
                      ) : (
                        <>
                          {!pending.items.length && (
                            <p>Все адресаты ответили.</p>
                          )}
                          <ul>
                            {pending.items.map((p, index) => (
                              <li key={index}>
                                {p.name}
                                {p.entityName ? ` · ${p.entityName}` : ""}
                                {p.className ? ` · ${p.className}` : ""} ·{" "}
                                {roleNames[p.role]}
                                {p.remindedToday ? " · напомнили сегодня" : ""}
                              </li>
                            ))}
                          </ul>
                          {pending.nextOffset !== null && (
                            <button
                              disabled={busy}
                              onClick={() =>
                                void run(() =>
                                  loadPending(form, pending.nextOffset!),
                                )
                              }
                            >
                              Показать ещё
                            </button>
                          )}
                        </>
                      )}
                      {pending.canRemind && (
                        <button
                          disabled={busy || !pending.pendingCount}
                          onClick={() => void run(remind)}
                        >
                          Напомнить всем, кто не ответил
                        </button>
                      )}
                      <p>
                        Каждому аккаунту — не больше одного напоминания в день.
                        Черновик ответом не считается.
                      </p>
                    </details>
                  )}
                  {summary && !summary.withheld && (
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
                  {!responses.length && !withheld && (
                    <p>Отправленных ответов пока нет.</p>
                  )}
                  {form.definition.anonymous &&
                    responses.map((r, index) => (
                      <article className="school-form-response" key={r.id}>
                        <h4>Анонимный ответ {index + 1}</h4>
                        <dl>
                          {form.definition.questions.map((q) => (
                            <div key={q.id}>
                              <dt>{q.title}</dt>
                              <dd>{display(r.answers[q.id])}</dd>
                            </div>
                          ))}
                        </dl>
                      </article>
                    ))}
                  {!form.definition.anonymous &&
                    responses.map((r) => (
                      <article className="school-form-response" key={r.id}>
                        <h4>
                          {r.name}
                          {r.entityName ? ` · ${r.entityName}` : ""}
                          {r.className ? ` · ${r.className}` : ""}
                        </h4>
                        <p>
                          {r.role ? roleNames[r.role] : ""} ·{" "}
                          {statuses[r.status]} · {date(r.updated)}
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
                            <p>
                              Свою заявку рассматривает другой ответственный.
                            </p>
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
                              setReviewHistory((v) => ({
                                ...v,
                                [r.id]: result,
                              }));
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

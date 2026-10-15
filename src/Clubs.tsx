import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import { displayTime } from "./meetings";
import "./Clubs.css";

type Pupil = { id: string; name: string; classId: string };
type Data = {
  title: string;
  description: string;
  location: string;
  startsOn: string;
  endsOn: string;
  opensOn: string;
  closesOn: string;
  capacity: number;
  queueMode: string;
  classIds: string[];
};
type Club = {
  id: string;
  version: number;
  status: string;
  data: Data;
  canManage: boolean;
  enrolled: number;
  waiting: number;
  accepting: boolean;
  staff: { id: string; name: string }[];
  students?: Pupil[];
};
type Member = {
  id: string;
  version: number;
  studentId: string;
  status: string;
  position: number | null;
  eligible: boolean;
  data: { studentName: string; className: string; decision: string };
};
type Mark = {
  studentId: string;
  studentName: string;
  className: string;
  status: string;
  note: string;
};
type Session = {
  id: string;
  version: number;
  status: string;
  data: {
    date: string;
    start: string;
    end: string;
    title: string;
    location: string;
  };
  marks: Mark[];
};
type Options = {
  admin: boolean;
  canManage: boolean;
  today: string;
  classes: { id: string; name: string }[];
  students: Pupil[];
  staff: { id: string; name: string; role: string }[];
};
type History = {
  items: {
    seq: number;
    created: string;
    actor: string;
    action: string;
    reason: string;
    data: any;
  }[];
  nextBefore: number | null;
};
const labels: Record<string, string> = {
  draft: "Черновик",
  open: "Запись открыта",
  closed: "Запись закрыта",
  archived: "В архиве",
  enrolled: "Зачислен",
  waiting: "В очереди",
  withdrawn: "Запись отменена",
  rejected: "Отклонена",
  completed: "Завершено",
  ineligible: "Нужна новая запись",
  planned: "Запланировано",
  active: "Отметки открыты",
  cancelled: "Отменено",
  unknown: "Не отмечен",
  present: "Присутствовал",
  absent: "Отсутствовал",
  excused: "Уважительная причина",
  late: "Опоздал",
  automatic: "Автоматически по порядку",
  manual: "С подтверждением сотрудника",
  saved: "Карточка сохранена",
  access: "Ответственные изменены",
  joined: "Подана запись",
  publish: "Кружок опубликован",
  close: "Приём закрыт",
  archive: "Архивировано",
  advance: "Обработана очередь",
  attendance: "Посещение записано",
  "session-saved": "Занятие сохранено",
  "session-active": "Отметки открыты",
  "session-completed": "Занятие завершено",
  "session-cancelled": "Занятие отменено",
};
function HistoryValue({
  value,
  classes,
}: {
  value: any;
  classes: Options["classes"];
}) {
  const names: Record<string, string> = {
    before: "До изменения",
    after: "После изменения",
    title: "Название",
    description: "Описание",
    location: "Место",
    startsOn: "Начало кружка",
    endsOn: "Окончание кружка",
    opensOn: "Начало записи",
    closesOn: "Конец записи",
    capacity: "Мест",
    queueMode: "Очередь",
    classIds: "Классы",
    studentName: "Ученик",
    status: "Состояние",
    name: "Сотрудник",
    date: "Дата",
    start: "Начало",
    end: "Окончание",
    marks: "Отметки",
    note: "Пояснение",
    promoted: "Зачислено из очереди",
  };
  if (value === null || value === undefined) return <span>Нет</span>;
  if (typeof value !== "object")
    return <span>{labels[String(value)] || String(value)}</span>;
  if (Array.isArray(value))
    return (
      <ul>
        {value.map((v, i) => (
          <li key={i}>
            <HistoryValue value={v} classes={classes} />
          </li>
        ))}
      </ul>
    );
  return (
    <dl>
      {Object.entries(value)
        .filter(([k]) => names[k])
        .map(([k, v]) => (
          <div key={k}>
            <dt>{names[k]}</dt>
            <dd>
              {k === "classIds" ? (
                (v as string[])
                  .map(
                    (id) =>
                      classes.find((c) => c.id === id)?.name || "Прежний класс",
                  )
                  .join(", ")
              ) : (
                <HistoryValue value={v} classes={classes} />
              )}
            </dd>
          </div>
        ))}
    </dl>
  );
}

export default function Clubs({
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
    [notice, setNotice] = useState("");
  const [mode, setMode] = useState("catalog"),
    [q, setQ] = useState(""),
    [query, setQuery] = useState("");
  const [items, setItems] = useState<Club[]>([]),
    [next, setNext] = useState<number | null>(null),
    [selected, setSelected] = useState<Club | null>(null);
  const [draft, setDraft] = useState<{
      id: string;
      version: number;
      data: Data;
    } | null>(null),
    [dirty, setDirty] = useState(false),
    [reason, setReason] = useState("");
  const [members, setMembers] = useState<Member[]>([]),
    [memberNext, setMemberNext] = useState<number | null>(null),
    [memberFilter, setMemberFilter] = useState(""),
    [student, setStudent] = useState("");
  const [sessions, setSessions] = useState<Session[]>([]),
    [sessionNext, setSessionNext] = useState<number | null>(null),
    [session, setSession] = useState<Session | null>(null),
    [sessionDraft, setSessionDraft] = useState<Session | null>(null),
    [marks, setMarks] = useState<Mark[]>([]);
  const [access, setAccess] = useState<string[] | null>(null),
    [history, setHistory] = useState<History | null>(null);
  const dialog = useRef<HTMLDialogElement>(null),
    replay = useRef({ key: "", id: "" }),
    generation = useRef(0);
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
  const leave = () =>
    !dirty || window.confirm("Есть несохранённые изменения. Отбросить их?");
  function clear() {
    setSelected(null);
    setDraft(null);
    setSessionDraft(null);
    setSession(null);
    setMarks([]);
    setMembers([]);
    setSessions([]);
    setAccess(null);
    setHistory(null);
    setDirty(false);
    setReason("");
    setStudent("");
    setMemberFilter("");
  }
  function change(fn: () => void) {
    if (!busy && leave()) {
      clear();
      setError("");
      setNotice("");
      fn();
    }
  }
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      if ([401, 403, 404].includes(e.status)) {
        clear();
        setOptions(null);
        setItems([]);
      }
    } finally {
      setBusy(false);
    }
  }
  async function send(path: string, method: string, body: object) {
    const key = JSON.stringify([workspace, path, method, body]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const result = await api("clubs/" + path, workspace, method, {
      ...body,
      requestId: replay.current.id,
    });
    replay.current = { key: "", id: "" };
    return result;
  }
  const base = (id = selected?.id) => "items/" + id;
  const listPath = (before?: number) =>
    `clubs/items?mode=${mode}&q=${encodeURIComponent(q)}${before ? "&before=" + before : ""}`;
  async function list() {
    const rows = await api(listPath(), workspace);
    setItems(rows.items);
    setNext(rows.nextBefore);
  }
  async function detail(id: string, filter = memberFilter) {
    const [c, m, x] = await Promise.all([
      api("clubs/" + base(id), workspace),
      api("clubs/" + base(id) + "/members?status=" + filter, workspace),
      api("clubs/" + base(id) + "/sessions", workspace),
    ]);
    setSelected(c);
    setMembers(m.items);
    setMemberNext(m.nextAfter);
    setSessions(x.items);
    setSessionNext(x.nextAfter);
    return c as Club;
  }
  useEffect(() => {
    const n = ++generation.current;
    void run(async () => {
      const o = await api("clubs/options", workspace),
        rows = await api(listPath(), workspace);
      if (generation.current !== n) return;
      setOptions(o);
      setItems(rows.items);
      setNext(rows.nextBefore);
    });
    return () => {
      generation.current++;
    };
  }, [workspace, mode, q]);
  function editData(field: keyof Data, value: any) {
    if (draft) {
      setDraft({ ...draft, data: { ...draft.data, [field]: value } });
      setDirty(true);
    }
  }
  function newClub() {
    change(() => {
      const today = options!.today;
      setDraft({
        id: "",
        version: 0,
        data: {
          title: "",
          description: "",
          location: "",
          startsOn: today,
          endsOn: today.slice(0, 4) + "-12-31",
          opensOn: today,
          closesOn: today.slice(0, 4) + "-12-31",
          capacity: 20,
          queueMode: "automatic",
          classIds: [],
        },
      });
    });
  }
  async function clubAction(action: string) {
    if (!selected || !leave()) return;
    const why = window.prompt(
      "Укажите причину. Закрытие записи отменит очередь; архив завершит участие и отменит будущие занятия.",
    );
    if (!why) return;
    await run(async () => {
      await send(base() + "/action", "POST", {
        version: selected.version,
        action,
        reason: why,
      });
      setDirty(false);
      setAccess(null);
      setSessionDraft(null);
      setSession(null);
      setMarks([]);
      setStudent("");
      setReason("");
      await detail(selected.id);
      await list();
      setNotice("Изменение сохранено.");
    });
  }
  async function memberAction(m: Member, action: string) {
    if (!selected || !leave()) return;
    const why = window.prompt("Причина изменения записи");
    if (!why) return;
    await run(async () => {
      await send(base() + "/members/" + m.id + "/action", "POST", {
        version: m.version,
        clubVersion: selected.version,
        action,
        reason: why,
      });
      setDirty(false);
      setStudent("");
      setReason("");
      await detail(selected.id);
      await list();
      setNotice("Запись обновлена.");
    });
  }
  async function sessionAction(action: string) {
    if (!selected || !session || !leave()) return;
    const why = window.prompt("Пояснение к действию с занятием");
    if (!why) return;
    await run(async () => {
      const x = await send(
        base() + "/sessions/" + session.id + "/action",
        "POST",
        { version: session.version, action, reason: why },
      );
      setSession(x);
      setMarks(x.marks);
      setDirty(false);
      setReason("");
      await detail(selected.id);
      setNotice("Занятие обновлено.");
    });
  }
  function editSession(x?: Session) {
    if (!selected || !leave()) return;
    setSession(null);
    setMarks([]);
    setSessionDraft(
      x || {
        id: "",
        version: 0,
        status: "planned",
        data: {
          date: options!.today,
          start: "16:00",
          end: "17:00",
          title: "",
          location: selected.data.location,
        },
        marks: [],
      },
    );
    setAccess(null);
    setDraft(null);
    setDirty(false);
    setReason("");
  }
  const pupils = selected?.canManage
    ? selected.students || []
    : options?.students.filter((p) =>
        selected?.data.classIds.includes(p.classId),
      ) || [];
  return (
    <dialog
      className="clubs-dialog"
      ref={dialog}
      aria-labelledby="clubs-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && leave()) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="clubs-title">Кружки</h2>
          <p>Выбирайте занятия, следите за записью и посещением</p>
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
      {error && (
        <p className="clubs-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="clubs-notice" role="status">
          {notice}
        </p>
      )}
      <fieldset disabled={busy}>
        <div className="clubs-toolbar">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => change(() => setWorkspace(e.target.value))}
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
              if (leave())
                void run(async () => {
                  clear();
                  setOptions(await api("clubs/options", workspace));
                  await list();
                });
            }}
          >
            Обновить
          </button>
        </div>
        <nav aria-label="Разделы кружков">
          {[
            ["catalog", "Каталог"],
            ["mine", "Мои записи"],
            ...(options?.canManage ? [["manage", "Управление"]] : []),
          ].map(([id, name]) => (
            <button
              key={id}
              aria-pressed={mode === id}
              onClick={() => change(() => setMode(id))}
            >
              {name}
            </button>
          ))}
          {options?.admin && <button onClick={newClub}>Создать кружок</button>}
        </nav>
        <form
          className="clubs-search"
          onSubmit={(e) => {
            e.preventDefault();
            change(() => setQ(query));
          }}
        >
          <label>
            Поиск кружка
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={200}
            />
          </label>
          <button>Найти</button>
        </form>
        <section className="clubs-list" aria-label="Список кружков">
          {items.length === 0 && <p>По этому фильтру кружков нет.</p>}
          {items.map((c) => (
            <button
              key={c.id}
              className={selected?.id === c.id ? "chosen" : ""}
              onClick={() =>
                change(() => void run(() => detail(c.id, "").then(() => {})))
              }
            >
              <strong>{c.data.title}</strong>
              <span>
                {labels[c.status]} · {c.enrolled} из {c.data.capacity} мест ·
                очередь: {c.waiting}
              </span>
              <span>{c.data.location || "Место уточняется"}</span>
            </button>
          ))}
        </section>
        {next && (
          <button
            onClick={() =>
              void run(async () => {
                const rows = await api(listPath(next), workspace);
                setItems([...items, ...rows.items]);
                setNext(rows.nextBefore);
              })
            }
          >
            Ещё кружки
          </button>
        )}
        {draft && options && (
          <form
            className="clubs-editor"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                const c = await send(
                  "items" + (draft.id ? "/" + draft.id : ""),
                  draft.id ? "PUT" : "POST",
                  { version: draft.version, data: draft.data, reason },
                );
                setDirty(false);
                setDraft(null);
                setReason("");
                await detail(c.id, "");
                await list();
                setNotice(
                  "Карточка сохранена. Публикация выполняется отдельно.",
                );
              });
            }}
          >
            <h3>{draft.id ? "Редактировать кружок" : "Новый кружок"}</h3>
            <label>
              Название
              <input
                required
                minLength={2}
                maxLength={160}
                value={draft.data.title}
                onChange={(e) => editData("title", e.target.value)}
              />
            </label>
            <label>
              Описание
              <textarea
                maxLength={4000}
                value={draft.data.description}
                onChange={(e) => editData("description", e.target.value)}
              />
            </label>
            <label>
              Место
              <input
                maxLength={200}
                value={draft.data.location}
                onChange={(e) => editData("location", e.target.value)}
              />
            </label>
            <div className="clubs-grid">
              {(["startsOn", "endsOn", "opensOn", "closesOn"] as const).map(
                (k, i) => (
                  <label key={k}>
                    {
                      [
                        "Начало занятий",
                        "Окончание занятий",
                        "Начало записи",
                        "Конец записи",
                      ][i]
                    }
                    <input
                      type="date"
                      required
                      value={draft.data[k]}
                      onChange={(e) => editData(k, e.target.value)}
                    />
                  </label>
                ),
              )}
              <label>
                Количество мест
                <input
                  type="number"
                  min={1}
                  max={300}
                  required
                  value={draft.data.capacity}
                  onChange={(e) => editData("capacity", Number(e.target.value))}
                />
              </label>
              <label>
                Перевод из очереди
                <select
                  value={draft.data.queueMode}
                  onChange={(e) => editData("queueMode", e.target.value)}
                >
                  <option value="automatic">Автоматически по порядку</option>
                  <option value="manual">С подтверждением сотрудника</option>
                </select>
              </label>
            </div>
            <fieldset disabled={!options.admin}>
              <legend>Классы участников</legend>
              <div className="clubs-choices">
                {options.classes.map((c) => (
                  <label key={c.id}>
                    <input
                      type="checkbox"
                      checked={draft.data.classIds.includes(c.id)}
                      onChange={(e) =>
                        editData(
                          "classIds",
                          e.target.checked
                            ? [...draft.data.classIds, c.id]
                            : draft.data.classIds.filter((id) => id !== c.id),
                        )
                      }
                    />
                    {c.name}
                  </label>
                ))}
              </div>
            </fieldset>
            <label>
              Причина изменения
              <input
                required={!!draft.id}
                minLength={draft.id ? 3 : undefined}
                maxLength={1000}
                value={reason}
                onChange={(e) => {
                  setReason(e.target.value);
                  setDirty(true);
                }}
              />
            </label>
            <div className="clubs-actions">
              <button>Сохранить карточку</button>
              <button
                type="button"
                onClick={() => {
                  if (leave()) {
                    setDraft(null);
                    setDirty(false);
                    setReason("");
                  }
                }}
              >
                Отмена
              </button>
            </div>
          </form>
        )}
        {selected && !draft && (
          <section className="clubs-detail">
            <h3>{selected.data.title}</h3>
            <p className="clubs-prose">{selected.data.description}</p>
            <p>
              {labels[selected.status]} · {selected.data.startsOn} —{" "}
              {selected.data.endsOn}
            </p>
            <p>
              Запись: {selected.data.opensOn} — {selected.data.closesOn}.{" "}
              {selected.enrolled} из {selected.data.capacity} мест; в очереди{" "}
              {selected.waiting}.
            </p>
            <p>
              Очередь: {labels[selected.data.queueMode]}.{" "}
              {selected.staff.length > 0
                ? "Ответственные: " +
                  selected.staff.map((a) => a.name).join(", ")
                : "Ведёт администрация."}
            </p>
            {selected.canManage && (
              <div className="clubs-actions">
                {selected.status !== "archived" && (
                  <button
                    onClick={() => {
                      if (leave()) {
                        setDraft({
                          id: selected.id,
                          version: selected.version,
                          data: structuredClone(selected.data),
                        });
                        setDirty(false);
                        setReason("");
                        setSessionDraft(null);
                        setAccess(null);
                      }
                    }}
                  >
                    Редактировать карточку
                  </button>
                )}
                {["draft", "closed"].includes(selected.status) && (
                  <button onClick={() => void clubAction("publish")}>
                    Открыть запись
                  </button>
                )}
                {selected.status === "open" && (
                  <>
                    <button onClick={() => void clubAction("close")}>
                      Закрыть запись
                    </button>
                    <button onClick={() => void clubAction("advance")}>
                      Рассмотреть первого в очереди
                    </button>
                  </>
                )}
                {selected.status !== "archived" && (
                  <button onClick={() => void clubAction("archive")}>
                    Завершить и архивировать
                  </button>
                )}
                {options?.admin && (
                  <button
                    onClick={() => {
                      if (leave()) {
                        setAccess(selected.staff.map((a) => a.id));
                        setSessionDraft(null);
                        setSession(null);
                        setDirty(false);
                        setReason("");
                      }
                    }}
                  >
                    Ответственные
                  </button>
                )}
                <button
                  onClick={() =>
                    void run(async () =>
                      setHistory(
                        await api("clubs/" + base() + "/history", workspace),
                      ),
                    )
                  }
                >
                  История кружка
                </button>
              </div>
            )}
            {access && options && (
              <form
                className="clubs-editor"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    await send(base() + "/access", "PUT", {
                      version: selected.version,
                      accountIds: access,
                      reason,
                    });
                    setAccess(null);
                    setDirty(false);
                    setReason("");
                    await detail(selected.id);
                    setNotice("Ответственные сохранены.");
                  });
                }}
              >
                <h4>Ответственные за этот кружок</h4>
                <p>Доступ относится только к выбранному кружку.</p>
                <div className="clubs-choices">
                  {options.staff.map((a) => (
                    <label key={a.id}>
                      <input
                        type="checkbox"
                        checked={access.includes(a.id)}
                        onChange={(e) => {
                          setAccess(
                            e.target.checked
                              ? [...access, a.id]
                              : access.filter((id) => id !== a.id),
                          );
                          setDirty(true);
                        }}
                      />
                      {a.name}
                    </label>
                  ))}
                </div>
                <label>
                  Основание назначения
                  <input
                    required
                    minLength={3}
                    maxLength={1000}
                    value={reason}
                    onChange={(e) => {
                      setReason(e.target.value);
                      setDirty(true);
                    }}
                  />
                </label>
                <button>Сохранить ответственных</button>
                <button
                  type="button"
                  onClick={() => {
                    if (leave()) {
                      setAccess(null);
                      setDirty(false);
                    }
                  }}
                >
                  Отмена
                </button>
              </form>
            )}
            {!sessionDraft && !session && !access && (
              <>
                {selected.accepting && pupils.length > 0 && (
                  <form
                    className="clubs-join"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void run(async () => {
                        const m = await send(base() + "/members", "POST", {
                          clubVersion: selected.version,
                          studentId: student,
                          reason: selected.canManage ? reason : "",
                        });
                        setDirty(false);
                        setStudent("");
                        setReason("");
                        await detail(selected.id);
                        await list();
                        setNotice(
                          m.status === "enrolled"
                            ? "Ребёнок зачислен."
                            : "Запись добавлена в очередь. Место ещё не подтверждено.",
                        );
                      });
                    }}
                  >
                    <h4>Записать ребёнка</h4>
                    <label>
                      Ученик
                      <select
                        required
                        value={student}
                        onChange={(e) => {
                          setStudent(e.target.value);
                          setDirty(true);
                        }}
                      >
                        <option value="">Выберите ученика</option>
                        {pupils.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name} ·{" "}
                            {
                              options?.classes.find((c) => c.id === p.classId)
                                ?.name
                            }
                          </option>
                        ))}
                      </select>
                    </label>
                    {selected.canManage && (
                      <label>
                        Основание записи
                        <input
                          required
                          minLength={3}
                          maxLength={1000}
                          value={reason}
                          onChange={(e) => {
                            setReason(e.target.value);
                            setDirty(true);
                          }}
                        />
                      </label>
                    )}
                    <p>
                      Если свободных мест нет или есть очередь, ребёнок встанет
                      в очередь.
                    </p>
                    <button>Записаться</button>
                  </form>
                )}
                <h4>
                  {selected.canManage
                    ? "Участники и очередь"
                    : "Записи моих детей"}
                </h4>
                <div className="clubs-toolbar">
                  <label>
                    Состояние записи
                    <select
                      value={memberFilter}
                      onChange={(e) => {
                        if (leave()) {
                          setDirty(false);
                          setMemberFilter(e.target.value);
                          void run(() =>
                            detail(selected.id, e.target.value).then(() => {}),
                          );
                        }
                      }}
                    >
                      <option value="">Все</option>
                      {[
                        "enrolled",
                        "waiting",
                        "withdrawn",
                        "rejected",
                        "completed",
                        "ineligible",
                      ].map((k) => (
                        <option key={k} value={k}>
                          {labels[k]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <a
                    href={`/api/clubs/${base()}/export?workspace=${encodeURIComponent(workspace)}`}
                  >
                    Скачать все записи CSV
                  </a>
                </div>
                <div className="clubs-members">
                  {members.length === 0 && <p>Записей пока нет.</p>}
                  {members.map((m) => (
                    <article key={m.id}>
                      <div>
                        <strong>{m.data.studentName}</strong>
                        <p>
                          {m.data.className} · {labels[m.status]}
                          {m.position ? " · место в очереди " + m.position : ""}
                        </p>
                        {m.data.decision && (
                          <p className="clubs-prose">{m.data.decision}</p>
                        )}
                        {!m.eligible && (
                          <p>Текущие данные ученика требуют проверки.</p>
                        )}
                      </div>
                      <div className="clubs-actions">
                        {["enrolled", "waiting"].includes(m.status) &&
                          selected.status !== "archived" && (
                            <button
                              onClick={() => void memberAction(m, "withdraw")}
                            >
                              Отменить запись
                            </button>
                          )}
                        {selected.canManage &&
                          ["enrolled", "waiting"].includes(m.status) && (
                            <button
                              onClick={() => void memberAction(m, "reject")}
                            >
                              Отклонить с причиной
                            </button>
                          )}
                        {selected.canManage && m.status === "waiting" && (
                          <button
                            onClick={() => void memberAction(m, "promote")}
                          >
                            Зачислить по очереди
                          </button>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
                {memberNext && (
                  <button
                    onClick={() =>
                      void run(async () => {
                        const m = await api(
                          "clubs/" +
                            base() +
                            "/members?status=" +
                            memberFilter +
                            "&after=" +
                            memberNext,
                          workspace,
                        );
                        setMembers([...members, ...m.items]);
                        setMemberNext(m.nextAfter);
                      })
                    }
                  >
                    Ещё записи
                  </button>
                )}
              </>
            )}
            <h4>Занятия и посещения</h4>
            <p>
              Время указано по часовому поясу школы. Здесь учитываются занятия
              кружка.
            </p>
            <div className="clubs-actions">
              {selected.canManage && selected.status !== "archived" && (
                <button onClick={() => editSession()}>Добавить занятие</button>
              )}
              <a
                href={`/api/clubs/${base()}/export?kind=attendance&workspace=${encodeURIComponent(workspace)}`}
              >
                Посещения CSV
              </a>
            </div>
            <div className="clubs-sessions">
              {sessions.map((x) => (
                <button
                  key={x.id}
                  onClick={() => {
                    if (leave())
                      void run(async () => {
                        const v = await api(
                          "clubs/" + base() + "/sessions/" + x.id,
                          workspace,
                        );
                        setSession(v);
                        setMarks(v.marks);
                        setSessionDraft(null);
                        setAccess(null);
                        setDirty(false);
                        setReason("");
                      });
                  }}
                >
                  <strong>
                    {x.data.date} · {x.data.start}–{x.data.end}
                  </strong>
                  <span>
                    {x.data.title} · {labels[x.status]}
                  </span>
                </button>
              ))}
            </div>
            {sessions.length === 0 && <p>Занятия ещё не добавлены.</p>}
            {sessionNext && (
              <button
                onClick={() =>
                  void run(async () => {
                    const x = await api(
                      "clubs/" + base() + "/sessions?after=" + sessionNext,
                      workspace,
                    );
                    setSessions([...sessions, ...x.items]);
                    setSessionNext(x.nextAfter);
                  })
                }
              >
                Ещё занятия
              </button>
            )}
            {sessionDraft && (
              <form
                className="clubs-editor"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    await send(
                      base() +
                        "/sessions" +
                        (sessionDraft.id ? "/" + sessionDraft.id : ""),
                      sessionDraft.id ? "PUT" : "POST",
                      {
                        version: sessionDraft.version,
                        clubVersion: selected.version,
                        data: sessionDraft.data,
                        reason,
                      },
                    );
                    setDirty(false);
                    setSessionDraft(null);
                    setReason("");
                    await detail(selected.id);
                    setNotice("Занятие сохранено.");
                  });
                }}
              >
                <h4>
                  {sessionDraft.id ? "Редактировать занятие" : "Новое занятие"}
                </h4>
                {(["title", "date", "start", "end", "location"] as const).map(
                  (k, i) => (
                    <label key={k}>
                      {
                        [
                          "Тема занятия",
                          "Дата",
                          "Начало",
                          "Окончание",
                          "Место",
                        ][i]
                      }
                      <input
                        type={
                          k === "date"
                            ? "date"
                            : k === "start" || k === "end"
                              ? "time"
                              : "text"
                        }
                        required={k !== "location"}
                        maxLength={200}
                        value={sessionDraft.data[k]}
                        onChange={(e) => {
                          setSessionDraft({
                            ...sessionDraft,
                            data: { ...sessionDraft.data, [k]: e.target.value },
                          });
                          setDirty(true);
                        }}
                      />
                    </label>
                  ),
                )}
                <label>
                  Пояснение
                  <input
                    required={!!sessionDraft.id}
                    minLength={sessionDraft.id ? 3 : undefined}
                    maxLength={1000}
                    value={reason}
                    onChange={(e) => {
                      setReason(e.target.value);
                      setDirty(true);
                    }}
                  />
                </label>
                <button>Сохранить занятие</button>
                <button
                  type="button"
                  onClick={() => {
                    if (leave()) {
                      setSessionDraft(null);
                      setDirty(false);
                    }
                  }}
                >
                  Отмена
                </button>
              </form>
            )}
            {session && (
              <section className="clubs-attendance">
                <h4>
                  {session.data.title} · {session.data.date},{" "}
                  {session.data.start}–{session.data.end}
                </h4>
                <p>
                  {labels[session.status]} · {session.data.location}
                </p>
                <div className="clubs-actions">
                  {selected.canManage && selected.status !== "archived" && (
                    <>
                      {session.status === "planned" && (
                        <>
                          <button onClick={() => editSession(session)}>
                            Редактировать занятие
                          </button>
                          <button onClick={() => void sessionAction("start")}>
                            Открыть отметки посещения
                          </button>
                        </>
                      )}
                      {session.status === "active" && (
                        <button onClick={() => void sessionAction("complete")}>
                          Завершить занятие
                        </button>
                      )}
                      {session.status === "completed" && (
                        <button onClick={() => void sessionAction("reopen")}>
                          Открыть для исправления
                        </button>
                      )}
                      {["planned", "active"].includes(session.status) && (
                        <button onClick={() => void sessionAction("cancel")}>
                          Отменить занятие
                        </button>
                      )}
                    </>
                  )}
                  <button
                    onClick={() => {
                      if (leave()) {
                        setSession(null);
                        setDirty(false);
                        setReason("");
                      }
                    }}
                  >
                    Скрыть занятие
                  </button>
                </div>
                {marks.length > 0 ? (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      void run(async () => {
                        const x = await send(
                          base() + "/sessions/" + session.id + "/attendance",
                          "PUT",
                          {
                            version: session.version,
                            marks: marks.map(({ studentId, status, note }) => ({
                              studentId,
                              status,
                              note,
                            })),
                            reason,
                          },
                        );
                        setSession(x);
                        setMarks(x.marks);
                        setDirty(false);
                        setReason("");
                        await detail(selected.id);
                        setNotice("Посещение сохранено.");
                      });
                    }}
                  >
                    {marks.map((m, i) => (
                      <article key={m.studentId}>
                        <strong>
                          {m.studentName} · {m.className}
                        </strong>
                        {selected.canManage &&
                        session.status === "active" &&
                        selected.status !== "archived" ? (
                          <div className="clubs-grid">
                            <label>
                              Посещение
                              <select
                                value={m.status}
                                onChange={(e) => {
                                  setMarks(
                                    marks.map((a, j) =>
                                      j === i
                                        ? { ...a, status: e.target.value }
                                        : a,
                                    ),
                                  );
                                  setDirty(true);
                                }}
                              >
                                {[
                                  "unknown",
                                  "present",
                                  "absent",
                                  "excused",
                                  "late",
                                ].map((k) => (
                                  <option key={k} value={k}>
                                    {labels[k]}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label>
                              Пояснение для семьи
                              <input
                                maxLength={500}
                                value={m.note}
                                onChange={(e) => {
                                  setMarks(
                                    marks.map((a, j) =>
                                      j === i
                                        ? { ...a, note: e.target.value }
                                        : a,
                                    ),
                                  );
                                  setDirty(true);
                                }}
                              />
                            </label>
                          </div>
                        ) : (
                          <p>
                            {labels[m.status]}
                            {m.note ? " · " + m.note : ""}
                          </p>
                        )}
                      </article>
                    ))}
                    {selected.canManage &&
                      session.status === "active" &&
                      selected.status !== "archived" && (
                        <>
                          <label>
                            Основание отметок
                            <input
                              required
                              minLength={3}
                              maxLength={1000}
                              value={reason}
                              onChange={(e) => {
                                setReason(e.target.value);
                                setDirty(true);
                              }}
                            />
                          </label>
                          <button>Сохранить посещение</button>
                        </>
                      )}
                  </form>
                ) : (
                  <p>Отметок для просмотра пока нет.</p>
                )}
              </section>
            )}
            {history && options && (
              <section className="clubs-history">
                <h4>История кружка</h4>
                {history.items.map((h) => (
                  <article key={h.seq}>
                    <strong>{labels[h.action] || h.action}</strong>
                    <p>
                      {displayTime(h.created)} · {h.actor}
                    </p>
                    <p>{h.reason}</p>
                    <details>
                      <summary>Сохранённые данные</summary>
                      <HistoryValue value={h.data} classes={options.classes} />
                    </details>
                  </article>
                ))}
                {history.nextBefore && (
                  <button
                    onClick={() =>
                      void run(async () => {
                        const h = await api(
                          "clubs/" +
                            base() +
                            "/history?before=" +
                            history.nextBefore,
                          workspace,
                        );
                        setHistory({
                          items: [...history.items, ...h.items],
                          nextBefore: h.nextBefore,
                        });
                      })
                    }
                  >
                    Ещё история
                  </button>
                )}
                <button onClick={() => setHistory(null)}>Скрыть историю</button>
              </section>
            )}
          </section>
        )}
      </fieldset>
    </dialog>
  );
}

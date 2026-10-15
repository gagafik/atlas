import { useCallback, useEffect, useRef, useState } from "react";
import type { Account } from "./auth";
import { api } from "./api";
import { startPolling } from "./polling";
import QuizBankPanel from "./QuizBankPanel";
import type { Activity, BankItem, QuestionSnapshot } from "./quiz";
import "./LiveLessons.css";

const labels: Record<string, string> = {
  draft: "Черновик",
  live: "Идёт занятие",
  ended: "Завершено",
  cancelled: "Отменено",
  waiting: "Ожидание вопроса",
  open: "Приём ответов",
  closed: "Ответы собраны",
  revealed: "Разбор открыт",
};
const moment = (v: string) =>
  new Date(v).toLocaleTimeString("ru-RU", {
    timeZone: "Asia/Qyzylorda",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
type Props = { user: Account; initialWorkspace: string; onClose: () => void };
export default function LiveLessons(p: Props) {
  const spaces =
    p.user.role === "admin"
      ? ["school", "demo"]
      : [...new Set(p.user.grants.map((g) => g.workspace))];
  const [workspace, setWorkspace] = useState(
    spaces.includes(p.initialWorkspace)
      ? p.initialWorkspace
      : spaces[0] || "school",
  );
  return (
    <Workspace
      key={workspace}
      {...p}
      workspace={workspace}
      spaces={spaces}
      changeWorkspace={setWorkspace}
    />
  );
}
function Workspace({
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
  const dialog = useRef<HTMLDialogElement>(null),
    alive = useRef(true),
    busyRef = useRef(false),
    generation = useRef(0),
    listSequence = useRef(0),
    responseSequence = useRef(0),
    request = useRef({ key: "", id: "" });
  const [options, setOptions] = useState<any>(null),
    [list, setList] = useState<any>({ items: [] }),
    [before, setBefore] = useState(0),
    [filter, setFilter] = useState("all");
  const [id, setId] = useState(""),
    [student, setStudent] = useState(""),
    [detail, setDetail] = useState<any>(null),
    [status, setStatus] = useState<any>(null);
  const [editor, setEditor] = useState<any>(null),
    [bank, setBank] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true);
  const [seconds, setSeconds] = useState(120),
    [responseIndex, setResponseIndex] = useState<number | null>(null),
    [responses, setResponses] = useState<any>(null),
    [responseOffset, setResponseOffset] = useState(0),
    [history, setHistory] = useState<any>(null);
  const activities = useRef<Record<string, Activity>>({}),
    [activity, setActivity] = useState<Activity>({ dirty: false, busy: false });
  const track = useCallback((key: string, a: Activity) => {
    activities.current[key] = a;
    setActivity({
      dirty: Object.values(activities.current).some((x) => x.dirty),
      busy: Object.values(activities.current).some((x) => x.busy),
    });
  }, []);
  const bankActivity = useCallback((a: Activity) => track("bank", a), [track]);
  const leave = () =>
    !busyRef.current &&
    !activity.busy &&
    (!(editor || activity.dirty) ||
      window.confirm("Есть несохранённые изменения. Закрыть без сохранения?"));
  const read = (path: string) => api("live-lessons" + path, workspace);
  async function refreshList(cursor = before) {
    const token = ++listSequence.current;
    setLoading(true);
    try {
      const r = await read(`?before=${cursor}&status=${filter}`);
      if (alive.current && token === listSequence.current) {
        setList(r);
        setBefore(cursor);
      }
    } catch (e: any) {
      if (alive.current && token === listSequence.current) setError(e.message);
    } finally {
      if (alive.current && token === listSequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    alive.current = true;
    dialog.current?.showModal();
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (editor || activity.dirty || activity.busy || busyRef.current)
        e.preventDefault();
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [editor, activity]);
  useEffect(() => {
    let active = true;
    read("/options")
      .then((r) => {
        if (active) setOptions(r);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    void refreshList(0);
  }, [filter]);
  useEffect(() => {
    ++generation.current;
    if (!id) {
      setDetail(null);
      setStatus(null);
      return;
    }
    let active = true,
      etag = "",
      signature = "",
      terminal = false;
    const controller = new AbortController();
    setDetail(null);
    setStatus(null);
    setError("");
    setResponses(null);
    setHistory(null);
    const poll = startPolling(
      async () => {
        const token = generation.current;
        try {
          const r = await fetch(
            `/api/live-lessons/${encodeURIComponent(id)}/status?workspace=${encodeURIComponent(workspace)}&studentId=${encodeURIComponent(student)}`,
            {
              headers: etag ? { "If-None-Match": etag } : {},
              signal: controller.signal,
            },
          );
          if (r.status === 304) return true;
          if (!r.ok) {
            const body = await r.json();
            if (
              active &&
              token === generation.current &&
              [401, 403, 404].includes(r.status)
            ) {
              setDetail(null);
              setStatus(null);
              setResponses(null);
              setHistory(null);
            }
            throw new Error(
              typeof body.detail === "string"
                ? body.detail
                : "Не удалось обновить занятие.",
            );
          }
          const value = await r.json();
          terminal = ["ended", "cancelled"].includes(value.status);
          const next = JSON.stringify([
            value.version,
            value.phase,
            options?.staff ? null : value.answerRevision,
          ]);
          if (next !== signature) {
            const d = await read(
              `/${id}?studentId=${encodeURIComponent(student)}`,
            );
            if (active && token === generation.current && !busyRef.current) {
              setDetail(d);
              signature = next;
            }
          }
          if (active && token === generation.current && !busyRef.current) {
            setStatus(value);
            setError("");
            etag = r.headers.get("ETag") || "";
          }
          return true;
        } catch (e: any) {
          if (active && token === generation.current && !busyRef.current)
            setError(e.message);
          return false;
        }
      },
      {
        interval: () => (terminal ? 30000 : options?.staff ? 4000 : 5000),
        paused: () => busyRef.current,
      },
    );
    return () => {
      active = false;
      controller.abort();
      poll.stop();
    };
  }, [id, student, options?.staff]);
  async function mutate(path: string, method: string, body: any) {
    if (busyRef.current) return;
    ++generation.current;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const key = JSON.stringify([path, method, body]);
    if (request.current.key !== key)
      request.current = { key, id: crypto.randomUUID() };
    try {
      const r = await api("live-lessons" + path, workspace, method, {
        ...body,
        requestId: request.current.id,
      });
      request.current = { key: "", id: "" };
      setEditor(null);
      setResponses(null);
      setHistory(null);
      if (r.id) {
        setId(r.id);
        setDetail(
          await read(`/${r.id}?studentId=${encodeURIComponent(student)}`),
        );
      }
      await refreshList();
      setNotice("Сохранено.");
      return r;
    } catch (e: any) {
      setError(e.message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  const control = (action: string, index = 0) => {
    if (!detail || !leave()) return;
    const reason =
      action === "cancel" ? window.prompt("Причина отмены занятия") : "";
    if (reason === null) return;
    if (
      ["start", "end"].includes(action) &&
      !window.confirm(
        action === "start"
          ? "Начать занятие для текущего состава группы? Вопросы после начала менять нельзя."
          : "Завершить занятие и закрыть приём ответов?",
      )
    )
      return;
    void mutate(`/${id}/control`, "POST", {
      version: detail.version,
      action,
      questionIndex: index,
      seconds,
      reason,
    });
  };
  async function loadResponses(index: number, offset = 0, force = false) {
    if (!force && !leave()) return;
    const token = ++responseSequence.current,
      view = generation.current;
    setResponseIndex(index);
    setResponseOffset(offset);
    setResponses(null);
    setError("");
    try {
      const r = await read(
        `/${id}/responses?questionIndex=${index}&offset=${offset}`,
      );
      if (
        alive.current &&
        token === responseSequence.current &&
        view === generation.current
      )
        setResponses(r);
    } catch (e: any) {
      if (
        alive.current &&
        token === responseSequence.current &&
        view === generation.current
      )
        setError(e.message);
    }
  }
  async function loadHistory(cursor = 0) {
    const view = generation.current;
    try {
      const result = await read(`/${id}/history?before=${cursor}`);
      if (alive.current && view === generation.current) setHistory(result);
    } catch (e: any) {
      if (alive.current && view === generation.current) setError(e.message);
    }
  }
  const select = (next: string) => {
    if (leave()) {
      ++generation.current;
      setEditor(null);
      setBank(false);
      setId(next);
      setResponses(null);
      setHistory(null);
    }
  };
  const current = detail?.rounds.at(-1);
  const choose = (item: BankItem) => {
    if (!editor.questions.some((q: QuestionSnapshot) => q.id === item.id))
      setEditor({ ...editor, questions: [...editor.questions, item] });
  };
  return (
    <dialog
      ref={dialog}
      className="live-dialog"
      aria-labelledby="live-title"
      onCancel={(e) => {
        e.preventDefault();
        if (leave()) onClose();
      }}
    >
      <header>
        <div>
          <small>АТЛАС · УЧЕБНАЯ РАБОТА</small>
          <h2 id="live-title">Интерактивные занятия</h2>
          <p>Вопросы в темпе учителя, ответы и совместный разбор.</p>
        </div>
        <button
          onClick={() => {
            if (leave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      <div className="live-toolbar">
        <label>
          Пространство{" "}
          <select
            value={workspace}
            disabled={busy || activity.busy}
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
        {options?.staff && (
          <>
            <button
              disabled={busy || activity.busy}
              onClick={() => {
                if (leave()) {
                  setId("");
                  setEditor(null);
                  setBank(!bank);
                }
              }}
            >
              {bank ? "К занятиям" : "Банк вопросов"}
            </button>
            <button
              disabled={busy || activity.busy || !options.pairs.length}
              onClick={() => {
                if (leave()) {
                  setBank(false);
                  setId("");
                  setEditor({
                    title: "",
                    instructions: "",
                    ...options.pairs[0],
                    questions: [],
                  });
                }
              }}
            >
              Новое занятие
            </button>
          </>
        )}
      </div>
      {error && (
        <p className="live-error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!options && <p>Загрузка доступа…</p>}
      {bank && (
        <QuizBankPanel workspace={workspace} onActivity={bankActivity} />
      )}
      {editor && (
        <section className="live-card">
          <h3>{id ? "Редактирование" : "Подготовка занятия"}</h3>
          <fieldset disabled={busy || activity.busy}>
            <label>
              Название
              <input
                value={editor.title}
                maxLength={160}
                onChange={(e) =>
                  setEditor({ ...editor, title: e.target.value })
                }
              />
            </label>
            <label>
              Группа и предмет
              <select
                value={JSON.stringify([editor.groupId, editor.subjectId])}
                onChange={(e) => {
                  const [groupId, subjectId] = JSON.parse(e.target.value);
                  setEditor({ ...editor, groupId, subjectId });
                }}
              >
                {options?.pairs.map((p: any) => (
                  <option
                    key={JSON.stringify([p.groupId, p.subjectId])}
                    value={JSON.stringify([p.groupId, p.subjectId])}
                  >
                    {p.group} · {p.subject} ({p.studentCount} уч.)
                  </option>
                ))}
              </select>
            </label>
            <label>
              Инструкция
              <textarea
                value={editor.instructions}
                maxLength={4000}
                onChange={(e) =>
                  setEditor({ ...editor, instructions: e.target.value })
                }
              />
            </label>
            <h4>Порядок вопросов · {editor.questions.length}/50</h4>
            <ol>
              {editor.questions.map((q: QuestionSnapshot, i: number) => (
                <li key={q.id}>
                  <p>{q.question.text}</p>
                  <button
                    disabled={!i}
                    onClick={() => {
                      const qs = [...editor.questions];
                      [qs[i - 1], qs[i]] = [qs[i], qs[i - 1]];
                      setEditor({ ...editor, questions: qs });
                    }}
                  >
                    Выше
                  </button>{" "}
                  <button
                    onClick={() =>
                      setEditor({
                        ...editor,
                        questions: editor.questions.filter(
                          (_: any, j: number) => i !== j,
                        ),
                      })
                    }
                  >
                    Убрать
                  </button>
                </li>
              ))}
            </ol>
            <button
              disabled={
                !editor.title.trim() ||
                !editor.questions.length ||
                editor.questions.length > 50
              }
              onClick={() =>
                void mutate(id ? `/${id}` : "", id ? "PUT" : "POST", {
                  version: id ? detail.version : 0,
                  definition: {
                    title: editor.title,
                    instructions: editor.instructions,
                    groupId: editor.groupId,
                    subjectId: editor.subjectId,
                    questions: editor.questions.map((q: QuestionSnapshot) => ({
                      id: q.id,
                      version: q.version,
                    })),
                  },
                })
              }
            >
              Сохранить занятие
            </button>{" "}
            <button
              onClick={() => {
                if (leave()) setEditor(null);
              }}
            >
              Отменить редактирование
            </button>
          </fieldset>
          <p>
            Добавьте вопросы из своего банка. Вопросы сохраняются в занятии
            отдельной копией.
          </p>
          <QuizBankPanel
            workspace={workspace}
            onActivity={bankActivity}
            onPick={choose}
          />
        </section>
      )}
      {!bank && !editor && (
        <div className="live-layout">
          <nav aria-label="Список занятий">
            <label>
              Состояние
              <select
                value={filter}
                disabled={busy}
                onChange={(e) => {
                  if (leave()) {
                    setId("");
                    setFilter(e.target.value);
                  }
                }}
              >
                {["all", "draft", "live", "ended", "cancelled"]
                  .filter((x) => options?.staff || x !== "draft")
                  .map((x) => (
                    <option key={x} value={x}>
                      {labels[x] || "Все занятия"}
                    </option>
                  ))}
              </select>
            </label>
            <button onClick={() => void refreshList(0)} disabled={loading}>
              Обновить список
            </button>
            {loading ? (
              <p role="status">Загружаем…</p>
            ) : !list.items.length ? (
              <p>Занятий пока нет.</p>
            ) : (
              list.items.map((r: any) => (
                <button
                  key={r.id}
                  className={"live-list-item " + (id === r.id ? "active" : "")}
                  aria-pressed={id === r.id}
                  onClick={() => select(r.id)}
                >
                  <strong>{r.title}</strong>
                  <span>{labels[r.status]}</span>
                </button>
              ))
            )}
            {before > 0 && (
              <button onClick={() => void refreshList(0)}>
                К новым занятиям
              </button>
            )}
            {list.nextBefore && (
              <button onClick={() => void refreshList(list.nextBefore)}>
                Более ранние
              </button>
            )}
          </nav>
          <main>
            {!id ? (
              <p>
                Выберите занятие. Ученики увидят его после запуска учителем.
              </p>
            ) : !detail ? (
              <p>Загружаем занятие…</p>
            ) : (
              <>
                <section className="live-card">
                  <small>
                    {labels[detail.status]} · {detail.group} · {detail.subject}
                  </small>
                  <h3>{detail.title}</h3>
                  <p className="live-text">{detail.instructions}</p>
                  {!detail.manage && detail.students.length > 1 && (
                    <label>
                      Ученик
                      <select
                        value={detail.studentId}
                        onChange={(e) => {
                          if (leave()) setStudent(e.target.value);
                        }}
                      >
                        {detail.students.map((p: any) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {detail.manage && (
                    <fieldset disabled={busy || activity.busy}>
                      <div className="live-toolbar">
                        {detail.status === "draft" && (
                          <>
                            <button
                              onClick={() => setEditor(detail.definition)}
                            >
                              Редактировать занятие
                            </button>
                            <button onClick={() => control("start")}>
                              Начать занятие
                            </button>
                          </>
                        )}
                        {detail.status === "live" && (
                          <>
                            <label>
                              Время на следующий вопрос, сек.
                              <input
                                type="number"
                                min={10}
                                max={7200}
                                value={seconds}
                                onChange={(e) =>
                                  setSeconds(Number(e.target.value))
                                }
                              />
                            </label>
                            <button
                              disabled={
                                current?.phase === "open" ||
                                detail.rounds.length >= detail.questionCount ||
                                seconds < 10 ||
                                seconds > 7200
                              }
                              onClick={() =>
                                control("open", detail.rounds.length)
                              }
                            >
                              Открыть следующий вопрос
                            </button>
                            <button onClick={() => control("end")}>
                              Завершить занятие
                            </button>
                          </>
                        )}
                        {["draft", "live"].includes(detail.status) && (
                          <button onClick={() => control("cancel")}>
                            Отменить занятие
                          </button>
                        )}
                        <button onClick={() => control("copy")}>
                          Создать копию
                        </button>
                        <button onClick={() => void loadHistory()}>
                          История действий
                        </button>
                        <a
                          href={`/api/live-lessons/${id}/export.csv?workspace=${workspace}`}
                        >
                          Скачать ответы CSV
                        </a>
                      </div>
                    </fieldset>
                  )}
                  {detail.manage && status && (
                    <p role="status">
                      Участников: {status.participants}. Сохранили текущий
                      ответ: {status.saved}; отправили: {status.submitted}.
                    </p>
                  )}
                  {!detail.rounds.length && (
                    <p>
                      {detail.status === "draft"
                        ? `Подготовлено вопросов: ${detail.questionCount}.`
                        : "Ждём, когда учитель откроет первый вопрос."}
                    </p>
                  )}
                  <p className="live-note">
                    Баллы этого занятия хранятся отдельно от школьного журнала.
                  </p>
                </section>
                {[...detail.rounds].reverse().map((r: any) => (
                  <section className="live-card" key={r.index}>
                    <h3>
                      Вопрос {r.index + 1} из {detail.questionCount}
                    </h3>
                    <p>
                      {labels[r.phase]}
                      {r.phase === "open"
                        ? ` · до ${moment(r.deadline)} (время школы)`
                        : ""}
                    </p>
                    <p className="live-text">{r.question.text}</p>
                    {detail.manage ? (
                      <>
                        <ul>
                          {r.question.options.map((o: any) => (
                            <li key={o.id}>{o.text}</li>
                          ))}
                        </ul>
                        <fieldset disabled={busy}>
                          <div className="live-toolbar">
                            {r.phase === "open" && (
                              <button onClick={() => control("close", r.index)}>
                                Закрыть приём ответов
                              </button>
                            )}
                            {r.phase === "closed" &&
                              detail.status !== "cancelled" && (
                                <button
                                  onClick={() => control("reveal", r.index)}
                                >
                                  Показать разбор ученикам
                                </button>
                              )}
                            <button onClick={() => void loadResponses(r.index)}>
                              Ответы учеников
                            </button>
                          </div>
                        </fieldset>
                      </>
                    ) : user.role === "student" ? (
                      <AnswerEditor
                        key={detail.studentId + ":" + r.index}
                        workspace={workspace}
                        id={id}
                        student={detail.studentId}
                        round={r}
                        track={track}
                      />
                    ) : r.response ? (
                      <AnswerText
                        answer={r.response.answer}
                        question={r.question}
                      />
                    ) : (
                      <p>Ответ ребёнка появится после открытия разбора.</p>
                    )}
                    {r.solution && (
                      <details open={r.phase === "revealed"}>
                        <summary>
                          {detail.manage && r.phase !== "revealed"
                            ? "Эталон — пока виден только учителю"
                            : "Разбор"}
                        </summary>
                        <Solution question={r.solution} />
                        {r.response?.mark && (
                          <p>
                            Баллы: {r.response.mark.score} / {r.question.points}
                            . {r.response.mark.comment}
                          </p>
                        )}
                        {r.response && r.response.mark === null && (
                          <p>Ожидает проверки учителя.</p>
                        )}
                      </details>
                    )}
                  </section>
                ))}
                {responses && responseIndex !== null && (
                  <section className="live-card">
                    <h3>Ответы · вопрос {responseIndex + 1}</h3>
                    <button
                      onClick={() =>
                        void loadResponses(responseIndex, responseOffset)
                      }
                    >
                      Обновить ответы
                    </button>
                    <p>
                      Показано {responses.items.length} из{" "}
                      {responses.totalCount}. {labels[responses.phase]}
                    </p>
                    {responses.items.map((r: any) => (
                      <ResponseReview
                        key={`${responseIndex}:${r.studentId}:${r.response?.version || 0}`}
                        row={r}
                        question={
                          detail.rounds.find(
                            (x: any) => x.index === responseIndex,
                          )?.question
                        }
                        phase={responses.phase}
                        busy={busy}
                        track={track}
                        onSave={async (value: any) => {
                          const result = await mutate(`/${id}/review`, "PUT", {
                            ...value,
                            studentId: r.studentId,
                            questionIndex: responseIndex,
                            version: r.response.version,
                          });
                          if (result)
                            await loadResponses(
                              responseIndex,
                              responseOffset,
                              true,
                            );
                        }}
                      />
                    ))}
                    {responseOffset > 0 && (
                      <button
                        onClick={() =>
                          void loadResponses(
                            responseIndex,
                            Math.max(0, responseOffset - 50),
                          )
                        }
                      >
                        Предыдущие 50
                      </button>
                    )}
                    {responses.nextOffset !== null && (
                      <button
                        onClick={() =>
                          void loadResponses(
                            responseIndex,
                            responses.nextOffset,
                          )
                        }
                      >
                        Следующие 50
                      </button>
                    )}
                    <button
                      onClick={() => {
                        if (leave()) setResponses(null);
                      }}
                    >
                      Скрыть ответы
                    </button>
                  </section>
                )}
                {history && (
                  <section className="live-card">
                    <h3>История действий</h3>
                    <ul>
                      {history.items.map((h: any) => (
                        <li key={h.seq}>
                          {new Date(h.created).toLocaleString("ru-RU")} ·{" "}
                          {h.actor_name} ·{" "}
                          {(
                            {
                              saved: "Сохранение",
                              start: "Начало занятия",
                              open: "Открытие вопроса",
                              close: "Закрытие приёма",
                              reveal: "Открытие разбора",
                              end: "Завершение",
                              cancel: "Отмена",
                              copied: "Копирование",
                              submitted: "Отправка ответа",
                              reviewed: "Проверка",
                            } as Record<string, string>
                          )[h.action] || h.action}
                          {h.detail.reason && ` · ${h.detail.reason}`}
                        </li>
                      ))}
                    </ul>
                    {history.nextBefore && (
                      <button
                        onClick={() => void loadHistory(history.nextBefore)}
                      >
                        Ранее
                      </button>
                    )}
                    <button onClick={() => setHistory(null)}>
                      Скрыть историю
                    </button>
                  </section>
                )}
              </>
            )}
          </main>
        </div>
      )}
    </dialog>
  );
}

function AnswerText({ answer, question }: { answer: any; question: any }) {
  return (
    <p className="live-text">
      {answer.text ||
        question.options
          .filter((o: any) => answer.selected.includes(o.id))
          .map((o: any) => o.text)
          .join("; ") ||
        "Пустой ответ"}
    </p>
  );
}
function Solution({ question: q }: { question: any }) {
  return (
    <div className="live-text">
      <p>
        {q.kind === "essay"
          ? "Развёрнутый ответ проверяет учитель."
          : q.kind === "number"
            ? `${q.number} (допуск ${q.tolerance})`
            : q.kind === "short"
              ? q.accepted.join(" / ")
              : q.options
                  .filter((o: any) => q.correctIds.includes(o.id))
                  .map((o: any) => o.text)
                  .join("; ")}
      </p>
      <p>{q.explanation}</p>
    </div>
  );
}
function AnswerEditor({
  workspace,
  id,
  student,
  round,
  track,
}: {
  workspace: string;
  id: string;
  student: string;
  round: any;
  track: (key: string, a: Activity) => void;
}) {
  const [answer, setAnswer] = useState(
      round.response?.answer || { selected: [], text: "" },
    ),
    [version, setVersion] = useState(round.response?.version || 0),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false),
    [submitting, setSubmitting] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [submitted, setSubmitted] = useState(!!round.response?.submitted);
  const current = useRef<any>(null),
    pending = useRef(false),
    replay = useRef({ key: "", id: "" }),
    alive = useRef(true);
  current.current = {
    answer,
    version,
    dirty,
    canAnswer: round.canAnswer,
    submitted,
  };
  const key = student + ":" + round.index;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    track(key, { dirty, busy });
    return () => track(key, { dirty: false, busy: false });
  }, [dirty, busy, key, track]);
  useEffect(() => {
    if (
      !dirty &&
      !pending.current &&
      round.response &&
      round.response.version > version
    ) {
      setAnswer(round.response.answer);
      setVersion(round.response.version);
      setSubmitted(round.response.submitted);
    }
  }, [round.response, dirty, version]);
  async function save(submit = false) {
    const c = current.current;
    if (pending.current || !c.canAnswer || c.submitted) return;
    pending.current = true;
    setBusy(true);
    setSubmitting(submit);
    setError("");
    const body = {
      version: c.version,
      studentId: student,
      questionIndex: round.index,
      answer: c.answer,
      submit,
    };
    const fingerprint = JSON.stringify(body);
    if (replay.current.key !== fingerprint)
      replay.current = { key: fingerprint, id: crypto.randomUUID() };
    try {
      const r = await api(`live-lessons/${id}/answer`, workspace, "PUT", {
        ...body,
        requestId: replay.current.id,
      });
      replay.current = { key: "", id: "" };
      if (alive.current) {
        setVersion(r.version);
        setSubmitted(r.submitted);
        setDirty(
          JSON.stringify(current.current.answer) !== JSON.stringify(c.answer),
        );
        setNotice(submit ? "Ответ отправлен." : "Черновик сохранён.");
      }
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      pending.current = false;
      if (alive.current) {
        setBusy(false);
        setSubmitting(false);
      }
    }
  }
  useEffect(() => {
    if (!dirty || busy || error || !round.canAnswer) return;
    const t = setTimeout(() => void save(), 1200);
    return () => clearTimeout(t);
  }, [dirty, answer, busy, error, round.canAnswer]);
  function patch(a: any) {
    setAnswer(a);
    setDirty(true);
    setNotice("");
    setError("");
  }
  async function reload() {
    if (
      dirty &&
      !window.confirm("Заменить несохранённый текст ответом с сервера?")
    )
      return;
    try {
      const d = await api(`live-lessons/${id}?studentId=${student}`, workspace);
      const r = d.rounds.find((x: any) => x.index === round.index)?.response;
      setAnswer(r?.answer || { selected: [], text: "" });
      setVersion(r?.version || 0);
      setSubmitted(!!r?.submitted);
      setDirty(false);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }
  return (
    <div>
      <fieldset disabled={submitting || !round.canAnswer || submitted}>
        {["single", "multiple"].includes(round.question.kind) ? (
          round.question.options.map((o: any) => (
            <label className="live-choice" key={o.id}>
              <input
                type={round.question.kind === "single" ? "radio" : "checkbox"}
                name={key}
                checked={answer.selected.includes(o.id)}
                onChange={(e) =>
                  patch({
                    ...answer,
                    selected:
                      round.question.kind === "single"
                        ? [o.id]
                        : e.target.checked
                          ? [...answer.selected, o.id]
                          : answer.selected.filter((v: string) => v !== o.id),
                  })
                }
              />
              {o.text}
            </label>
          ))
        ) : (
          <label>
            Ваш ответ
            <textarea
              value={answer.text}
              maxLength={10000}
              onChange={(e) => patch({ ...answer, text: e.target.value })}
            />
          </label>
        )}
        {round.canAnswer && !submitted && (
          <div className="live-toolbar">
            <button onClick={() => void save()} disabled={!dirty || busy}>
              Сохранить ответ
            </button>
            <button disabled={busy} onClick={() => void save(true)}>
              Отправить окончательно
            </button>
          </div>
        )}
      </fieldset>
      {submitted && <p>Ответ отправлен. Изменения закрыты.</p>}
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p role="alert" className="live-error">
          {error}
        </p>
      )}
      {dirty && !round.canAnswer && (
        <p role="alert">
          Приём закрыт. Последние несохранённые изменения не отправлены; учтён
          ответ, который сервер сохранил раньше.
        </p>
      )}
      {error && (
        <button disabled={busy} onClick={() => void reload()}>
          Загрузить сохранённый ответ
        </button>
      )}
      <small>
        Черновик сохраняется автоматически. При закрытии вопроса учитывается
        последняя сохранённая версия.
      </small>
    </div>
  );
}
function ResponseReview({
  row,
  question,
  phase,
  busy,
  onSave,
  track,
}: {
  row: any;
  question: any;
  phase: string;
  busy: boolean;
  onSave: (value: any) => Promise<void>;
  track: (key: string, a: Activity) => void;
}) {
  const [editing, setEditing] = useState(false),
    [score, setScore] = useState(row.response?.mark?.score ?? 0),
    [comment, setComment] = useState(row.response?.mark?.comment || ""),
    [reason, setReason] = useState(""),
    [dirty, setDirty] = useState(false);
  useEffect(() => {
    const key = "review:" + row.studentId;
    track(key, { dirty, busy });
    return () => track(key, { dirty: false, busy: false });
  }, [dirty, busy, row.studentId, track]);
  return (
    <article className="live-response">
      <h4>{row.name}</h4>
      {row.response ? (
        <>
          <AnswerText answer={row.response.answer} question={question} />
          <p>
            {row.response.submitted ? "Отправлен" : "Сохранён"}
            {phase !== "open" &&
              ` · ${row.response.mark ? `${row.response.mark.score} / ${question.points} б.` : "Ожидает проверки"}`}
          </p>
          {phase !== "open" && (
            <button onClick={() => setEditing(!editing)} disabled={busy}>
              Проверить ответ
            </button>
          )}
          {editing && (
            <fieldset disabled={busy}>
              <label>
                Балл
                <input
                  type="number"
                  min={0}
                  max={question.points}
                  step="any"
                  value={score}
                  onChange={(e) => {
                    setDirty(true);
                    setScore(Number(e.target.value));
                  }}
                />
              </label>
              <label>
                Комментарий
                <textarea
                  value={comment}
                  maxLength={4000}
                  onChange={(e) => {
                    setDirty(true);
                    setComment(e.target.value);
                  }}
                />
              </label>
              <label>
                Причина изменения результата
                <input
                  value={reason}
                  maxLength={1000}
                  onChange={(e) => {
                    setDirty(true);
                    setReason(e.target.value);
                  }}
                />
              </label>
              <button onClick={() => void onSave({ score, comment, reason })}>
                Сохранить проверку
              </button>
            </fieldset>
          )}
        </>
      ) : (
        <p>Ответ не сохранён.</p>
      )}
    </article>
  );
}

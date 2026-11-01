import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { canLeaveHomework } from "./homeworkNavigation";
import { csvCell, download } from "./utils";
import QuizBankPanel from "./QuizBankPanel";
import QuizBuilder from "./QuizBuilder";
import QuizAttemptPanel from "./QuizAttemptPanel";
import QuizTransferPanel from "./QuizTransferPanel";
import { quizStatuses, schoolDate, type Activity, type Quiz } from "./quiz";
import { reviewedScore, type TransferSummary } from "./quizTransfer";

interface Item {
  id: string;
  version: number;
  title: string;
  subject: string;
  group: string;
  course: boolean;
  status: string;
  opensAt: string;
  closesAt: string;
  studentId: string | null;
  student: string | null;
  readOnly: boolean;
  released: boolean;
}

function Detail({
  workspace,
  id,
  studentId,
  onActivity,
  onClose,
  onEdit,
  onChanged,
}: {
  workspace: string;
  id: string;
  studentId?: string;
  onActivity: (activity: Activity) => void;
  onClose: () => void;
  onEdit: (quiz: Quiz, copy: boolean) => void;
  onChanged: () => void;
}) {
  const [quiz, setQuiz] = useState<Quiz | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [reason, setReason] = useState(""),
    [epoch, setEpoch] = useState(0),
    [selected, setSelected] = useState("");
  const [summary, setSummary] = useState<TransferSummary | null>(null),
    [transferOpen, setTransferOpen] = useState(false);
  const [activity, setActivity] = useState<Activity>({
    dirty: false,
    busy: false,
  });
  const alive = useRef(true),
    requestId = useRef(crypto.randomUUID());
  const endpoint = `journal/quizzes/${encodeURIComponent(id)}`;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onActivity({
      dirty: activity.dirty || !!reason,
      busy: activity.busy || busy,
    });
    return () => onActivity({ dirty: false, busy: false });
  }, [activity, reason, busy, onActivity]);
  useEffect(() => {
    let live = true;
    setBusy(true);
    setError("");
    api(
      endpoint +
        (studentId ? `?studentId=${encodeURIComponent(studentId)}` : ""),
      workspace,
    )
      .then((r) => {
        if (live) setQuiz(r);
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
  }, [workspace, endpoint, studentId, epoch]);
  // What was transferred to the journal, for the results table; only those who manage the test read it.
  const manages = !!quiz?.staff && !!quiz.canManage;
  useEffect(() => {
    if (!manages) {
      setSummary(null);
      return;
    }
    let live = true;
    api(endpoint + "/transfer", workspace)
      .then((r: TransferSummary) => {
        if (live) setSummary(r);
      })
      .catch(() => {
        if (live) setSummary(null);
      });
    return () => {
      live = false;
    };
  }, [workspace, endpoint, manages, epoch]);
  async function run(fn: () => Promise<void>) {
    if (busy || activity.busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function act(action: string) {
    if (!quiz) return;
    const result = await api(endpoint + "/actions", workspace, "POST", {
      version: quiz.version,
      action,
      reason,
    });
    if (alive.current) {
      setQuiz(result);
      setReason("");
      onChanged();
    }
  }
  async function start() {
    if (!quiz) return;
    const result = await api(endpoint + "/attempts", workspace, "POST", {
      version: quiz.version,
      studentId,
      requestId: requestId.current,
    });
    if (alive.current) {
      setSelected(result.id);
      requestId.current = crypto.randomUUID();
      setEpoch((v) => v + 1);
      onChanged();
    }
  }
  function csv() {
    if (!quiz?.students) return;
    const rows: unknown[][] = [
      [
        "Ученик",
        "Вариант",
        "Попытка",
        "Статус",
        "Баллы",
        "Максимум",
        "Отправлено",
      ],
    ];
    for (const pupil of quiz.students) {
      if (!pupil.attempts.length)
        rows.push([pupil.name, pupil.variant, "", "Не начато", "", "", ""]);
      for (const a of pupil.attempts)
        rows.push([
          pupil.name,
          pupil.variant,
          a.number,
          quizStatuses[a.status],
          a.score ?? "",
          a.maxScore ?? "",
          a.submittedAt ? schoolDate(a.submittedAt) : "",
        ]);
    }
    download(
      "Результаты теста.csv",
      "\uFEFF" +
        rows
          .map((row) => row.map((value) => csvCell(String(value))).join(";"))
          .join("\r\n"),
      "text/csv;charset=utf-8",
    );
  }
  return (
    <section className="quiz-detail">
      <div className="dated-toolbar">
        <button
          disabled={busy || activity.busy}
          onClick={() => {
            if (canLeaveHomework()) onClose();
          }}
        >
          Закрыть тест
        </button>
        <button
          disabled={busy || activity.busy}
          onClick={() => setEpoch((v) => v + 1)}
        >
          Обновить тест
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {!quiz ? (
        <p role="status">Загружаем тест…</p>
      ) : (
        <>
          <h2>{quiz.title}</h2>
          <p>
            {quiz.subject} · {quiz.group}
            {quiz.scope === "course" ? " · весь курс" : ""} ·{" "}
            {quizStatuses[quiz.status]}
            {quiz.released ? " · разбор открыт" : ""}
          </p>
          <p className="journal-text">{quiz.instructions}</p>
          <p>
            Открытие: {schoolDate(quiz.opensAt)} · закрытие:{" "}
            {schoolDate(quiz.closesAt)} · время школы
          </p>
          <p>
            {quiz.timeLimitMinutes} минут на попытку · попыток:{" "}
            {quiz.maxAttempts}
          </p>
          {quiz.staff && !selected && (
            <>
              <div className="dated-toolbar">
                {quiz.status === "draft" && quiz.canManage && (
                  <button disabled={busy} onClick={() => onEdit(quiz, false)}>
                    Редактировать тест
                  </button>
                )}
                <button disabled={busy} onClick={() => onEdit(quiz, true)}>
                  Создать копию теста
                </button>
                {quiz.students && (
                  <button disabled={busy} onClick={csv}>
                    Скачать результаты CSV
                  </button>
                )}
                {quiz.canTransfer &&
                  summary &&
                  quiz.status !== "draft" &&
                  quiz.status !== "withdrawn" && (
                    <button
                      className="primary"
                      aria-pressed={transferOpen}
                      disabled={busy}
                      onClick={() => setTransferOpen((v) => !v)}
                    >
                      Перенести в журнал
                    </button>
                  )}
              </div>
              {transferOpen && quiz.canTransfer && summary && (
                <QuizTransferPanel
                  workspace={workspace}
                  quiz={quiz}
                  summary={summary}
                  onActivity={setActivity}
                  onChanged={() => {
                    setEpoch((v) => v + 1);
                    onChanged();
                  }}
                  onClose={() => setTransferOpen(false)}
                />
              )}
              {quiz.canManage && quiz.status !== "withdrawn" && (
                <fieldset className="access-fields" disabled={busy}>
                  <label className="field">
                    <span>Причина завершения, отзыва или открытия разбора</span>
                    <textarea
                      rows={2}
                      maxLength={1000}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </label>
                  <div className="dated-toolbar">
                    {quiz.status === "published" && (
                      <button
                        disabled={!reason.trim()}
                        onClick={() => {
                          if (
                            window.confirm(
                              "Завершить приём и отправить последние сохранённые ответы всех активных попыток?",
                            )
                          )
                            void run(() => act("close"));
                        }}
                      >
                        Завершить приём ответов
                      </button>
                    )}
                    {quiz.status !== "draft" && !quiz.released && (
                      <button
                        disabled={!reason.trim()}
                        onClick={() => void run(() => act("release"))}
                      >
                        Открыть разбор ученикам
                      </button>
                    )}
                    <button
                      disabled={!reason.trim()}
                      onClick={() => {
                        if (
                          window.confirm(
                            "Отозвать назначение теста? Начатые попытки сохранятся в истории и завершатся.",
                          )
                        )
                          void run(() => act("withdraw"));
                      }}
                    >
                      Отозвать тест
                    </button>
                  </div>
                  <small>
                    Разбор раскрывает баллы, правильные ответы и объяснения
                    семье. Он доступен только после завершения приёма и ручной
                    проверки всех отправленных попыток.
                  </small>
                </fieldset>
              )}
              {quiz.students && (
                <div className="quiz-results">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Ученик</th>
                        <th scope="col">Вариант</th>
                        <th scope="col">Попытки</th>
                        <th scope="col">Баллы</th>
                        <th scope="col">В журнале</th>
                      </tr>
                    </thead>
                    <tbody>
                      {quiz.students.map((pupil) => {
                        const reviewed = reviewedScore(pupil.attempts),
                          mark = summary?.marks.find(
                            (m) => m.studentId === pupil.id,
                          );
                        return (
                          <tr key={pupil.id}>
                            <th scope="row">
                              {pupil.name}
                              {!pupil.enrolled ? " · прежний состав" : ""}
                            </th>
                            <td>
                              {quiz.document?.definition.variants.find(
                                (v) => v.id === pupil.variant,
                              )?.name || pupil.variant}
                            </td>
                            <td>
                              {!pupil.attempts.length && "Тест ещё не начат."}
                              {pupil.attempts.map((a) => (
                                <button
                                  key={a.id}
                                  disabled={busy}
                                  onClick={() => {
                                    if (
                                      !reason ||
                                      window.confirm(
                                        "Сбросить несохранённую причину действия?",
                                      )
                                    ) {
                                      setReason("");
                                      setSelected(a.id);
                                    }
                                  }}
                                >
                                  Попытка {a.number} · {quizStatuses[a.status]}
                                  {a.score !== null && a.score !== undefined
                                    ? ` · ${a.score}/${a.maxScore}`
                                    : ""}
                                </button>
                              ))}
                            </td>
                            <td>
                              {reviewed
                                ? `${reviewed.score}/${reviewed.maxScore}`
                                : "—"}
                            </td>
                            <td>
                              {mark ? (
                                <>
                                  {mark.value} ·{" "}
                                  {mark.day.split("-").reverse().join(".")}
                                  <small>
                                    {mark.work} · {mark.actor}
                                    {mark.stale
                                      ? " · устарело: результат теста изменился"
                                      : ""}
                                  </small>
                                </>
                              ) : (
                                "—"
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
          {!quiz.staff && !selected && (
            <>
              {quiz.canStart && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => {
                    if (
                      quiz.attempts?.some((a) => a.status === "active") ||
                      window.confirm(
                        "Начать попытку? С этого момента запускается таймер.",
                      )
                    )
                      void run(start);
                  }}
                >
                  {quiz.attempts?.some((a) => a.status === "active")
                    ? "Продолжить попытку"
                    : "Начать тест"}
                </button>
              )}
              {!quiz.canStart && (
                <p className="info-line">
                  Новая попытка сейчас недоступна: проверьте время приёма и
                  число использованных попыток. Родитель может просматривать
                  отправленные работы.
                </p>
              )}
              <div className="quiz-cards">
                {quiz.attempts?.map((a) => (
                  <article className="quiz-card" key={a.id}>
                    <strong>
                      Попытка {a.number} · {quizStatuses[a.status]}
                    </strong>
                    <p>Начало: {schoolDate(a.startedAt)}</p>
                    {a.resultsVisible && (
                      <p>
                        {a.score} / {a.maxScore} б.
                      </p>
                    )}
                    <button disabled={busy} onClick={() => setSelected(a.id)}>
                      Открыть попытку {a.number}
                    </button>
                  </article>
                ))}
              </div>
            </>
          )}
          {selected && (
            <QuizAttemptPanel
              key={selected}
              workspace={workspace}
              quiz={quiz}
              attemptId={selected}
              onActivity={setActivity}
              onChanged={() => {
                setEpoch((v) => v + 1);
                onChanged();
              }}
              onClose={() => setSelected("")}
            />
          )}
        </>
      )}
    </section>
  );
}

export default function QuizzesPanel({ workspace }: { workspace: string }) {
  const [section, setSection] = useState<"list" | "bank" | "builder">("list"),
    [editing, setEditing] = useState<{
      quiz?: Quiz;
      copy: boolean;
      instance: number;
    }>({
      copy: false,
      instance: 0,
    });
  const [items, setItems] = useState<Item[]>([]),
    [staff, setStaff] = useState(false),
    [selected, setSelected] = useState<{
      id: string;
      studentId?: string;
    } | null>(null);
  const [search, setSearch] = useState(""),
    [applied, setApplied] = useState(""),
    [history, setHistory] = useState(false),
    [cursor, setCursor] = useState(""),
    [next, setNext] = useState<string | null>(null),
    [epoch, setEpoch] = useState(0);
  const [busy, setBusy] = useState(true),
    [error, setError] = useState(""),
    [activity, setActivity] = useState<Activity>({ dirty: false, busy: false });
  useEffect(() => {
    const leave = (e: Event) => {
      if (
        activity.busy ||
        (activity.dirty &&
          !window.confirm(
            "В тесте есть несохранённые изменения. Уйти без сохранения?",
          ))
      )
        e.preventDefault();
    };
    const unload = (e: BeforeUnloadEvent) => {
      if (activity.dirty || activity.busy) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("atlas-homework-leave", leave);
    window.addEventListener("beforeunload", unload);
    return () => {
      window.removeEventListener("atlas-homework-leave", leave);
      window.removeEventListener("beforeunload", unload);
    };
  }, [activity]);
  useEffect(() => {
    let live = true;
    setBusy(true);
    setError("");
    api(
      `journal/quizzes?${new URLSearchParams({ search: applied, history: String(history), ...(cursor ? { cursor } : {}) })}`,
      workspace,
    )
      .then((r) => {
        if (live) {
          setItems(r.items);
          setNext(r.nextCursor);
          setStaff(r.staff);
        }
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
  }, [workspace, applied, history, cursor, epoch]);
  function refresh() {
    setCursor("");
    setEpoch((v) => v + 1);
  }
  function edit(quiz?: Quiz, copy = false) {
    if (!canLeaveHomework()) return;
    setEditing((previous) => ({ quiz, copy, instance: previous.instance + 1 }));
    setSection("builder");
    setSelected(null);
  }
  function switchTo(value: "list" | "bank") {
    if (!canLeaveHomework()) return;
    setSelected(null);
    setSection(value);
  }
  return (
    <section className="quizzes">
      <div className="dated-toolbar">
        <button
          aria-pressed={section === "list"}
          disabled={activity.busy}
          onClick={() => switchTo("list")}
        >
          Назначенные тесты
        </button>
        {staff && (
          <>
            <button
              aria-pressed={section === "bank"}
              disabled={activity.busy}
              onClick={() => switchTo("bank")}
            >
              Банк вопросов
            </button>
            <button
              className="primary"
              disabled={activity.busy}
              onClick={() => edit()}
            >
              Создать тест
            </button>
          </>
        )}
      </div>
      {section === "bank" ? (
        <QuizBankPanel workspace={workspace} onActivity={setActivity} />
      ) : section === "builder" ? (
        <QuizBuilder
          key={editing.instance}
          workspace={workspace}
          initial={editing.quiz}
          copy={editing.copy}
          onActivity={setActivity}
          onSaved={(quiz) => {
            setSection("list");
            setSelected({ id: quiz.id });
            refresh();
          }}
          onClose={() => {
            setSection("list");
            refresh();
          }}
        />
      ) : selected ? (
        <Detail
          key={selected.id + selected.studentId}
          workspace={workspace}
          id={selected.id}
          studentId={selected.studentId}
          onActivity={setActivity}
          onClose={() => {
            setSelected(null);
            refresh();
          }}
          onEdit={edit}
          onChanged={refresh}
        />
      ) : (
        <>
          <p className="info-line">
            Тесты по вашим урокам и курсам. Таймер работает по серверному
            времени, а результаты и разбор открывает учитель после проверки.
          </p>
          <form
            className="dated-toolbar"
            onSubmit={(e) => {
              e.preventDefault();
              setApplied(search);
              setCursor("");
            }}
          >
            <label className="field">
              <span>Найти тест по названию, предмету или группе</span>
              <input
                value={search}
                maxLength={120}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <button disabled={busy}>Найти тесты</button>
            <label className="check-line">
              <input
                type="checkbox"
                checked={history}
                onChange={(e) => {
                  setHistory(e.target.checked);
                  setCursor("");
                }}
              />
              Включить прежние и отозванные назначения
            </label>
            <button type="button" disabled={busy} onClick={refresh}>
              Обновить тесты
            </button>
          </form>
          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {busy && <p role="status">Загружаем тесты…</p>}
          {!busy && !items.length && (
            <p>
              На этой странице тестов нет.
              {next ? " Продолжите просмотр следующей страницы." : ""}
            </p>
          )}
          <div className="quiz-cards">
            {items.map((item) => (
              <article className="quiz-card" key={item.id + item.studentId}>
                <h2>{item.title}</h2>
                <p>
                  {item.subject} · {item.group}
                  {item.course ? " · весь курс" : ""}
                  {item.student ? ` · ${item.student}` : ""}
                </p>
                <strong>
                  {quizStatuses[item.status]}
                  {item.released ? " · разбор открыт" : ""}
                  {item.readOnly ? " · только просмотр" : ""}
                </strong>
                <p>
                  С {schoolDate(item.opensAt)} до {schoolDate(item.closesAt)}
                </p>
                <button
                  disabled={busy}
                  onClick={() =>
                    setSelected({
                      id: item.id,
                      studentId: item.studentId || undefined,
                    })
                  }
                >
                  Открыть тест
                </button>
              </article>
            ))}
          </div>
          <div className="dated-toolbar">
            {cursor && (
              <button disabled={busy} onClick={() => setCursor("")}>
                В начало тестов
              </button>
            )}
            {next && (
              <button disabled={busy} onClick={() => setCursor(next)}>
                Следующие тесты
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

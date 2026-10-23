import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import QuizFiles from "./QuizFiles";
import HomeworkGradeForm, { type ReviewGrade } from "./HomeworkGradeForm";
import {
  quizStatuses,
  schoolDate,
  type Activity,
  type Attempt,
  type Quiz,
} from "./quiz";

export default function QuizAttemptPanel({
  workspace,
  quiz,
  attemptId,
  onActivity,
  onChanged,
  onClose,
}: {
  workspace: string;
  quiz: Quiz;
  attemptId: string;
  onActivity: (activity: Activity) => void;
  onChanged: () => void;
  onClose: () => void;
}) {
  const endpoint = `journal/quizzes/${encodeURIComponent(quiz.id)}/attempts/${encodeURIComponent(attemptId)}`;
  const [work, setWork] = useState<Attempt | null>(null),
    [answers, setAnswers] = useState<NonNullable<Attempt["answers"]>>({});
  const [busy, setBusy] = useState(false),
    [dirty, setDirty] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [conflict, setConflict] = useState(false);
  const [, setTick] = useState(0),
    [edits, setEdits] = useState<
      Record<string, { score: string; comment: string }>
    >({}),
    [comment, setComment] = useState(""),
    [reason, setReason] = useState("");
  const [reviewDirty, setReviewDirty] = useState(false),
    [gradeDirty, setGradeDirty] = useState(false),
    [gradeOpen, setGradeOpen] = useState(false),
    [grade, setGrade] = useState<ReviewGrade | null>(null);
  const [history, setHistory] = useState<any[]>([]),
    [historyOpen, setHistoryOpen] = useState(false),
    [next, setNext] = useState<number | null>(null);
  const alive = useRef(true),
    operation = useRef(false),
    current = useRef<Attempt | null>(null),
    draft = useRef(answers),
    sequence = useRef(0),
    saved = useRef(0),
    blocked = useRef(false);
  const clock = useRef({ server: Date.now(), client: performance.now() }),
    lastSave = useRef(0),
    saveRef = useRef<(submit?: boolean) => Promise<void>>(async () => {});
  const unsaved = dirty || reviewDirty || gradeDirty;
  function receive(value: Attempt) {
    current.current = value;
    setWork(value);
    draft.current = value.answers || {};
    setAnswers(draft.current);
    sequence.current = 0;
    saved.current = 0;
    setDirty(false);
    setEdits({});
    setComment(value.comment || "");
    setReason("");
    setReviewDirty(false);
    setGradeDirty(false);
    setGradeOpen(false);
    setGrade(null);
    setHistory([]);
    setHistoryOpen(false);
    setNext(null);
    setConflict(false);
    blocked.current = false;
    if (value.serverNow)
      clock.current = {
        server: Date.parse(value.serverNow),
        client: performance.now(),
      };
  }
  useEffect(() => {
    alive.current = true;
    let live = true;
    api(endpoint, workspace)
      .then((r) => {
        if (live) receive(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
      alive.current = false;
    };
  }, [workspace, endpoint]);
  useEffect(() => {
    onActivity({ dirty: unsaved, busy });
    return () => onActivity({ dirty: false, busy: false });
  }, [unsaved, busy, onActivity]);
  useEffect(() => {
    if (!unsaved && !busy) return;
    const listener = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", listener);
    return () => window.removeEventListener("beforeunload", listener);
  }, [unsaved, busy]);
  async function saveAnswer(submit = false) {
    const value = current.current;
    if (
      operation.current ||
      blocked.current ||
      !value?.canAnswer ||
      value.status !== "active" ||
      !navigator.onLine
    )
      return;
    if (!submit && sequence.current === saved.current) return;
    operation.current = true;
    setBusy(true);
    setError("");
    lastSave.current = Date.now();
    const at = sequence.current;
    try {
      const result: Attempt = await api(endpoint, workspace, "PUT", {
        version: value.version,
        answers: structuredClone(draft.current),
        submit,
      });
      if (!alive.current) return;
      if (result.status !== "active") {
        receive(result);
        setNotice(
          result.expired
            ? "Время истекло. Отправлены только ответы, которые успели сохраниться на сервере."
            : "Ответы отправлены. Разбор откроет учитель.",
        );
        onChanged();
      } else {
        current.current = { ...result, canAnswer: value.canAnswer };
        setWork(current.current);
        saved.current = at;
        setDirty(sequence.current !== at);
        setNotice(
          sequence.current === at
            ? "Ответы сохранены на сервере."
            : "Сохраняем следующие изменения…",
        );
      }
    } catch (e: any) {
      if (!alive.current) return;
      setError(e.message);
      if ([401, 403, 409].includes(e.status)) {
        blocked.current = true;
        setConflict(true);
        if (e.status === 409) {
          try {
            const latest: Attempt = await api(endpoint, workspace);
            if (alive.current && latest.status !== "active") {
              receive(latest);
              setNotice(
                "Попытка завершена. Показаны последние ответы, сохранённые на сервере.",
              );
              onChanged();
            }
          } catch {
            /* Keep local answers while access or connection is unavailable. */
          }
        }
      }
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  saveRef.current = saveAnswer;
  useEffect(() => {
    const timer = window.setInterval(() => {
      setTick((v) => v + 1);
      const value = current.current;
      if (!value || value.status !== "active" || !value.canAnswer) return;
      const remaining =
        Date.parse(value.deadline) -
        (clock.current.server + performance.now() - clock.current.client);
      if (Date.now() - lastSave.current >= 5000)
        void saveRef.current(remaining <= 0);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const timer = window.setTimeout(() => void saveRef.current(), 1500);
    return () => window.clearTimeout(timer);
  }, [answers, dirty]);
  async function run(fn: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function review() {
    if (!work) return;
    const marks = Object.fromEntries(
      Object.entries(edits)
        .filter(([, v]) => v.score !== "")
        .map(([id, v]) => [id, { score: Number(v.score), comment: v.comment }]),
    );
    const result = await api(endpoint + "/review", workspace, "POST", {
      version: work.version,
      marks,
      comment,
      reason,
    });
    if (alive.current) {
      receive(result);
      onChanged();
    }
  }
  async function publishGrade() {
    if (!work || !grade) return;
    const result = await api(endpoint + "/grade", workspace, "POST", {
      version: work.version,
      grading: grade,
    });
    if (alive.current) {
      receive(result);
      onChanged();
    }
  }
  async function loadHistory(before?: number) {
    const result = await api(
      endpoint + `/history${before ? `?before=${before}` : ""}`,
      workspace,
    );
    if (alive.current) {
      setHistory((old) => (before ? [...old, ...result.items] : result.items));
      setNext(result.nextBefore);
      setHistoryOpen(true);
    }
  }
  function editAnswer(id: string, value: { selected: string[]; text: string }) {
    draft.current = { ...draft.current, [id]: value };
    setAnswers(draft.current);
    sequence.current += 1;
    setDirty(true);
  }
  function editMark(
    id: string,
    value: Partial<{ score: string; comment: string }>,
  ) {
    const old = edits[id] || {
      score: work?.marks?.[id]?.score.toString() || "",
      comment: work?.marks?.[id]?.comment || "",
    };
    setEdits((items) => ({ ...items, [id]: { ...old, ...value } }));
    setReviewDirty(true);
  }
  if (!work)
    return (
      <section>
        {error ? (
          <p className="error-banner" role="alert">
            {error}
          </p>
        ) : (
          <p role="status">Загружаем попытку…</p>
        )}
        <button onClick={onClose}>Закрыть попытку</button>
      </section>
    );
  const active = work.status === "active",
    canAnswer = active && work.canAnswer && !conflict;
  const remaining = Math.max(
    0,
    Math.ceil(
      (Date.parse(work.deadline) -
        (clock.current.server + performance.now() - clock.current.client)) /
        1000,
    ),
  );
  const canReview = work.canReview && !active && !!work.questions;
  const markComplete = work.questions?.every(
    (q) =>
      q.kind !== "essay" ||
      (edits[q.id]?.score !== undefined && edits[q.id].score !== "") ||
      work.marks?.[q.id],
  );
  return (
    <section className="quiz-attempt" aria-label="Попытка теста">
      <div className="dated-toolbar">
        <h3>
          Попытка {work.number} · {quizStatuses[work.status]}
        </h3>
        <button
          disabled={busy}
          onClick={() => {
            if (
              !unsaved ||
              window.confirm(
                "Закрыть попытку с несохранёнными изменениями? Таймер продолжит идти.",
              )
            )
              onClose();
          }}
        >
          Закрыть попытку
        </button>
        <button
          disabled={busy}
          onClick={() => {
            if (
              !unsaved ||
              window.confirm(
                "Загрузить сохранённый вариант и заменить несохранённые изменения?",
              )
            )
              void run(async () => {
                const r = await api(endpoint, workspace);
                if (alive.current) receive(r);
              });
          }}
        >
          Загрузить сохранённую попытку
        </button>
      </div>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="info-line">
          {notice}
        </p>
      )}
      {conflict && (
        <p className="error-banner">
          Автосохранение остановлено. Ваш текст остаётся в этом окне. После
          проверки доступа загрузите сохранённую попытку; изменения из другого
          окна автоматически не перезаписываются.
        </p>
      )}
      {active && (
        <div className="quiz-timer">
          <strong>
            Осталось {Math.floor(remaining / 60)}:
            {String(remaining % 60).padStart(2, "0")}
          </strong>
          <span>Окончание: {schoolDate(work.deadline)} · время школы</span>
          <small>
            Обновление страницы и выход не останавливают таймер. После его
            окончания учитываются последние ответы, сохранённые на сервере.
          </small>
          {dirty && (
            <strong>Есть ответы, ещё не сохранённые на сервере.</strong>
          )}
        </div>
      )}
      {!work.questions && (
        <p>
          Ученик выполняет тест. Ответы во время попытки доступны только ему;
          обновите страницу после отправки.
        </p>
      )}
      {!active && !work.resultsVisible && (
        <p className="info-line">
          Ответ отправлен. Баллы, правильные ответы и объяснения появятся после
          открытия разбора учителем.
        </p>
      )}
      {work.resultsVisible && (
        <p className="quiz-result">
          <strong>
            {work.score === null
              ? "Нужна ручная проверка"
              : `${work.score} / ${work.maxScore} баллов`}
          </strong>
          {work.comment && <span className="journal-text">{work.comment}</span>}
          {work.correctionReason && (
            <span>Причина исправления: {work.correctionReason}</span>
          )}
        </p>
      )}
      {work.grading && (
        <div className="info-line">
          <strong>
            В журнале: {work.grading.assessment.name} ·{" "}
            {work.grading.grade.score ?? work.grading.grade.label}
          </strong>
          <p>
            {work.grading.grader} · {schoolDate(work.grading.gradedAt)}
          </p>
          {work.grading.stale && (
            <p>
              Баллы теста изменены после переноса. Оценку в журнале нужно
              проверить отдельно.
            </p>
          )}
        </div>
      )}
      <ol className="quiz-question-list">
        {work.questions?.map((q) => {
          const answer = answers[q.id] || { selected: [], text: "" };
          const solution = work.solutions?.find((s) => s.id === q.id)?.question;
          const mark = work.marks?.[q.id];
          const field = edits[q.id] || {
            score: mark?.score.toString() || "",
            comment: mark?.comment || "",
          };
          return (
            <li key={q.id}>
              <h4 className="journal-text">{q.text}</h4>
              <QuizFiles
                files={q.attachments}
                workspace={workspace}
                quizId={work.quizId}
                attemptId={work.id}
                questionId={q.id}
              />
              <small>Максимум: {q.points} б.</small>
              {q.kind === "single" || q.kind === "multiple" ? (
                <fieldset disabled={!canAnswer} className="quiz-options">
                  <legend>Ваш ответ</legend>
                  {q.options.map((o) => (
                    <label className="check-line" key={o.id}>
                      <input
                        type={q.kind === "single" ? "radio" : "checkbox"}
                        name={`answer-${q.id}`}
                        checked={answer.selected.includes(o.id)}
                        onChange={(e) =>
                          editAnswer(q.id, {
                            text: "",
                            selected:
                              q.kind === "single"
                                ? [o.id]
                                : e.target.checked
                                  ? [...answer.selected, o.id]
                                  : answer.selected.filter((id) => id !== o.id),
                          })
                        }
                      />
                      {o.text}
                    </label>
                  ))}
                  {canAnswer && (
                    <button
                      type="button"
                      onClick={() =>
                        editAnswer(q.id, { selected: [], text: "" })
                      }
                    >
                      Снять ответ
                    </button>
                  )}
                </fieldset>
              ) : (
                <label className="field">
                  <span>
                    {q.kind === "number" ? "Ваш числовой ответ" : "Ваш ответ"}
                  </span>
                  <textarea
                    rows={q.kind === "essay" ? 6 : 2}
                    inputMode={q.kind === "number" ? "decimal" : undefined}
                    maxLength={10000}
                    readOnly={!canAnswer}
                    value={answer.text}
                    onChange={(e) =>
                      editAnswer(q.id, { selected: [], text: e.target.value })
                    }
                  />
                </label>
              )}
              {mark && (
                <p>
                  {mark.score} / {q.points} ·{" "}
                  {mark.automatic
                    ? "Автоматическая проверка"
                    : "Проверка учителя"}
                  {mark.comment ? ` · ${mark.comment}` : ""}
                </p>
              )}
              {solution && (
                <details>
                  <summary>Правильный ответ и объяснение</summary>
                  <p>
                    {solution.kind === "essay"
                      ? "Оценивает учитель."
                      : solution.kind === "number"
                        ? `${solution.number} ± ${solution.tolerance}`
                        : solution.kind === "short"
                          ? solution.accepted.join(" / ")
                          : solution.options
                              .filter((o) => solution.correctIds.includes(o.id))
                              .map((o) => o.text)
                              .join("; ")}
                  </p>
                  <p className="journal-text">{solution.explanation}</p>
                  <QuizFiles
                    files={solution.solutionAttachments}
                    workspace={workspace}
                    quizId={work.quizId}
                    attemptId={work.id}
                    questionId={q.id}
                    solution
                  />
                </details>
              )}
              {canReview && (
                <fieldset className="quiz-grid" disabled={busy || gradeDirty}>
                  <label className="field">
                    <span>
                      Баллы за вопрос {work.questions!.indexOf(q) + 1}
                    </span>
                    <input
                      type="number"
                      min={0}
                      max={q.points}
                      step="any"
                      value={field.score}
                      onChange={(e) =>
                        editMark(q.id, { score: e.target.value })
                      }
                    />
                  </label>
                  <label className="field">
                    <span>
                      Комментарий к вопросу {work.questions!.indexOf(q) + 1}
                    </span>
                    <textarea
                      rows={2}
                      maxLength={2000}
                      value={field.comment}
                      onChange={(e) =>
                        editMark(q.id, { comment: e.target.value })
                      }
                    />
                  </label>
                </fieldset>
              )}
            </li>
          );
        })}
      </ol>
      {canAnswer && (
        <div className="dated-toolbar">
          <button disabled={busy || !dirty} onClick={() => void saveAnswer()}>
            Сохранить ответы сейчас
          </button>
          <button
            className="primary"
            disabled={busy}
            onClick={() => {
              if (window.confirm("Завершить эту попытку и отправить ответы?"))
                void saveAnswer(true);
            }}
          >
            Завершить попытку
          </button>
        </div>
      )}
      {canReview && (
        <fieldset className="access-fields" disabled={busy || gradeDirty}>
          <label className="field">
            <span>Общий комментарий к тесту</span>
            <textarea
              rows={3}
              maxLength={4000}
              value={comment}
              onChange={(e) => {
                setComment(e.target.value);
                setReviewDirty(true);
              }}
            />
          </label>
          {quiz.released && (
            <label className="field">
              <span>Причина исправления опубликованного результата</span>
              <textarea
                rows={2}
                required
                maxLength={1000}
                value={reason}
                onChange={(e) => {
                  setReason(e.target.value);
                  setReviewDirty(true);
                }}
              />
            </label>
          )}
          <p className="muted">
            Комментарий к вопросу нужен, только если баллы отличаются от
            автоматических. Исправление результата сохраняется в истории и само
            по себе не меняет оценку журнала.
          </p>
          <button
            className="primary"
            disabled={!markComplete || (quiz.released && !reason.trim())}
            onClick={() => void run(review)}
          >
            Сохранить проверку теста
          </button>
        </fieldset>
      )}
      {canReview && quiz.released && work.status === "reviewed" && (
        <section className="quiz-grade">
          <button
            disabled={busy || reviewDirty || gradeDirty}
            onClick={() => setGradeOpen((v) => !v)}
          >
            {gradeOpen
              ? "Скрыть перенос оценки"
              : "Перенести результат в журнал"}
          </button>
          {gradeOpen && (
            <fieldset className="access-fields" disabled={busy || reviewDirty}>
              <p>
                Результат теста: {work.score} из {work.maxScore}. Выберите
                работу и укажите оценку по её шкале.
              </p>
              <HomeworkGradeForm
                workspace={workspace}
                lessonKey={quiz.lessonKey}
                studentId={work.studentId}
                endpoint={endpoint + "/grading"}
                defaultName={quiz.title}
                scoreLabel="Оценка за тест"
                onChange={setGrade}
                onEdited={() => setGradeDirty(true)}
              />
              <button
                className="primary"
                disabled={!grade}
                onClick={() => void run(publishGrade)}
              >
                Опубликовать оценку теста
              </button>
              <button
                onClick={() => {
                  if (
                    !gradeDirty ||
                    window.confirm("Закрыть несохранённую оценку?")
                  ) {
                    setGradeOpen(false);
                    setGradeDirty(false);
                    setGrade(null);
                  }
                }}
              >
                Отменить перенос оценки
              </button>
            </fieldset>
          )}
        </section>
      )}
      {(reviewDirty || gradeDirty) && (
        <p className="muted">
          Сохраните текущую проверку или перенос оценки перед следующим
          действием.
        </p>
      )}
      {!active && (
        <button disabled={busy} onClick={() => void run(() => loadHistory())}>
          История этой попытки
        </button>
      )}
      {historyOpen && (
        <div className="homework-history">
          {history.map((h) => (
            <details key={h.id}>
              <summary>
                {(
                  {
                    "quiz.started": "Начало",
                    "quiz.submitted": "Отправка",
                    "quiz.reviewed": "Проверка",
                    "quiz.graded": "Оценка в журнале",
                  } as Record<string, string>
                )[h.action] || h.action}{" "}
                · {schoolDate(h.created)} · {h.actor}
              </summary>
              {h.attempt?.resultsVisible && (
                <>
                  <p>
                    Баллы: {h.attempt.score ?? "ожидают проверки"} /{" "}
                    {h.attempt.maxScore}
                  </p>
                  <p className="journal-text">{h.attempt.comment}</p>
                  <p>{h.attempt.correctionReason}</p>
                </>
              )}
            </details>
          ))}
          {next && (
            <button
              disabled={busy}
              onClick={() => void run(() => loadHistory(next))}
            >
              Более ранние записи попытки
            </button>
          )}
        </div>
      )}
    </section>
  );
}

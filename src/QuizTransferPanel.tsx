import { useEffect, useRef, useState } from "react";
import "./QuizTransfer.css";
import { api } from "./api";
import { useAssessmentCategories } from "./assessmentCategories";
import type { Activity, Quiz } from "./quiz";
import {
  NEW_WORK,
  canSend,
  closedLessons,
  countsLine,
  formComplete,
  lessonOption,
  markText,
  outcomeLabels,
  overwriteIds,
  pickLesson,
  targetBody,
  transferBody,
  type Preview,
  type TransferForm,
  type TransferLesson,
  type TransferResult,
  type TransferSummary,
} from "./quizTransfer";

/** Moves the results of a test into one lesson of its course: lesson and work are chosen, the preview shows every pupil, and one button writes them. */
export default function QuizTransferPanel({
  workspace,
  quiz,
  summary,
  onActivity,
  onChanged,
  onClose,
}: {
  workspace: string;
  quiz: Quiz;
  summary: TransferSummary;
  onActivity: (value: Activity) => void;
  onChanged: () => void;
  onClose: () => void;
}) {
  const id = quiz.id,
    revision = quiz.revision ?? 0,
    endpoint = `journal/quizzes/${encodeURIComponent(id)}/transfer`;
  const categories = useAssessmentCategories(workspace);
  const [form, setForm] = useState<TransferForm>(() => ({
    day: summary.defaultDay,
    lessonKey: summary.defaultLessonKey,
    workId: NEW_WORK,
    name: quiz.title.slice(0, 100),
    category: summary.defaultCategory,
    weight: 1,
    maxScore: summary.quizMax,
    pick: "last",
    publish: false,
  }));
  const [lessons, setLessons] = useState<TransferLesson[] | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [chosen, setChosen] = useState<Set<string>>(new Set()),
    [touched, setTouched] = useState(false),
    [loading, setLoading] = useState(false),
    [previewing, setPreviewing] = useState(false),
    [sending, setSending] = useState(false),
    [error, setError] = useState(""),
    [done, setDone] = useState<TransferResult | null>(null),
    [epoch, setEpoch] = useState(0);
  const alive = useRef(true),
    sent = useRef<{ signature: string; id: string } | null>(null);
  const busy = loading || previewing || sending;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onActivity({ dirty: touched || chosen.size > 0, busy });
    return () => onActivity({ dirty: false, busy: false });
  }, [touched, chosen, busy, onActivity]);
  function change(value: Partial<TransferForm>) {
    setForm((old) => ({ ...old, ...value }));
    setTouched(true);
    setDone(null);
    setError("");
  }
  // The lessons of the course on that day; the chosen one is kept while it is still there.
  useEffect(() => {
    if (!form.day) {
      setLessons([]);
      return;
    }
    let live = true;
    setLoading(true);
    api(
      `${endpoint}/lessons?${new URLSearchParams({ day: form.day })}`,
      workspace,
    )
      .then((r: { lessons: TransferLesson[] }) => {
        if (!live) return;
        setLessons(r.lessons);
        setForm((old) => {
          const pick = pickLesson(r.lessons, old.lessonKey);
          const works = pick?.works.map((w) => w.id) ?? [];
          return {
            ...old,
            lessonKey: pick?.lessonKey ?? "",
            workId:
              old.workId === NEW_WORK || works.includes(old.workId)
                ? old.workId
                : NEW_WORK,
          };
        });
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [workspace, endpoint, form.day, epoch]);
  const lesson = lessons?.find((l) => l.lessonKey === form.lessonKey);
  // Every complete form for a lesson that takes marks is previewed; the last answer wins.
  const signature =
    lesson?.editable && formComplete(form)
      ? JSON.stringify(targetBody(form, revision))
      : "";
  useEffect(() => {
    setPreview(null);
    setChosen(new Set());
    setPreviewing(!!signature);
    if (!signature) return;
    let live = true;
    const timer = setTimeout(() => {
      api(`${endpoint}/preview`, workspace, "POST", JSON.parse(signature))
        .then((r: Preview) => {
          if (live) setPreview(r);
        })
        .catch((e) => {
          if (live) setError(e.message);
        })
        .finally(() => {
          if (live) setPreviewing(false);
        });
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [workspace, endpoint, signature, epoch]);
  async function send() {
    if (busy || !preview || !canSend(preview, form.publish, chosen)) return;
    const body = transferBody(preview, form, "", chosen, revision);
    // A retry of the same confirmed body keeps its request id, so a lost answer never writes twice.
    const key = JSON.stringify(body);
    if (sent.current?.signature !== key)
      sent.current = { signature: key, id: crypto.randomUUID() };
    setSending(true);
    setError("");
    try {
      const result: TransferResult = await api(endpoint, workspace, "POST", {
        ...body,
        requestId: sent.current.id,
      });
      if (!alive.current) return;
      sent.current = null;
      setDone(result);
      setTouched(false);
      setEpoch((v) => v + 1);
      onChanged();
    } catch (e: any) {
      if (!alive.current) return;
      setError(e.message);
      // The server answered, so nothing was written: show what the journal holds now.
      if (e.status) {
        sent.current = null;
        setEpoch((v) => v + 1);
      }
    } finally {
      if (alive.current) setSending(false);
    }
  }
  const works = lesson?.works.filter((w) => w.kind === "points") ?? [],
    closed = closedLessons(lessons ?? []),
    conflicts = preview?.rows.filter((r) => r.outcome === "conflict") ?? [],
    todo =
      (preview?.counts.write ?? 0) +
      (preview?.counts.update ?? 0) +
      overwriteIds(preview?.rows ?? [], chosen).length,
    elsewhere = summary.runs.find((r) => r.lessonKey !== form.lessonKey);
  return (
    <section
      className="quiz-transfer"
      aria-label="Перенос результатов в журнал"
    >
      <h3>Перенос результатов в журнал</h3>
      <p className="muted">
        Результат каждого ученика станет оценкой за работу в выбранном уроке
        курса. Черновик семья не видит: оценки появятся у неё после публикации.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {done && (
        <p role="status" className="info-line">
          {done.status === "saved"
            ? `Готово: ${countsLine(done.counts)}${done.published ? " · опубликовано семье" : " · сохранено в черновике"}.`
            : "В журнале уже есть эти результаты, ничего не изменилось."}
        </p>
      )}
      <fieldset className="access-fields" disabled={sending}>
        <div className="quiz-grid">
          <label className="field">
            <span>Дата урока</span>
            <input
              type="date"
              required
              value={form.day}
              onChange={(e) => change({ day: e.target.value, lessonKey: "" })}
            />
          </label>
          <label className="field">
            <span>Урок курса</span>
            <select
              value={form.lessonKey}
              onChange={(e) =>
                change({ lessonKey: e.target.value, workId: NEW_WORK })
              }
            >
              {!lessons?.length && <option value="">Уроков нет</option>}
              {lessons?.map((l) => {
                const o = lessonOption(l);
                return (
                  <option key={o.value} value={o.value} disabled={o.disabled}>
                    {o.label}
                  </option>
                );
              })}
            </select>
          </label>
        </div>
        {lessons && !lessons.length && !loading && (
          <p>В этот день у курса нет уроков. Выберите другую дату.</p>
        )}
        {lesson && !lesson.editable && (
          <p className="error-banner" role="alert">
            В этот день ни один урок курса не принимает оценки.
          </p>
        )}
        {closed.length > 0 && (
          <ul className="quiz-transfer-closed muted">
            {closed.map((c) => (
              <li key={c.key}>
                {c.time} · недоступен: {c.reason}
              </li>
            ))}
          </ul>
        )}
        <label className="field">
          <span>Работа в журнале</span>
          <select
            value={form.workId}
            onChange={(e) => change({ workId: e.target.value })}
          >
            <option value={NEW_WORK}>Новая работа</option>
            {works.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name} · {w.maxScore} б.
              </option>
            ))}
          </select>
        </label>
        {form.workId === NEW_WORK && (
          <div className="quiz-grid">
            <label className="field">
              <span>Название работы</span>
              <input
                value={form.name}
                maxLength={100}
                onChange={(e) => change({ name: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Категория</span>
              <select
                value={form.category}
                onChange={(e) => change({ category: e.target.value })}
              >
                {(categories?.categories ?? [])
                  .filter((c) => c.active || c.id === form.category)
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
              </select>
            </label>
            <label className="field">
              <span>Вес работы</span>
              <input
                type="number"
                min={0}
                max={100}
                step="any"
                value={form.weight}
                onChange={(e) => change({ weight: Number(e.target.value) })}
              />
            </label>
            <label className="field">
              <span>Максимум баллов</span>
              <input
                type="number"
                min={0.01}
                max={10000}
                step="any"
                value={form.maxScore}
                onChange={(e) => change({ maxScore: Number(e.target.value) })}
              />
            </label>
          </div>
        )}
        <label className="field">
          <span>Если попыток несколько</span>
          <select
            value={form.pick}
            onChange={(e) =>
              change({ pick: e.target.value as TransferForm["pick"] })
            }
          >
            <option value="last">Берётся последняя</option>
            <option value="best">Берётся лучшая</option>
          </select>
        </label>
        <label className="check-line">
          <input
            type="checkbox"
            checked={form.publish && quiz.released}
            disabled={!quiz.released}
            onChange={(e) => change({ publish: e.target.checked })}
          />
          Сразу опубликовать семье
        </label>
        {!quiz.released && (
          <p className="muted">
            Публикация станет доступна, когда вы откроете разбор теста. Пока
            можно перенести результаты в черновик журнала.
          </p>
        )}
      </fieldset>
      {elsewhere && (
        <p className="info-line">
          Результаты этого теста уже переносились в урок {elsewhere.day}
          {elsewhere.work ? ` (${elsewhere.work})` : ""}. Проверьте, что не
          переносите их второй раз.
        </p>
      )}
      {previewing && <p role="status">Считаем перенос…</p>}
      {preview && !preview.allowed && (
        <p className="error-banner" role="alert">
          {preview.reason}
        </p>
      )}
      {preview?.allowed && (
        <>
          {preview.workExists && preview.work && (
            <p className="info-line">
              Работа «{preview.work.name}» уже есть в уроке: оценки пойдут в
              неё, максимум {preview.work.maxScore} б.
            </p>
          )}
          {form.publish && preview.otherPending && (
            <p className="info-line">
              При публикации семья увидит и другие неопубликованные работы и
              оценки этого урока.
            </p>
          )}
          {conflicts.length > 0 && (
            <div className="dated-toolbar">
              <p className="muted">
                У {conflicts.length} учеников в журнале другая оценка. Она не
                будет заменена, пока вы не отметите «Заменить».
              </p>
              <button
                type="button"
                onClick={() =>
                  setChosen(new Set(conflicts.map((r) => r.studentId)))
                }
              >
                Заменить все
              </button>
            </div>
          )}
          <ul className="quiz-transfer-rows">
            {preview.rows.map((r) => (
              <li key={r.studentId} data-outcome={r.outcome}>
                <strong>
                  {r.name}
                  {!r.enrolled ? " · прежний состав" : ""}
                </strong>
                {r.outcome === "skip" ? (
                  <span>{r.reason}</span>
                ) : (
                  <>
                    <span>
                      Попытка {r.attemptNumber}: {r.score}/{r.maxScore} →{" "}
                      <b>{r.value}</b>
                      {r.scaled && preview.work
                        ? ` из ${preview.work.maxScore}`
                        : ""}
                    </span>
                    <span className="muted">
                      В журнале: черновик {markText(r.draft)} · опубликовано{" "}
                      {markText(r.published)}
                    </span>
                    <span>{outcomeLabels[r.outcome]}</span>
                  </>
                )}
                {r.outcome === "conflict" && (
                  <label className="check-line">
                    <input
                      type="checkbox"
                      checked={chosen.has(r.studentId)}
                      onChange={(e) =>
                        setChosen((old) => {
                          const next = new Set(old);
                          if (e.target.checked) next.add(r.studentId);
                          else next.delete(r.studentId);
                          return next;
                        })
                      }
                    />
                    Заменить
                  </label>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      <div className="dated-toolbar">
        <button
          className="primary"
          disabled={busy || !canSend(preview, form.publish, chosen)}
          onClick={() => void send()}
        >
          Перенести
          {todo ? ` · оценок: ${todo}` : ""}
          {!todo && form.publish && preview?.otherPending
            ? " · опубликовать"
            : ""}
        </button>
        <button type="button" disabled={sending} onClick={onClose}>
          Закрыть перенос
        </button>
      </div>
    </section>
  );
}

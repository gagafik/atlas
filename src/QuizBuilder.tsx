import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import QuizBankPanel from "./QuizBankPanel";
import {
  questionKinds,
  type Activity,
  type BankItem,
  type Quiz,
  type QuizDefinition,
} from "./quiz";
import type { JournalEntry } from "./journal";
import { useAssessmentCategories } from "./assessmentCategories";

const schoolInput = (value: string) =>
  new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Qyzylorda",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  })
    .format(new Date(value))
    .replace(" ", "T");

export default function QuizBuilder({
  workspace,
  initial,
  copy = false,
  onActivity,
  onSaved,
  onClose,
}: {
  workspace: string;
  initial?: Quiz;
  copy?: boolean;
  onActivity: (value: Activity) => void;
  onSaved: (quiz: Quiz) => void;
  onClose: () => void;
}) {
  const id = useRef(initial && !copy ? initial.id : crypto.randomUUID());
  const [definition, setDefinition] = useState<QuizDefinition>(() =>
    initial?.document
      ? structuredClone(initial.document.definition)
      : {
          title: "",
          instructions: "",
          variants: [
            { id: crypto.randomUUID(), name: "Вариант 1", questions: [] },
          ],
          opensAt: new Date().toISOString(),
          closesAt: new Date(Date.now() + 86400000).toISOString(),
          timeLimitMinutes: 30,
          maxAttempts: 1,
          shuffleQuestions: true,
          shuffleOptions: true,
          journalCategory: "",
        },
  );
  const categories = useAssessmentCategories(workspace);
  const [opens, setOpens] = useState(() => schoolInput(definition.opensAt)),
    [closes, setCloses] = useState(() => schoolInput(definition.closesAt));
  const [day, setDay] = useState(
      initial?.lessonDate || schoolInput(new Date().toISOString()).slice(0, 10),
    ),
    [lessonKey, setLessonKey] = useState(initial?.lessonKey || "");
  const [lessons, setLessons] = useState<
      {
        lessonKey: string;
        archived: boolean;
        lesson: {
          subject: string;
          group: string;
          start: string;
          status: string;
        };
      }[]
    >([]),
    [record, setRecord] = useState<JournalEntry | null>(null);
  const [students, setStudents] = useState<string[]>(
      initial?.document?.studentIds || [],
    ),
    [assignments, setAssignments] = useState<Record<string, string>>(
      initial?.document?.variantsByStudent || {},
    );
  const [selected, setSelected] = useState(definition.variants[0].id),
    [dirty, setDirty] = useState(copy),
    [saving, setBusy] = useState(false),
    [loadingDays, setLoadingDays] = useState(false),
    [loadingLesson, setLoadingLesson] = useState(false),
    [error, setError] = useState("");
  const [pickerActivity, setPickerActivity] = useState<Activity>({
      dirty: false,
      busy: false,
    }),
    [epoch, setEpoch] = useState(0);
  const alive = useRef(true),
    loadedLesson = useRef(initial?.lessonKey || "");
  const busy = saving || loadingDays || loadingLesson;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onActivity({ dirty, busy: busy || pickerActivity.busy });
    return () => onActivity({ dirty: false, busy: false });
  }, [dirty, busy, pickerActivity.busy, onActivity]);
  useEffect(() => {
    let live = true;
    setLoadingDays(true);
    setError("");
    api(`journal/day?day=${day}`, workspace)
      .then((r) => {
        if (live)
          setLessons(
            r.lessons.filter(
              (l: any) => !l.archived && l.lesson.status !== "cancelled",
            ),
          );
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoadingDays(false);
      });
    return () => {
      live = false;
    };
  }, [workspace, day, epoch]);
  useEffect(() => {
    if (!lessonKey) {
      setRecord(null);
      setLoadingLesson(false);
      return;
    }
    let live = true;
    setLoadingLesson(true);
    setRecord(null);
    api(`journal/entry?${new URLSearchParams({ lessonKey, day })}`, workspace)
      .then((r) => {
        if (!live) return;
        setRecord(r);
        const ids: string[] = r.document.lesson.students
          .filter((s: any) => s.enrolled !== false)
          .map((s: any) => s.id);
        if (loadedLesson.current !== lessonKey) {
          setStudents(ids);
          setAssignments({});
        } else {
          setStudents((old) => old.filter((id) => ids.includes(id)));
          setAssignments((old) =>
            Object.fromEntries(
              Object.entries(old).filter(([id]) => ids.includes(id)),
            ),
          );
        }
        loadedLesson.current = lessonKey;
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoadingLesson(false);
      });
    return () => {
      live = false;
    };
  }, [workspace, lessonKey, day, epoch]);
  function patch(value: Partial<QuizDefinition>) {
    setDefinition((old) => ({ ...old, ...value }));
    setDirty(true);
  }
  function addQuestion(item: BankItem) {
    const variant = definition.variants.find((v) => v.id === selected)!;
    if (variant.questions.some((q) => q.id === item.id)) {
      setError("Этот вопрос уже есть в выбранном варианте.");
      return;
    }
    if (variant.questions.length >= 50) {
      setError("В варианте может быть не больше 50 вопросов.");
      return;
    }
    patch({
      variants: definition.variants.map((v) =>
        v.id === selected
          ? {
              ...v,
              questions: [
                ...v.questions,
                { id: item.id, version: item.version, question: item.question },
              ],
            }
          : v,
      ),
    });
    setError("");
  }
  async function save(publish: boolean) {
    if (busy || pickerActivity.busy || !record) return;
    setBusy(true);
    setError("");
    try {
      const result = await api(
        `journal/quizzes/${id.current}`,
        workspace,
        "PUT",
        {
          version: initial && !copy ? initial.version : 0,
          revision: record.revision,
          lessonKey,
          day,
          definition: {
            ...definition,
            opensAt: opens,
            closesAt: closes,
            variants: definition.variants.map((v) => ({
              ...v,
              questions: v.questions.map((q) => ({
                id: q.id,
                version: q.version,
              })),
            })),
          },
          studentIds: students,
          variantsByStudent: assignments,
          publish,
          ...(copy && initial ? { copyFrom: initial.id } : {}),
        },
      );
      if (alive.current) {
        setDirty(false);
        onSaved(result);
      }
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  const variant = definition.variants.find((v) => v.id === selected)!;
  const available =
    record?.document.lesson.students.filter((s) => s.enrolled !== false) || [];
  return (
    <section className="quiz-builder">
      <h2>
        {copy
          ? "Копия теста"
          : initial
            ? "Редактирование черновика"
            : "Новый тест"}
      </h2>
      <p className="info-line">
        Сначала выберите урок, состав и вопросы. После публикации вопросы,
        варианты и условия фиксируются; изменить их можно в новой копии теста.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save(true);
        }}
      >
        <fieldset
          className="access-fields"
          disabled={busy || pickerActivity.busy}
        >
          <div className="quiz-grid">
            <label className="field">
              <span>Дата урока для теста</span>
              <input
                type="date"
                required
                value={day}
                disabled={!!initial && !copy}
                onChange={(e) => {
                  setDay(e.target.value);
                  setLessonKey("");
                  setStudents([]);
                  setDirty(true);
                }}
              />
            </label>
            <label className="field">
              <span>Урок теста</span>
              <select
                value={lessonKey}
                required
                disabled={!!initial && !copy}
                onChange={(e) => {
                  setLessonKey(e.target.value);
                  setDirty(true);
                }}
              >
                <option value="">Выберите урок</option>
                {lessons.map((l) => (
                  <option key={l.lessonKey} value={l.lessonKey}>
                    {l.lesson.start} · {l.lesson.subject} · {l.lesson.group}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {!lessons.length && (
            <p>На эту дату доступных уроков нет. Выберите учебный день.</p>
          )}
          <button type="button" onClick={() => setEpoch((v) => v + 1)}>
            Обновить урок и состав
          </button>
          {record && (!record.writable || record.needsSync) && (
            <p className="error-banner">
              Сначала обновите состав урока или откройте его период в журнале.
            </p>
          )}
          <label className="field">
            <span>Название теста</span>
            <input
              required
              value={definition.title}
              maxLength={160}
              onChange={(e) => patch({ title: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Инструкция ученику</span>
            <textarea
              rows={3}
              value={definition.instructions}
              maxLength={4000}
              onChange={(e) => patch({ instructions: e.target.value })}
            />
          </label>
          <div className="quiz-grid">
            <label className="field">
              <span>Открытие · время школы, Кызылорда</span>
              <input
                type="datetime-local"
                required
                value={opens}
                onChange={(e) => {
                  setOpens(e.target.value);
                  setDirty(true);
                }}
              />
            </label>
            <label className="field">
              <span>Закрытие · время школы, Кызылорда</span>
              <input
                type="datetime-local"
                required
                value={closes}
                onChange={(e) => {
                  setCloses(e.target.value);
                  setDirty(true);
                }}
              />
            </label>
            <label className="field">
              <span>Минут на попытку</span>
              <input
                type="number"
                required
                min={1}
                max={240}
                value={definition.timeLimitMinutes}
                onChange={(e) =>
                  patch({ timeLimitMinutes: Number(e.target.value) })
                }
              />
            </label>
            <label className="field">
              <span>Число попыток</span>
              <input
                type="number"
                required
                min={1}
                max={10}
                value={definition.maxAttempts}
                onChange={(e) => patch({ maxAttempts: Number(e.target.value) })}
              />
            </label>
          </div>
          <p className="muted">
            Таймер начинается при открытии попытки учеником. Общий срок закрытия
            может завершить её раньше. Разбор открывает учитель после окончания
            приёма и ручной проверки.
          </p>
          <label className="field">
            <span>Категория в журнале</span>
            <select
              value={definition.journalCategory || ""}
              onChange={(e) => patch({ journalCategory: e.target.value })}
            >
              <option value="">По умолчанию школы</option>
              {(categories?.categories ?? [])
                .filter((c) => c.active || c.id === definition.journalCategory)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {c.active ? "" : " (отключена)"}
                  </option>
                ))}
            </select>
          </label>
          <p className="muted">
            В эту категорию попадёт работа, созданная при переносе результатов
            теста в журнал.
          </p>
          <label className="check-line">
            <input
              type="checkbox"
              checked={definition.shuffleQuestions}
              onChange={(e) => patch({ shuffleQuestions: e.target.checked })}
            />
            Перемешивать порядок вопросов в каждой попытке
          </label>
          <label className="check-line">
            <input
              type="checkbox"
              checked={definition.shuffleOptions}
              onChange={(e) => patch({ shuffleOptions: e.target.checked })}
            />
            Перемешивать варианты ответа
          </label>
          <div className="dated-toolbar">
            <label className="field">
              <span>Редактируемый вариант</span>
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                {definition.variants.map((v) => (
                  <option value={v.id} key={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              disabled={definition.variants.length >= 8}
              onClick={() => {
                const id = crypto.randomUUID();
                patch({
                  variants: [
                    ...definition.variants,
                    {
                      id,
                      name: `Вариант ${definition.variants.length + 1}`,
                      questions: [],
                    },
                  ],
                });
                setSelected(id);
              }}
            >
              Добавить вариант
            </button>
            <button
              type="button"
              disabled={definition.variants.length < 2}
              onClick={() => {
                const remaining = definition.variants.filter(
                  (v) => v.id !== selected,
                );
                patch({ variants: remaining });
                setAssignments((old) =>
                  Object.fromEntries(
                    Object.entries(old).filter(([, v]) => v !== selected),
                  ),
                );
                setSelected(remaining[0].id);
              }}
            >
              Удалить вариант
            </button>
          </div>
          <label className="field">
            <span>Название варианта</span>
            <input
              required
              maxLength={100}
              value={variant.name}
              onChange={(e) =>
                patch({
                  variants: definition.variants.map((v) =>
                    v.id === selected ? { ...v, name: e.target.value } : v,
                  ),
                })
              }
            />
          </label>
          <p>
            Вопросов: {variant.questions.length} · максимум:{" "}
            {variant.questions.reduce((sum, q) => sum + q.question.points, 0)}{" "}
            б. Все варианты должны иметь одинаковый максимум.
          </p>
          {!variant.questions.length && (
            <p className="info-line">Добавьте вопросы из банка ниже.</p>
          )}
          <ol className="quiz-question-list">
            {variant.questions.map((q, index) => (
              <li key={q.id}>
                <p className="journal-text">{q.question.text}</p>
                <small>
                  {questionKinds[q.question.kind]} · {q.question.points} б.
                </small>
                <div className="dated-toolbar">
                  <button
                    type="button"
                    disabled={!index}
                    onClick={() => {
                      const questions = [...variant.questions];
                      [questions[index - 1], questions[index]] = [
                        questions[index],
                        questions[index - 1],
                      ];
                      patch({
                        variants: definition.variants.map((v) =>
                          v.id === selected ? { ...v, questions } : v,
                        ),
                      });
                    }}
                  >
                    Выше
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      patch({
                        variants: definition.variants.map((v) =>
                          v.id === selected
                            ? {
                                ...v,
                                questions: v.questions.filter(
                                  (item) => item.id !== q.id,
                                ),
                              }
                            : v,
                        ),
                      })
                    }
                  >
                    Убрать вопрос
                  </button>
                </div>
              </li>
            ))}
          </ol>
          <fieldset className="quiz-roster">
            <legend>Кому назначить · выбрано {students.length}</legend>
            <div className="dated-toolbar">
              <button
                type="button"
                onClick={() => {
                  setStudents(available.map((s) => s.id));
                  setDirty(true);
                }}
              >
                Выбрать весь урок
              </button>
              <button
                type="button"
                onClick={() => {
                  setStudents([]);
                  setAssignments({});
                  setDirty(true);
                }}
              >
                Снять выбор
              </button>
            </div>
            {available.map((s) => (
              <div className="quiz-roster-row" key={s.id}>
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={students.includes(s.id)}
                    onChange={(e) => {
                      setStudents((old) =>
                        e.target.checked
                          ? [...old, s.id]
                          : old.filter((id) => id !== s.id),
                      );
                      if (!e.target.checked)
                        setAssignments((old) =>
                          Object.fromEntries(
                            Object.entries(old).filter(([id]) => id !== s.id),
                          ),
                        );
                      setDirty(true);
                    }}
                  />
                  {s.name}
                </label>
                <select
                  aria-label={`Вариант для ${s.name}`}
                  disabled={!students.includes(s.id)}
                  value={assignments[s.id] || ""}
                  onChange={(e) => {
                    setAssignments((old) =>
                      e.target.value
                        ? { ...old, [s.id]: e.target.value }
                        : Object.fromEntries(
                            Object.entries(old).filter(([id]) => id !== s.id),
                          ),
                    );
                    setDirty(true);
                  }}
                >
                  <option value="">Распределить автоматически</option>
                  {definition.variants.map((v) => (
                    <option value={v.id} key={v.id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </fieldset>
          <p className="muted">
            Автоматическое распределение выравнивает число учеников в вариантах.
            Можно назначить вариант каждому вручную.
          </p>
          <div className="dated-toolbar">
            <button
              type="submit"
              className="primary"
              disabled={
                !record?.writable ||
                record.needsSync ||
                !students.length ||
                definition.variants.some((v) => !v.questions.length)
              }
            >
              Опубликовать тест
            </button>
            <button
              type="button"
              disabled={
                !record?.writable ||
                record.needsSync ||
                !students.length ||
                !definition.title.trim() ||
                definition.variants.some((v) => !v.questions.length)
              }
              onClick={() => void save(false)}
            >
              Сохранить черновик теста
            </button>
            <button
              type="button"
              onClick={() => {
                if (
                  !dirty ||
                  window.confirm("Закрыть несохранённые изменения теста?")
                )
                  onClose();
              }}
            >
              Закрыть редактор теста
            </button>
          </div>
        </fieldset>
      </form>
      <details open className="quiz-picker">
        <summary>Банк вопросов · добавление в «{variant.name}»</summary>
        <fieldset className="quiz-picker-fields" disabled={busy}>
          <QuizBankPanel
            workspace={workspace}
            onActivity={setPickerActivity}
            onPick={addQuestion}
          />
        </fieldset>
      </details>
    </section>
  );
}

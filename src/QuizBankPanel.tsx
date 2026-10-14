import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import {
  blankQuestion,
  questionKinds,
  type Activity,
  type BankItem,
  type Question,
} from "./quiz";

export default function QuizBankPanel({
  workspace,
  onActivity,
  onPick,
}: {
  workspace: string;
  onActivity: (activity: Activity) => void;
  onPick?: (item: BankItem) => void;
}) {
  const [items, setItems] = useState<BankItem[]>([]),
    [next, setNext] = useState<string | null>(null),
    [after, setAfter] = useState("");
  const [search, setSearch] = useState(""),
    [applied, setApplied] = useState(""),
    [archived, setArchived] = useState(false),
    [epoch, setEpoch] = useState(0);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [editing, setEditing] = useState<BankItem | null>(null),
    [dirty, setDirty] = useState(false);
  const [tagsText, setTagsText] = useState("");
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onActivity({ dirty, busy });
    return () => onActivity({ dirty: false, busy: false });
  }, [dirty, busy, onActivity]);
  useEffect(() => {
    let live = true;
    setBusy(true);
    setError("");
    api(
      `journal/quiz-bank?${new URLSearchParams({ search: applied, archived: String(archived), after })}`,
      workspace,
    )
      .then((r) => {
        if (live) {
          setItems(r.items);
          setNext(r.nextAfter);
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
  }, [workspace, applied, archived, after, epoch]);
  function canLeave() {
    return (
      !busy &&
      (!dirty || window.confirm("Закрыть несохранённые изменения вопроса?"))
    );
  }
  function patch(value: Partial<BankItem>) {
    setEditing((old) => (old ? { ...old, ...value } : old));
    setDirty(true);
  }
  function question(value: Partial<Question>) {
    if (editing) patch({ question: { ...editing.question, ...value } });
  }
  async function save() {
    if (!editing || busy) return;
    setBusy(true);
    setError("");
    try {
      await api(
        `journal/quiz-bank/${encodeURIComponent(editing.id)}`,
        workspace,
        "PUT",
        {
          version: editing.version,
          subject: editing.subject,
          tags: tagsText
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean),
          question: editing.question,
          archived: editing.archived,
        },
      );
      if (alive.current) {
        setEditing(null);
        setDirty(false);
        setEpoch((v) => v + 1);
      }
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  const q = editing?.question;
  return (
    <section className="quiz-bank">
      <p className="info-line">
        Вопросы вашего банка. Опубликованные тесты сохраняют выбранные версии:
        дальнейшие изменения и архивирование не меняют начатые попытки.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {!editing && (
        <>
          <form
            className="dated-toolbar"
            onSubmit={(e) => {
              e.preventDefault();
              setAfter("");
              setApplied(search);
            }}
          >
            <label className="field">
              <span>Поиск вопросов</span>
              <input
                value={search}
                maxLength={120}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <button disabled={busy}>Найти вопросы</button>
            <label className="check-line">
              <input
                type="checkbox"
                checked={archived}
                disabled={busy}
                onChange={(e) => {
                  setArchived(e.target.checked);
                  setAfter("");
                }}
              />
              Архив банка
            </label>
            {!onPick && (
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => {
                  setTagsText("");
                  setEditing({
                    id: crypto.randomUUID(),
                    version: 0,
                    subject: "",
                    tags: [],
                    question: blankQuestion(),
                    archived: false,
                  });
                  setDirty(false);
                }}
              >
                Добавить вопрос
              </button>
            )}
          </form>
          {busy ? (
            <p role="status">Загружаем банк…</p>
          ) : (
            !items.length && (
              <p>
                Вопросов по этим условиям нет. Добавьте их в банк перед
                созданием теста.
              </p>
            )
          )}
          <div className="quiz-cards">
            {items.map((item) => (
              <article className="quiz-card" key={item.id}>
                <strong>
                  {questionKinds[item.question.kind]} · {item.question.points}{" "}
                  б.
                </strong>
                <p className="journal-text">{item.question.text}</p>
                <small>
                  {item.subject} · {item.tags.join(", ")} · версия{" "}
                  {item.version}
                </small>
                {onPick ? (
                  <button
                    type="button"
                    disabled={busy || item.archived}
                    onClick={() => onPick(item)}
                  >
                    Добавить в вариант
                  </button>
                ) : (
                  <div className="dated-toolbar">
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (canLeave()) {
                          setTagsText(item.tags.join(", "));
                          setEditing(structuredClone(item));
                          setDirty(false);
                        }
                      }}
                    >
                      Изменить вопрос
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (canLeave()) {
                          setTagsText(item.tags.join(", "));
                          setEditing({
                            ...structuredClone(item),
                            id: crypto.randomUUID(),
                            version: 0,
                            archived: false,
                          });
                          setDirty(true);
                        }
                      }}
                    >
                      Копировать вопрос
                    </button>
                  </div>
                )}
              </article>
            ))}
          </div>
          <div className="dated-toolbar">
            {after && (
              <button disabled={busy} onClick={() => setAfter("")}>
                В начало банка
              </button>
            )}
            {next && (
              <button disabled={busy} onClick={() => setAfter(next)}>
                Следующие вопросы
              </button>
            )}
          </div>
        </>
      )}
      {editing && q && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset className="access-fields" disabled={busy}>
            <legend>
              {editing.version ? "Редактирование вопроса" : "Новый вопрос"}
            </legend>
            <label className="field">
              <span>Предмет в банке</span>
              <input
                value={editing.subject}
                maxLength={160}
                onChange={(e) => patch({ subject: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Тип вопроса</span>
              <select
                value={q.kind}
                onChange={(e) => {
                  const kind = e.target.value as Question["kind"];
                  const choice = kind === "single" || kind === "multiple";
                  question({
                    kind,
                    options: choice ? blankQuestion().options : [],
                    correctIds: [],
                    accepted: kind === "short" ? [""] : [],
                    caseSensitive: false,
                    number: kind === "number" ? 0 : null,
                    tolerance: 0,
                  });
                }}
              >
                {Object.entries(questionKinds).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Текст вопроса</span>
              <textarea
                required
                rows={4}
                value={q.text}
                maxLength={6000}
                onChange={(e) => question({ text: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Баллы за вопрос</span>
              <input
                type="number"
                required
                min={0.001}
                max={1000}
                step="any"
                value={q.points}
                onChange={(e) => question({ points: Number(e.target.value) })}
              />
            </label>
            {(q.kind === "single" || q.kind === "multiple") && (
              <fieldset className="quiz-options">
                <legend>Варианты ответа · отметьте правильные</legend>
                {q.options.map((option, index) => (
                  <div className="quiz-option-editor" key={option.id}>
                    <label className="check-line">
                      <input
                        aria-label={`Правильный ответ ${index + 1}`}
                        type={q.kind === "single" ? "radio" : "checkbox"}
                        name="correct-answer"
                        checked={q.correctIds.includes(option.id)}
                        onChange={(e) =>
                          question({
                            correctIds:
                              q.kind === "single"
                                ? [option.id]
                                : e.target.checked
                                  ? [...q.correctIds, option.id]
                                  : q.correctIds.filter(
                                      (id) => id !== option.id,
                                    ),
                          })
                        }
                      />
                      {index + 1}.
                    </label>
                    <input
                      aria-label={`Текст ответа ${index + 1}`}
                      required
                      maxLength={1000}
                      value={option.text}
                      onChange={(e) =>
                        question({
                          options: q.options.map((o) =>
                            o.id === option.id
                              ? { ...o, text: e.target.value }
                              : o,
                          ),
                        })
                      }
                    />
                    <button
                      type="button"
                      disabled={q.options.length <= 2}
                      onClick={() =>
                        question({
                          options: q.options.filter((o) => o.id !== option.id),
                          correctIds: q.correctIds.filter(
                            (id) => id !== option.id,
                          ),
                        })
                      }
                    >
                      Убрать
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  disabled={q.options.length >= 12}
                  onClick={() =>
                    question({
                      options: [
                        ...q.options,
                        { id: crypto.randomUUID(), text: "" },
                      ],
                    })
                  }
                >
                  Добавить ответ
                </button>
                {q.kind === "multiple" && (
                  <small>
                    Полный балл даётся только за точный набор правильных
                    вариантов.
                  </small>
                )}
              </fieldset>
            )}
            {q.kind === "short" && (
              <>
                <label className="field">
                  <span>Допустимые ответы, по одному в строке</span>
                  <textarea
                    required
                    rows={3}
                    value={q.accepted.join("\n")}
                    onChange={(e) =>
                      question({ accepted: e.target.value.split("\n") })
                    }
                  />
                </label>
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={q.caseSensitive}
                    onChange={(e) =>
                      question({ caseSensitive: e.target.checked })
                    }
                  />
                  Учитывать регистр букв
                </label>
                <small>
                  Повторные пробелы и эквивалентные символы Unicode
                  нормализуются.
                </small>
              </>
            )}
            {q.kind === "number" && (
              <>
                <label className="field">
                  <span>Правильное число</span>
                  <input
                    required
                    type="number"
                    step="any"
                    min={-1e12}
                    max={1e12}
                    value={q.number ?? ""}
                    onChange={(e) =>
                      question({
                        number:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  />
                </label>
                <label className="field">
                  <span>Допустимая абсолютная погрешность</span>
                  <input
                    required
                    type="number"
                    step="any"
                    min={0}
                    max={1e12}
                    value={q.tolerance}
                    onChange={(e) =>
                      question({ tolerance: Number(e.target.value) })
                    }
                  />
                </label>
              </>
            )}
            {q.kind === "essay" && (
              <p className="muted">
                Развёрнутый ответ проверяет учитель. Результаты открываются
                после завершения проверки.
              </p>
            )}
            <label className="field">
              <span>Объяснение для разбора</span>
              <textarea
                rows={3}
                maxLength={4000}
                value={q.explanation}
                onChange={(e) => question({ explanation: e.target.value })}
              />
              <small>
                Ученик увидит объяснение только после открытия разбора учителем.
              </small>
            </label>
            <label className="field">
              <span>Метки через запятую</span>
              <input
                value={tagsText}
                maxLength={610}
                onChange={(e) => {
                  setTagsText(e.target.value);
                  setDirty(true);
                }}
              />
            </label>
            <label className="check-line">
              <input
                type="checkbox"
                checked={editing.archived}
                onChange={(e) => patch({ archived: e.target.checked })}
              />
              Убрать в архив банка
            </label>
            <div className="dated-toolbar">
              <button className="primary" type="submit">
                Сохранить вопрос
              </button>
              <button
                type="button"
                onClick={() => {
                  if (canLeave()) {
                    setEditing(null);
                    setDirty(false);
                  }
                }}
              >
                Закрыть вопрос
              </button>
            </div>
          </fieldset>
        </form>
      )}
    </section>
  );
}

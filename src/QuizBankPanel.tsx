import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import QuizFiles from "./QuizFiles";
import {
  blankQuestion,
  questionKinds,
  type Activity,
  type BankItem,
  type Question,
} from "./quiz";
import {
  MAX_SELECTION,
  exportFailure,
  exportPackage,
  packageError,
  previewSummary,
  refreshSelected,
  saveBlob,
  sendPackage,
  toggleSelected,
  type ImportResult,
  type PackagePreview,
} from "./quizBankPackage";

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
  const [selecting, setSelecting] = useState(false),
    [selected, setSelected] = useState<Map<string, number>>(new Map()),
    [preview, setPreview] = useState<PackagePreview | null>(null),
    [importFile, setImportFile] = useState<File | null>(null),
    [importRequest, setImportRequest] = useState(""),
    [notice, setNotice] = useState("");
  const alive = useRef(true),
    staleMessage = useRef("");
  const uploadLock = useRef(false),
    uploadAbort = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      uploadAbort.current?.abort();
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
          setSelected((old) =>
            old.size ? refreshSelected(old, r.items as BankItem[]) : old,
          );
          // The reload clears the banner, so a refused export reports after it.
          if (staleMessage.current) setError(staleMessage.current);
          staleMessage.current = "";
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
        staleMessage.current = "";
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
      !uploadLock.current &&
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
    if (uploadLock.current) return;
    if (!editing || busy) return;
    setBusy(true);
    setError("");
    try {
      const saved: BankItem = await api(
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
        // The edit may take the question out of the list that refreshes versions.
        setSelected((old) =>
          old.has(saved.id) ? refreshSelected(old, [saved]) : old,
        );
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
  async function transfer(work: (signal: AbortSignal) => Promise<void>) {
    if (busy || uploadLock.current) return;
    uploadLock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const controller = new AbortController();
    uploadAbort.current = controller;
    try {
      await work(controller.signal);
    } catch (e: any) {
      if (alive.current && e.name !== "AbortError") setError(e.message);
    } finally {
      uploadLock.current = false;
      uploadAbort.current = null;
      if (alive.current) setBusy(false);
    }
  }
  function toggle(item: BankItem) {
    const result = toggleSelected(selected, item);
    setSelected(result.selected);
    setError(result.error);
  }
  function exportSelected() {
    void transfer(async (signal) => {
      try {
        const { blob, filename } = await exportPackage(
          workspace,
          selected,
          signal,
        );
        saveBlob(blob, filename);
        if (alive.current) setNotice("Пакет сохранён.");
      } catch (e: any) {
        if (e.status !== 409 || !alive.current) throw e;
        staleMessage.current = exportFailure(e);
        setEpoch((v) => v + 1);
      }
    });
  }
  function choosePackage(file: File | undefined) {
    if (!file || busy || uploadLock.current) return;
    const problem = packageError(file);
    if (problem) {
      setError(problem);
      return;
    }
    setPreview(null);
    setImportFile(null);
    void transfer(async (signal) => {
      const result = await sendPackage(
        "import/preview",
        workspace,
        file,
        {},
        signal,
      );
      if (alive.current) {
        setPreview(result);
        setImportFile(file);
        setImportRequest(crypto.randomUUID());
      }
    });
  }
  function confirmImport() {
    if (!preview || !importFile) return;
    void transfer(async (signal) => {
      const result: ImportResult = await sendPackage(
        "import",
        workspace,
        importFile,
        { requestId: importRequest, packageHash: preview.packageHash },
        signal,
      );
      if (alive.current) {
        setNotice(
          `Импортировано вопросов: ${result.imported}` +
            (result.archived ? `, из них в архиве: ${result.archived}` : ""),
        );
        setPreview(null);
        setImportFile(null);
        setImportRequest("");
        setAfter("");
        setEpoch((v) => v + 1);
      }
    });
  }
  const q = editing?.question;
  async function upload(files: FileList | null, solution: boolean) {
    if (!files?.length || !editing || busy || uploadLock.current) return;
    const ids = solution ? "solutionFileIds" : "fileIds";
    const projection = solution ? "solutionAttachments" : "attachments";
    const batch = Array.from(files);
    if (
      batch.length + (editing.question[ids]?.length || 0) > 6 ||
      batch.some((f) => !f.size || f.size > 10 * 1024 * 1024)
    ) {
      setError("До шести непустых файлов в каждом разделе, каждый до 10 МБ.");
      return;
    }
    uploadLock.current = true;
    setBusy(true);
    setError("");
    const controller = new AbortController();
    uploadAbort.current = controller;
    try {
      for (const file of batch) {
        const params = new URLSearchParams({ workspace, filename: file.name });
        const response = await fetch(`/api/journal/quiz-files?${params}`, {
          method: "POST",
          headers: {
            "X-Atlas-Request": "1",
            "Content-Type": "application/octet-stream",
          },
          body: file,
          signal: controller.signal,
        });
        const result = await response.json();
        if (!response.ok) {
          if (response.status === 401)
            window.dispatchEvent(new Event("atlas-auth-required"));
          throw new Error(
            typeof result.detail === "string"
              ? result.detail
              : "Не удалось загрузить файл.",
          );
        }
        if (alive.current) {
          setEditing((old) =>
            old
              ? {
                  ...old,
                  question: {
                    ...old.question,
                    [ids]: [...(old.question[ids] || []), result.id],
                    [projection]: [...(old.question[projection] || []), result],
                  },
                }
              : old,
          );
          setDirty(true);
        }
      }
    } catch (e: any) {
      if (alive.current && e.name !== "AbortError") setError(e.message);
    } finally {
      uploadLock.current = false;
      uploadAbort.current = null;
      if (alive.current) setBusy(false);
    }
  }
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
      {notice && <p role="status">{notice}</p>}
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
          {!onPick && (
            <div className="dated-toolbar">
              {selecting ? (
                <>
                  <span role="status">
                    Выбрано: {selected.size} из {MAX_SELECTION}
                  </span>
                  <button
                    type="button"
                    className="primary"
                    disabled={busy || !selected.size}
                    onClick={exportSelected}
                  >
                    Экспорт
                  </button>
                  <button
                    type="button"
                    disabled={busy || !selected.size}
                    onClick={() => setSelected(new Map())}
                  >
                    Снять выбор
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setSelecting(false)}
                  >
                    Готово
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setSelecting(true)}
                >
                  Выбрать для экспорта
                  {selected.size ? ` (${selected.size})` : ""}
                </button>
              )}
              <label className="field">
                <span>Импорт пакета (ZIP или JSON)</span>
                <input
                  type="file"
                  accept=".zip,.json"
                  disabled={busy}
                  onChange={(e) => {
                    choosePackage(e.target.files?.[0]);
                    e.target.value = "";
                  }}
                />
              </label>
              <a
                href={`/api/journal/quiz-bank/template?workspace=${encodeURIComponent(workspace)}`}
                download
              >
                Шаблон JSON
              </a>
            </div>
          )}
          {preview && !onPick && (
            <section className="quiz-card" aria-live="polite">
              <h3>Предпросмотр импорта</h3>
              <p>{previewSummary(preview)}</p>
              {preview.archived > 0 && (
                <p>Архивные вопросы попадут в «Архив банка».</p>
              )}
              <ul>
                {preview.items.slice(0, 10).map((item, index) => (
                  <li key={index}>
                    {questionKinds[item.kind as keyof typeof questionKinds] ??
                      item.kind}
                    : {item.text}
                  </li>
                ))}
              </ul>
              {preview.items.length > 10 && (
                <small>Показаны первые 10 из {preview.items.length}.</small>
              )}
              <div className="dated-toolbar">
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={confirmImport}
                >
                  Импортировать вопросы
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setPreview(null);
                    setImportFile(null);
                    setImportRequest("");
                  }}
                >
                  Отмена
                </button>
              </div>
            </section>
          )}
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
                ) : selecting ? (
                  <label className="check-line">
                    <input
                      type="checkbox"
                      checked={selected.has(item.id)}
                      disabled={busy}
                      onChange={() => toggle(item)}
                    />
                    Выбрать вопрос
                  </label>
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
            <fieldset>
              <legend>Материалы вопроса</legend>
              <QuizFiles
                files={q.attachments}
                workspace={workspace}
                disabled={busy}
                onRemove={(id) =>
                  question({
                    fileIds: (q.fileIds || []).filter((v) => v !== id),
                    attachments: (q.attachments || []).filter(
                      (f) => f.id !== id,
                    ),
                  })
                }
              />
              <label className="field">
                <span>Добавить файл к вопросу</span>
                <input
                  type="file"
                  multiple
                  accept=".pdf,.png,.jpg,.jpeg,.txt,.docx,.xlsx,.pptx,.mp3,.wav,.m4a,.ogg,.mp4,.webm"
                  onChange={(e) => {
                    void upload(e.target.files, false);
                    e.target.value = "";
                  }}
                />
              </label>
              <small>
                До 6 файлов по 10 МБ: изображения, PDF, документы Office, текст,
                аудио и видео. Ученик увидит их после начала попытки.
              </small>
            </fieldset>
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
            <fieldset>
              <legend>Материалы разбора</legend>
              <QuizFiles
                files={q.solutionAttachments}
                workspace={workspace}
                solution
                disabled={busy}
                onRemove={(id) =>
                  question({
                    solutionFileIds: (q.solutionFileIds || []).filter(
                      (v) => v !== id,
                    ),
                    solutionAttachments: (q.solutionAttachments || []).filter(
                      (f) => f.id !== id,
                    ),
                  })
                }
              />
              <label className="field">
                <span>Добавить файл к разбору</span>
                <input
                  type="file"
                  multiple
                  accept=".pdf,.png,.jpg,.jpeg,.txt,.docx,.xlsx,.pptx,.mp3,.wav,.m4a,.ogg,.mp4,.webm"
                  onChange={(e) => {
                    void upload(e.target.files, true);
                    e.target.value = "";
                  }}
                />
              </label>
              <small>
                До 6 файлов по 10 МБ. Ученики и родители увидят их только после
                открытия разбора.
              </small>
            </fieldset>
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

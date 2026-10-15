import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import { displayTime } from "./meetings";
import DocumentTemplateEditor from "./DocumentTemplateEditor";
import { kinds, statuses, actions, blankTemplate } from "./documentTypes";
import type {
  Item,
  Options,
  Template,
  Preview,
  Snapshot,
  History,
} from "./documentTypes";
import "./SchoolDocuments.css";

export default function SchoolDocuments({
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
      : Array.from(
          new Set([...user.workspaces, ...user.grants.map((g) => g.workspace)]),
        );
  const [workspace, setWorkspace] = useState(
      spaces.includes(initialWorkspace)
        ? initialWorkspace
        : spaces[0] || "school",
    ),
    [options, setOptions] = useState<Options | null>(null);
  const [items, setItems] = useState<Item[]>([]),
    [next, setNext] = useState<number | null>(null),
    [status, setStatus] = useState(""),
    [archived, setArchived] = useState(false),
    [year, setYear] = useState("");
  const [yearInput, setYearInput] = useState("");
  const [selected, setSelected] = useState<Item | null>(null),
    [values, setValues] = useState<Record<string, string>>({}),
    [dirty, setDirty] = useState(false);
  const [template, setTemplate] = useState<Template | null>(null),
    [newDoc, setNewDoc] = useState<{
      templateId: string;
      subjectId: string;
      assigneeId: string;
    } | null>(null);
  const [preview, setPreview] = useState<
      (Preview & { recipientsOnly: boolean }) | null
    >(null),
    [history, setHistory] = useState<{
      entity: string;
      id: string;
      items: History[];
      nextBefore: number | null;
    } | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  const canLeave = () =>
    !dirty ||
    window.confirm("Есть несохранённые изменения. Продолжить без сохранения?");
  const clear = () => {
    setSelected(null);
    setValues({});
    setTemplate(null);
    setNewDoc(null);
    setPreview(null);
    setHistory(null);
    setDirty(false);
  };
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
  const listPath = (before?: number) =>
    `documents/items?status=${status}&archived=${archived}&year=${year || 0}${before ? "&before=" + before : ""}`;
  async function load() {
    const n = ++sequence.current;
    const [o, r] = await Promise.all([
      api("documents/options", workspace),
      api(listPath(), workspace),
    ]);
    if (n === sequence.current) {
      setOptions(o);
      setItems(r.items);
      setNext(r.nextBefore);
    }
  }
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      setPreview(null);
      if ([401, 403, 404].includes(e.status)) {
        clear();
        setOptions(null);
        setItems([]);
        setNext(null);
      }
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    clear();
    setOptions(null);
    setItems([]);
    void run(load);
    return () => {
      ++sequence.current;
    };
  }, [workspace, status, archived, year]);
  async function mutate(path: string, body: object, method = "POST") {
    const key = JSON.stringify([workspace, path, body, method]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const r = await api("documents/" + path, workspace, method, {
      ...body,
      requestId: replay.current.id,
    });
    replay.current = { key: "", id: "" };
    return r;
  }
  async function openItem(id: string) {
    const d = await api("documents/items/" + id, workspace);
    clear();
    setSelected(d);
    setValues(d.values || {});
  }
  async function changed(id?: string) {
    clear();
    await load();
    if (id) await openItem(id);
    setNotice("Изменения сохранены.");
  }
  async function applyAction(action: string, p?: Preview) {
    if (!selected || !canLeave()) return;
    const reason = [
      "return",
      "cancel",
      "revoke",
      "archive",
      "unarchive",
      "recipients",
    ].includes(action)
      ? window.prompt("Причина действия")
      : "";
    if (reason === null) return;
    await run(async () => {
      await mutate(`items/${selected.id}/action`, {
        version: selected.version,
        action,
        reason,
        previewHash: p?.previewHash,
      });
      await changed(selected.id);
    });
  }
  async function showHistory(entity: string, id: string, more = false) {
    await run(async () => {
      const h = await api(
        `documents/history/${entity}/${id}${more && history?.nextBefore ? "?before=" + history.nextBefore : ""}`,
        workspace,
      );
      setHistory({
        entity,
        id,
        items: more ? [...history!.items, ...h.items] : h.items,
        nextBefore: h.nextBefore,
      });
    });
  }
  async function check(recipientsOnly = false) {
    if (!selected || !canLeave()) return;
    await run(async () => {
      await openItem(selected.id);
      const p = await api(
        `documents/items/${selected.id}/preview?recipients=${recipientsOnly}`,
        workspace,
      );
      setPreview({ ...p, recipientsOnly });
    });
  }
  const switchValue = (change: () => void) => {
    if (canLeave()) {
      clear();
      change();
    }
  };
  const admin = !!options?.canManage,
    editable =
      !!selected?.canPrepare &&
      ["draft", "returned"].includes(selected.status) &&
      !selected.archived;
  const selectedTemplate = options?.templates.find(
    (t) => t.id === newDoc?.templateId,
  );
  return (
    <dialog
      className="school-documents"
      ref={dialog}
      aria-labelledby="documents-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && canLeave()) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="documents-title">Документы и архив</h2>
          <p>
            {admin
              ? "Шаблоны, подготовка и выдача школьных документов"
              : "Ваши документы и поручения по подготовке"}
          </p>
        </div>
        <button
          disabled={busy}
          onClick={() => {
            if (canLeave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      {error && (
        <p className="document-error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <fieldset disabled={busy}>
        <div className="document-controls">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => switchValue(() => setWorkspace(e.target.value))}
            >
              {spaces.map((s) => (
                <option key={s} value={s}>
                  {s === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Состояние
            <select
              value={status}
              onChange={(e) => switchValue(() => setStatus(e.target.value))}
            >
              <option value="">Все состояния</option>
              {Object.entries(statuses).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            Год создания
            <input
              type="number"
              min="1"
              max="9998"
              placeholder="Все годы"
              value={yearInput}
              onChange={(e) => setYearInput(e.target.value)}
            />
          </label>
          <button
            onClick={() => {
              if (
                yearInput &&
                (!/^\d{1,4}$/.test(yearInput) ||
                  Number(yearInput) < 1 ||
                  Number(yearInput) > 9998)
              ) {
                setError("Укажите год от 1 до 9998 либо оставьте поле пустым.");
                return;
              }
              switchValue(() => setYear(yearInput));
            }}
          >
            Применить год
          </button>
          <label className="document-check">
            <input
              type="checkbox"
              checked={archived}
              onChange={(e) => switchValue(() => setArchived(e.target.checked))}
            />
            Включая архив
          </label>
          <button
            onClick={() => {
              if (canLeave()) {
                clear();
                void run(load);
              }
            }}
          >
            Обновить
          </button>
        </div>
        {admin && (
          <>
            <div className="document-actions">
              <button
                onClick={() => {
                  if (canLeave()) {
                    clear();
                    setTemplate(blankTemplate());
                  }
                }}
              >
                Новый шаблон
              </button>
              <button
                disabled={!options?.templates.some((t) => t.active)}
                onClick={() => {
                  if (canLeave()) {
                    clear();
                    setNewDoc({
                      templateId: options!.templates.find((t) => t.active)!.id,
                      subjectId: "",
                      assigneeId: "",
                    });
                  }
                }}
              >
                Подготовить документ
              </button>
              <a
                href={`/api/documents/export?workspace=${encodeURIComponent(workspace)}&year=${year || options?.year}`}
                download
              >
                Реестр CSV за {year || options?.year}
              </a>
            </div>
            <details className="document-templates">
              <summary>Шаблоны ({options?.templates.length || 0})</summary>
              {!options?.templates.length && (
                <p>Создайте шаблон и разрешите выдачу документов.</p>
              )}
              {options?.templates.map((t) => (
                <article key={t.id}>
                  <strong>{t.definition.title}</strong>
                  <p>
                    {kinds[t.definition.kind]} · {t.definition.series} ·{" "}
                    {t.active ? "Выдача разрешена" : "Отключён"}
                  </p>
                  <div className="document-actions">
                    <button
                      onClick={() => {
                        if (canLeave()) {
                          clear();
                          setTemplate(structuredClone(t));
                        }
                      }}
                    >
                      Изменить шаблон
                    </button>
                    <button
                      onClick={() => {
                        if (canLeave()) {
                          clear();
                          setTemplate({
                            ...structuredClone(t),
                            id: "",
                            version: 0,
                            active: false,
                            definition: {
                              ...structuredClone(t.definition),
                              title: t.definition.title + " — копия",
                            },
                          });
                        }
                      }}
                    >
                      Копировать шаблон
                    </button>
                    <button onClick={() => void showHistory("template", t.id)}>
                      История шаблона
                    </button>
                  </div>
                </article>
              ))}
            </details>
          </>
        )}
        {template && (
          <DocumentTemplateEditor
            value={template}
            onChange={(v) => {
              setTemplate(v);
              setDirty(true);
            }}
            onCancel={() => {
              if (canLeave()) clear();
            }}
            onSave={() => {
              const reason = template.id
                ? window.prompt("Причина изменения шаблона")
                : "";
              if (reason === null) return;
              void run(async () => {
                await mutate(
                  template.id ? "templates/" + template.id : "templates",
                  {
                    version: template.version,
                    definition: template.definition,
                    active: template.active,
                    reason,
                  },
                  template.id ? "PUT" : "POST",
                );
                await changed();
              });
            }}
          />
        )}
        {newDoc && (
          <form
            className="document-create"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                const d = await mutate("items", {
                  ...newDoc,
                  templateVersion: selectedTemplate!.version,
                });
                await changed(d.id);
              });
            }}
          >
            <h3>Подготовить документ</h3>
            <div className="document-controls">
              <label>
                Шаблон
                <select
                  value={newDoc.templateId}
                  required
                  onChange={(e) => {
                    setNewDoc({
                      ...newDoc,
                      templateId: e.target.value,
                      subjectId: "",
                    });
                    setDirty(true);
                  }}
                >
                  {options?.templates
                    .filter((t) => t.active)
                    .map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.definition.title}
                      </option>
                    ))}
                </select>
              </label>
              {selectedTemplate?.definition.kind !== "internal" && (
                <label>
                  Получатель
                  <select
                    required
                    value={newDoc.subjectId}
                    onChange={(e) => {
                      setNewDoc({ ...newDoc, subjectId: e.target.value });
                      setDirty(true);
                    }}
                  >
                    <option value="">Выберите получателя</option>
                    {(selectedTemplate?.definition.kind === "student"
                      ? options?.students
                      : options?.employees
                    )?.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                        {p.className ? " · " + p.className : ""}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label>
                Ответственный за подготовку
                <select
                  value={newDoc.assigneeId}
                  onChange={(e) => {
                    setNewDoc({ ...newDoc, assigneeId: e.target.value });
                    setDirty(true);
                  }}
                >
                  <option value="">Только администрация</option>
                  {options?.employees.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p>
              Ответственный сможет читать этот документ и его историю, заполнять
              поля и передавать документ на проверку. Номер и окончательную
              выдачу подтверждает администрация.
            </p>
            <div className="document-actions">
              <button>Создать черновик</button>
              <button
                type="button"
                onClick={() => {
                  if (canLeave()) clear();
                }}
              >
                Отмена
              </button>
            </div>
          </form>
        )}
        <div className="document-layout">
          <section className="document-list" aria-label="Список документов">
            <h3>{archived ? "Документы и архив" : "Документы"}</h3>
            {!items.length && <p>Нет доступных документов по этим условиям.</p>}
            {items.map((item) => (
              <button
                className={selected?.id === item.id ? "selected" : ""}
                key={item.id}
                onClick={() => {
                  if (canLeave()) void run(() => openItem(item.id));
                }}
              >
                <strong>{item.title}</strong>
                <span>{item.subjectName}</span>
                <span>
                  {item.number || "Без номера"} · {statuses[item.status]}
                  {item.archived ? " · Архив" : ""}
                </span>
              </button>
            ))}
            {next && (
              <button
                onClick={() =>
                  void run(async () => {
                    const r = await api(listPath(next), workspace);
                    setItems([...items, ...r.items]);
                    setNext(r.nextBefore);
                  })
                }
              >
                Ещё документы
              </button>
            )}
          </section>
          {selected && (
            <section className="document-detail">
              <h3>{selected.title}</h3>
              <p>
                {selected.subjectName} · {statuses[selected.status]}
                {selected.archived ? " · Архив" : ""}
              </p>
              {selected.revocationReason && (
                <p className="document-error">
                  Документ отозван: {selected.revocationReason}
                </p>
              )}
              {selected.snapshot && (
                <>
                  <DocumentPaper snapshot={selected.snapshot} />
                  <div className="document-actions">
                    <a
                      href={`/api/documents/items/${selected.id}/print?workspace=${encodeURIComponent(workspace)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Открыть для печати
                    </a>
                    <a
                      href={`/api/documents/items/${selected.id}/print?workspace=${encodeURIComponent(workspace)}&download=true`}
                      download
                    >
                      Скачать документ HTML
                    </a>
                  </div>
                  <p>
                    Для печати или сохранения в PDF откройте документ и
                    используйте печать браузера. Электронная подпись не
                    добавлена.
                  </p>
                </>
              )}
              {selected.canPrepare &&
                selected.definition &&
                !selected.snapshot && (
                  <form
                    className="document-fields"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void run(async () => {
                        await mutate(
                          "items/" + selected.id,
                          { version: selected.version, values },
                          "PUT",
                        );
                        await changed(selected.id);
                      });
                    }}
                  >
                    <p>
                      Редакция шаблона: {selected.templateVersion}. Номер будет
                      назначен при выдаче.
                    </p>
                    {selected.definition.fields.map((f) => (
                      <label key={f.key}>
                        {f.label}
                        {f.required ? " *" : ""}
                        <textarea
                          disabled={!editable}
                          maxLength={2000}
                          value={values[f.key] || ""}
                          onChange={(e) => {
                            setValues({ ...values, [f.key]: e.target.value });
                            setDirty(true);
                            setPreview(null);
                          }}
                        />
                      </label>
                    ))}
                    {editable && <button>Сохранить черновик</button>}
                  </form>
                )}
              {selected.canPrepare && (
                <div className="document-actions">
                  {!selected.archived &&
                    ["draft", "returned"].includes(selected.status) && (
                      <button onClick={() => void applyAction("submit")}>
                        На проверку
                      </button>
                    )}
                  {!selected.archived && selected.status === "submitted" && (
                    <button onClick={() => void applyAction("withdraw")}>
                      Отозвать с проверки
                    </button>
                  )}
                  {admin &&
                    !selected.archived &&
                    ["draft", "submitted"].includes(selected.status) && (
                      <button onClick={() => void check()}>
                        Проверить перед выдачей
                      </button>
                    )}
                  {admin &&
                    selected.status === "submitted" &&
                    !selected.archived && (
                      <button onClick={() => void applyAction("return")}>
                        Вернуть на исправление
                      </button>
                    )}
                  {admin &&
                    !selected.archived &&
                    ["draft", "submitted", "returned"].includes(
                      selected.status,
                    ) && (
                      <button onClick={() => void applyAction("cancel")}>
                        Отменить подготовку
                      </button>
                    )}
                  {admin && selected.status === "issued" && (
                    <button onClick={() => void applyAction("revoke")}>
                      Отозвать документ
                    </button>
                  )}
                  {admin &&
                    ["issued", "revoked", "cancelled"].includes(
                      selected.status,
                    ) && (
                      <button
                        onClick={() =>
                          void applyAction(
                            selected.archived ? "unarchive" : "archive",
                          )
                        }
                      >
                        {selected.archived ? "Вернуть из архива" : "В архив"}
                      </button>
                    )}
                  {admin && ["issued", "revoked"].includes(selected.status) && (
                    <button onClick={() => void check(true)}>
                      Проверить доступ получателей
                    </button>
                  )}
                  <button
                    onClick={() => void showHistory("document", selected.id)}
                  >
                    История документа
                  </button>
                </div>
              )}
              {admin && (
                <details>
                  <summary>Ответственный и доступ</summary>
                  <p>
                    Назначение даёт сотруднику доступ к тексту и всей истории
                    этого документа. Выдача и отзыв остаются у администрации.
                  </p>
                  <label>
                    Ответственный
                    <select
                      value={selected.assigneeId || ""}
                      onChange={(e) => {
                        if (!canLeave()) return;
                        const assigneeId = e.target.value,
                          reason = window.prompt(
                            "Причина назначения или снятия доступа",
                          );
                        if (reason)
                          void run(async () => {
                            await mutate(
                              `items/${selected.id}/assignee`,
                              { version: selected.version, assigneeId, reason },
                              "PUT",
                            );
                            await changed(selected.id);
                          });
                      }}
                    >
                      <option value="">Только администрация</option>
                      {options?.employees.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                      {selected.assigneeId &&
                        !options?.employees.some(
                          (p) => p.id === selected.assigneeId,
                        ) && (
                          <option value={selected.assigneeId}>
                            Прежний ответственный
                          </option>
                        )}
                    </select>
                  </label>
                  {selected.recipients && (
                    <p>
                      Получатели при последней выдаче доступа:{" "}
                      {selected.recipients.map((p) => p.name).join(", ") ||
                        "не назначены"}
                      . Текущие школьные связи проверяются при каждом открытии.
                    </p>
                  )}
                </details>
              )}
              {preview && (
                <section className="document-preview">
                  <h3>
                    {preview.recipientsOnly
                      ? "Обновление доступа"
                      : "Проверка перед выдачей"}
                  </h3>
                  {preview.snapshot && (
                    <DocumentPaper snapshot={preview.snapshot} />
                  )}
                  <p>
                    Доступ получат:{" "}
                    {preview.recipients.map((p) => p.name).join(", ") ||
                      "нет личных получателей"}
                    .
                  </p>
                  {preview.recipientsOnly ? (
                    <p>
                      Прежний список:{" "}
                      {preview.previousRecipients
                        ?.map((p) => p.name)
                        .join(", ") || "пусто"}
                      . Новый список заменит прежний. Текст документа не
                      меняется.
                    </p>
                  ) : (
                    <p>
                      После выдачи текст и номер изменить нельзя. Проверьте
                      содержание и получателей; для исправления потребуется
                      новый документ.
                    </p>
                  )}
                  <button
                    onClick={() =>
                      void applyAction(
                        preview.recipientsOnly ? "recipients" : "issue",
                        preview,
                      )
                    }
                  >
                    {preview.recipientsOnly
                      ? "Обновить доступ"
                      : "Выдать документ"}
                  </button>{" "}
                  <button onClick={() => setPreview(null)}>
                    Отмена проверки
                  </button>
                </section>
              )}
            </section>
          )}
        </div>
        {history && (
          <section className="document-history">
            <h3>История изменений</h3>
            {history.items.map((h) => (
              <article key={h.seq}>
                <strong>{actions[h.action] || h.action}</strong>
                <p>
                  {displayTime(h.created)} · {h.actor}
                </p>
                {h.reason && <p>{h.reason}</p>}
                <details>
                  <summary>Сохранённое содержание</summary>
                  {h.document.snapshot ? (
                    <DocumentPaper snapshot={h.document.snapshot} />
                  ) : (
                    <>
                      <p>
                        {h.document.definition?.title || h.document.title} ·{" "}
                        {h.document.status
                          ? statuses[h.document.status]
                          : h.document.active
                            ? "Шаблон включён"
                            : "Шаблон отключён"}
                      </p>
                      {h.document.definition && (
                        <pre>{h.document.definition.text}</pre>
                      )}
                      {h.document.definition?.fields.map((f) => (
                        <p key={f.key}>
                          <strong>{f.label}: </strong>
                          {h.document.values?.[f.key] || "Не заполнено"}
                        </p>
                      ))}
                    </>
                  )}
                  {h.document.recipients && (
                    <p>
                      Получатели:{" "}
                      {h.document.recipients.map((p) => p.name).join(", ") ||
                        "нет"}
                      .
                    </p>
                  )}
                </details>
              </article>
            ))}
            {history.nextBefore && (
              <button
                onClick={() =>
                  void showHistory(history.entity, history.id, true)
                }
              >
                Ранее
              </button>
            )}{" "}
            <button onClick={() => setHistory(null)}>Скрыть историю</button>
          </section>
        )}
      </fieldset>
      {busy && <p role="status">Загружаю или сохраняю документы…</p>}
    </dialog>
  );
}

function DocumentPaper({ snapshot }: { snapshot: Snapshot }) {
  return (
    <article className="document-paper">
      <p>{snapshot.schoolName}</p>
      <h4>{snapshot.title}</h4>
      <p>
        № {snapshot.number} · {snapshot.date}
      </p>
      <pre>{snapshot.text}</pre>
    </article>
  );
}

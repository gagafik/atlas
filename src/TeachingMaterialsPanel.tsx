import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { MaterialView } from "./LessonMaterials";
import {
  materialFileUrl,
  materialSnapshot,
  type MaterialContent,
  type MaterialDetail,
  type LibraryItem,
  type MaterialSnapshot,
  type TeachingFile,
} from "./teachingMaterials";
import "./teachingMaterials.css";

const blank = (): MaterialContent => ({
  title: "",
  subject: "",
  topics: [],
  text: "",
  fileIds: [],
});
interface HistoryItem {
  version: number;
  created: string;
  actor: string;
}

export default function TeachingMaterialsPanel({
  workspace,
  onChoose,
}: {
  workspace: string;
  onChoose?: (item: MaterialSnapshot) => void;
}) {
  const [items, setItems] = useState<LibraryItem[]>([]),
    [more, setMore] = useState(false),
    [offset, setOffset] = useState(0);
  const [query, setQuery] = useState(""),
    [search, setSearch] = useState(""),
    [mine, setMine] = useState(false),
    [archived, setArchived] = useState(false);
  const [epoch, setEpoch] = useState(0),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<MaterialDetail | null>(null),
    [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<MaterialContent>(blank),
    [topics, setTopics] = useState(""),
    [visibility, setVisibility] = useState<"private" | "staff">("private");
  const [uploads, setUploads] = useState<TeachingFile[]>([]),
    [dirty, setDirty] = useState(false);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [history, setHistory] = useState<HistoryItem[]>([]),
    [historyMore, setHistoryMore] = useState(false);
  const lock = useRef(false),
    dirtyRef = useRef(false),
    alive = useRef(true),
    requestId = useRef(0),
    uploadAbort = useRef<AbortController | null>(null);
  const detailPanel = useRef<HTMLElement | null>(null);
  const editable =
    !onChoose &&
    (creating ||
      (!!selected?.canEdit &&
        !selected.archived &&
        selected.version === selected.latestVersion));
  dirtyRef.current = dirty;

  function canLeave() {
    if (lock.current) return false;
    return (
      !dirtyRef.current ||
      window.confirm("Есть несохранённые изменения материала. Отбросить их?")
    );
  }
  useEffect(() => {
    alive.current = true;
    const leave = (event: Event) => {
      if (!canLeave()) event.preventDefault();
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current || lock.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("atlas-homework-leave", leave);
    window.addEventListener("beforeunload", unload);
    return () => {
      alive.current = false;
      requestId.current++;
      uploadAbort.current?.abort();
      window.removeEventListener("atlas-homework-leave", leave);
      window.removeEventListener("beforeunload", unload);
    };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      q: search,
      mine: String(mine),
      archived: String(archived),
      offset: String(offset),
    });
    api(`journal/library?${params}`, workspace)
      .then((result) => {
        if (active) {
          setItems(result.items);
          setMore(result.hasMore);
        }
      })
      .catch((e) => {
        if (active) {
          setItems([]);
          setMore(false);
          setError(e.message);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [workspace, search, mine, archived, offset, epoch]);

  function install(item: MaterialDetail) {
    setSelected(item);
    setCreating(false);
    setDraft(item.content);
    setTopics(item.content.topics.join(", "));
    setVisibility(item.visibility);
    setUploads(item.files);
    setDirty(false);
    dirtyRef.current = false;
  }
  async function open(id: string, version?: number) {
    if (!canLeave()) return;
    const token = ++requestId.current;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const item = await api(
        `journal/library/${id}${version ? `?version=${version}` : ""}`,
        workspace,
      );
      if (alive.current && token === requestId.current) {
        install(item);
        setHistory([]);
        setHistoryMore(false);
        requestAnimationFrame(() => detailPanel.current?.focus());
      }
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  function change(values: Partial<MaterialContent>) {
    setDraft((previous) => ({ ...previous, ...values }));
    setDirty(true);
    dirtyRef.current = true;
  }
  function startNew() {
    if (!canLeave()) return;
    setSelected(null);
    setCreating(true);
    setDraft(blank());
    setTopics("");
    setVisibility("private");
    setUploads([]);
    setDirty(false);
    dirtyRef.current = false;
    setError("");
    setNotice("");
    setHistory([]);
    requestAnimationFrame(() => detailPanel.current?.focus());
  }
  async function save() {
    if (lock.current || !editable) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const content = {
        ...draft,
        topics: topics
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      };
      const result = await api(
        `journal/library${selected ? `/${selected.id}` : ""}`,
        workspace,
        selected ? "PUT" : "POST",
        {
          version: selected?.latestVersion || 0,
          content,
          visibility,
        },
      );
      if (alive.current) {
        install(result);
        setHistory([]);
        setEpoch((e) => e + 1);
        setNotice(
          `Сохранена версия ${result.version}. Материалы опубликованных уроков остались прежними.`,
        );
      }
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function action(kind: "copy" | "archive") {
    if (!selected || !canLeave()) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const item = await api(
        `journal/library/${selected.id}/${kind}`,
        workspace,
        "POST",
        kind === "copy"
          ? { version: selected.version }
          : { version: selected.latestVersion, archived: !selected.archived },
      );
      if (alive.current) {
        install(item);
        setHistory([]);
        setEpoch((e) => e + 1);
        setNotice(
          kind === "copy"
            ? "Создана личная копия выбранной версии."
            : item.archived
              ? "Материал в архиве. Версии в уроках сохранены."
              : "Материал восстановлен из архива.",
        );
      }
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function loadHistory(before?: number) {
    if (!selected || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api(
        `journal/library/${selected.id}/history${before ? `?before=${before}` : ""}`,
        workspace,
      );
      if (alive.current) {
        setHistory((previous) =>
          before ? [...previous, ...result.items] : result.items,
        );
        setHistoryMore(result.hasMore);
      }
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function upload(files: FileList | null) {
    if (!files?.length || lock.current || !editable) return;
    const batch = Array.from(files);
    if (
      batch.length + draft.fileIds.length > 5 ||
      batch.some((f) => !f.size || f.size > 10 * 1024 * 1024)
    ) {
      setError("Можно приложить до пяти непустых файлов, каждый до 10 МБ.");
      return;
    }
    lock.current = true;
    setBusy(true);
    setError("");
    const controller = new AbortController();
    uploadAbort.current = controller;
    try {
      for (const file of batch) {
        const params = new URLSearchParams({ workspace, filename: file.name });
        const response = await fetch(`/api/journal/material-files?${params}`, {
          method: "POST",
          headers: {
            "X-Atlas-Request": "1",
            "Content-Type": "application/octet-stream",
          },
          body: file,
          signal: controller.signal,
        });
        const result = await response.json();
        if (!response.ok)
          throw new Error(
            typeof result.detail === "string"
              ? result.detail
              : "Не удалось загрузить файл.",
          );
        if (alive.current) {
          setUploads((previous) => [...previous, result]);
          setDraft((previous) => ({
            ...previous,
            fileIds: [...previous.fileIds, result.id],
          }));
          setDirty(true);
          dirtyRef.current = true;
        }
      }
    } catch (e: any) {
      if (alive.current && e.name !== "AbortError") setError(e.message);
    } finally {
      lock.current = false;
      uploadAbort.current = null;
      if (alive.current) setBusy(false);
    }
  }

  return (
    <section
      className="teaching-library"
      aria-label={
        onChoose
          ? "Выбор материала из библиотеки"
          : "Библиотека учебных материалов"
      }
    >
      <header className="teaching-library-heading">
        <div>
          <h2>
            {onChoose ? "Выбор из библиотеки" : "Библиотека учебных материалов"}
          </h2>
          <p>
            Заготовки по предметам и темам. У каждого изменения своя версия.
          </p>
        </div>
        {!onChoose && (
          <button className="primary" disabled={busy} onClick={startNew}>
            Новый материал
          </button>
        )}
      </header>
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
      <div className="teaching-library-layout">
        <aside className="teaching-library-list">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setSearch(query);
              setOffset(0);
            }}
            className="teaching-library-search"
          >
            <label className="field">
              <span>Поиск по названию, предмету, теме или тексту</span>
              <input
                value={query}
                maxLength={200}
                onChange={(e) => setQuery(e.target.value)}
                type="search"
              />
            </label>
            <button type="submit">Найти</button>
          </form>
          <div className="teaching-library-filters">
            <label>
              <input
                type="checkbox"
                checked={mine}
                onChange={(e) => {
                  setMine(e.target.checked);
                  setOffset(0);
                }}
              />{" "}
              Только мои
            </label>
            {!onChoose && (
              <label>
                <input
                  type="checkbox"
                  checked={archived}
                  onChange={(e) => {
                    setArchived(e.target.checked);
                    setOffset(0);
                  }}
                />{" "}
                Архив
              </label>
            )}
            <button disabled={loading} onClick={() => setEpoch((e) => e + 1)}>
              Обновить список
            </button>
          </div>
          {loading ? (
            <p role="status">Загружаем материалы…</p>
          ) : !items.length ? (
            <p className="empty-state">
              Материалы не найдены.
              {!onChoose && " Создайте заготовку или измените поиск."}
            </p>
          ) : (
            <ul className="teaching-library-results">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    disabled={busy}
                    aria-pressed={selected?.id === item.id}
                    onClick={() => void open(item.id)}
                  >
                    <strong>{item.title}</strong>
                    <span>
                      {[item.subject, ...item.topics]
                        .filter(Boolean)
                        .join(" · ") || "Без темы"}
                    </span>
                    <small>
                      {item.owner} · версия {item.version} ·{" "}
                      {item.archived
                        ? "В архиве"
                        : item.visibility === "staff"
                          ? "Для учителей"
                          : "Личный"}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <nav
            className="teaching-library-pagination"
            aria-label="Страницы материалов"
          >
            <button
              disabled={!offset || loading}
              onClick={() => setOffset((n) => Math.max(0, n - 20))}
            >
              Назад
            </button>
            <span>Страница {offset / 20 + 1}</span>
            <button
              disabled={!more || loading}
              onClick={() => setOffset((n) => n + 20)}
            >
              Далее
            </button>
          </nav>
        </aside>
        <section
          className="teaching-library-detail"
          tabIndex={-1}
          ref={detailPanel}
          aria-label="Карточка материала"
        >
          {!creating && !selected ? (
            <p className="empty-state">
              {onChoose
                ? "Откройте материал, проверьте содержание и выберите версию для урока."
                : "Откройте материал из списка или создайте новый."}
            </p>
          ) : (
            <>
              <h3>
                {creating
                  ? "Новый материал"
                  : `${selected!.content.title} · версия ${selected!.version}`}
              </h3>
              {selected && (
                <p className="muted">
                  Автор: {selected.owner}. Эта версия:{" "}
                  {new Date(selected.created).toLocaleString("ru-RU")} ·{" "}
                  {selected.actor}.
                  {selected.version !== selected.latestVersion &&
                    ` Доступна новая версия ${selected.latestVersion}.`}
                </p>
              )}
              {selected?.archived && (
                <p className="info-line">
                  В архиве. Новые привязки к урокам недоступны. Прежние уроки
                  сохраняют выбранные версии.
                </p>
              )}
              {editable ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void save();
                  }}
                >
                  <fieldset disabled={busy}>
                    <label className="field">
                      <span>Название материала</span>
                      <input
                        required
                        maxLength={200}
                        value={draft.title}
                        onChange={(e) => change({ title: e.target.value })}
                      />
                    </label>
                    <div className="teaching-library-fields">
                      <label className="field">
                        <span>Предмет материала</span>
                        <input
                          maxLength={160}
                          value={draft.subject}
                          onChange={(e) => change({ subject: e.target.value })}
                        />
                      </label>
                      <label className="field">
                        <span>Темы через запятую</span>
                        <input
                          maxLength={2019}
                          value={topics}
                          onChange={(e) => {
                            setTopics(e.target.value);
                            setDirty(true);
                            dirtyRef.current = true;
                          }}
                        />
                      </label>
                    </div>
                    <label className="field">
                      <span>Содержание материала</span>
                      <textarea
                        rows={9}
                        maxLength={20000}
                        value={draft.text}
                        onChange={(e) => change({ text: e.target.value })}
                      />
                    </label>
                    <label className="field">
                      <span>Кто может использовать заготовку</span>
                      <select
                        value={visibility}
                        onChange={(e) => {
                          setVisibility(e.target.value as "private" | "staff");
                          setDirty(true);
                          dirtyRef.current = true;
                        }}
                      >
                        <option value="private">
                          Личный: автор и администратор
                        </option>
                        <option value="staff">
                          Все учителя этого пространства
                        </option>
                      </select>
                    </label>
                    <p className="muted">
                      Учителя могут читать общую заготовку, прикреплять версии к
                      урокам и создавать личные копии. Редактируют автор и
                      администратор. Прежние личные версии остаются закрытыми.
                      Смена доступа не отзывает уже созданные копии и материалы
                      в уроках.
                    </p>
                    <label className="field">
                      <span>Файлы материала (до пяти по 10 МБ)</span>
                      <input
                        type="file"
                        multiple
                        disabled={draft.fileIds.length >= 5}
                        accept=".pdf,.png,.jpg,.jpeg,.txt,.docx,.xlsx,.pptx"
                        onChange={(e) => {
                          void upload(e.target.files);
                          e.target.value = "";
                        }}
                      />
                    </label>
                    <ul className="teaching-library-files">
                      {uploads
                        .filter((f) => draft.fileIds.includes(f.id))
                        .map((file) => (
                          <li key={file.id}>
                            <a
                              href={materialFileUrl(
                                workspace,
                                file.id,
                                selected?.content.fileIds.includes(file.id)
                                  ? selected
                                  : undefined,
                              )}
                            >
                              {file.filename}
                            </a>
                            <button
                              type="button"
                              onClick={() =>
                                change({
                                  fileIds: draft.fileIds.filter(
                                    (id) => id !== file.id,
                                  ),
                                })
                              }
                            >
                              Убрать {file.filename}
                            </button>
                          </li>
                        ))}
                    </ul>
                    <div className="teaching-library-actions">
                      <button
                        className="primary"
                        type="submit"
                        disabled={!dirty && !creating}
                      >
                        {busy ? "Сохраняем…" : "Сохранить версию"}
                      </button>
                      {selected && (
                        <button
                          type="button"
                          disabled={!dirty}
                          onClick={() => {
                            if (canLeave()) install(selected);
                          }}
                        >
                          Отменить изменения
                        </button>
                      )}
                    </div>
                  </fieldset>
                </form>
              ) : (
                selected && (
                  <MaterialView
                    item={materialSnapshot(selected)}
                    workspace={workspace}
                  />
                )
              )}
              {selected && (
                <div className="teaching-library-actions">
                  {onChoose && (
                    <button
                      className="primary"
                      disabled={busy || selected.archived}
                      onClick={() => onChoose(materialSnapshot(selected))}
                    >
                      Прикрепить версию {selected.version} к уроку
                    </button>
                  )}
                  {!onChoose && (
                    <button disabled={busy} onClick={() => void action("copy")}>
                      Создать личную копию
                    </button>
                  )}
                  {!onChoose && selected.canEdit && (
                    <button
                      disabled={busy}
                      onClick={() => void action("archive")}
                    >
                      {selected.archived ? "Восстановить из архива" : "В архив"}
                    </button>
                  )}
                  <button disabled={busy} onClick={() => void loadHistory()}>
                    История версий
                  </button>
                  {selected.version !== selected.latestVersion && (
                    <button
                      disabled={busy}
                      onClick={() => void open(selected.id)}
                    >
                      Открыть актуальную версию
                    </button>
                  )}
                </div>
              )}
              {!!history.length && (
                <section
                  className="teaching-library-history"
                  aria-label="История версий материала"
                >
                  <h4>Сохранённые версии</h4>
                  <ul>
                    {history.map((item) => (
                      <li key={item.version}>
                        <button
                          disabled={busy}
                          onClick={() =>
                            selected && void open(selected.id, item.version)
                          }
                        >
                          Версия {item.version}
                        </button>{" "}
                        {new Date(item.created).toLocaleString("ru-RU")} ·{" "}
                        {item.actor}
                      </li>
                    ))}
                  </ul>
                  {historyMore && (
                    <button
                      disabled={busy}
                      onClick={() => void loadHistory(history.at(-1)?.version)}
                    >
                      Более ранние версии
                    </button>
                  )}
                </section>
              )}
            </>
          )}
        </section>
      </div>
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import "./SchoolSite.css";

type Block = { kind: "text" | "heading" | "link"; text: string; url: string };
type Photo = { id: string; alt: string; caption: string };
type Content = {
  kind: string;
  slug: string;
  title: string;
  summary: string;
  blocks: Block[];
  photos: Photo[];
  startsAt: string | null;
  endsAt: string | null;
  place: string;
};
type ConfigData = {
  title: string;
  intro: string;
  contact: string;
  footer: string;
  menuIds: string[];
  indexable: boolean;
};
type Config = {
  version: number;
  data: ConfigData;
  published: ConfigData | null;
  enabled: boolean;
};
type Entry = {
  id: string;
  version: number;
  data: Content;
  published: Content | null;
  archived: boolean;
  publishedAt: string | null;
};
type Summary = {
  id: string;
  title: string;
  kind: string;
  slug: string;
  hasPublished: boolean;
  archived: boolean;
};
type Options = {
  menuItems: { id: string; title: string }[];
  canEdit: boolean;
  canPublish: boolean;
  url: string;
  config: Config | null;
  staff: { id: string; name: string; role: string }[];
  assignments: { id: string; current: boolean }[];
};
type History = {
  id: number;
  created: string;
  actor: string;
  action: string;
  reason: string;
  data: any;
};
const kinds: Record<string, string> = {
  page: "Страница",
  news: "Новость",
  event: "Событие",
  gallery: "Галерея",
};
const actions: Record<string, string> = {
  save: "Черновик сохранён",
  save_config: "Настройки сохранены",
  access: "Редакторы изменены",
  publish: "Опубликовано",
  withdraw: "Публикация снята",
  archive: "В архиве",
  restore: "Возвращено из архива",
  restore_revision: "Прежняя редакция восстановлена в черновик",
};
const empty = (): Content => ({
  kind: "page",
  slug: "",
  title: "",
  summary: "",
  blocks: [],
  photos: [],
  startsAt: null,
  endsAt: null,
  place: "",
});
const copy = <T,>(data: T): T => structuredClone(data);
const localTime = (value: string | null) =>
  value
    ? new Date(new Date(value).getTime() + 5 * 3600000)
        .toISOString()
        .slice(0, 16)
    : "";

export default function SchoolSite({
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
  const [tab, setTab] = useState("items"),
    [rows, setRows] = useState<Summary[]>([]),
    [more, setMore] = useState(false),
    [offset, setOffset] = useState(0),
    [status, setStatus] = useState("active"),
    [query, setQuery] = useState(""),
    [applied, setApplied] = useState("");
  const [entry, setEntry] = useState<Entry | null>(null),
    [draft, setDraft] = useState<Content | null>(null),
    [configDraft, setConfigDraft] = useState<ConfigData | null>(null),
    [assigned, setAssigned] = useState<string[]>([]),
    [baseline, setBaseline] = useState("");
  const [photos, setPhotos] = useState<
      { id: string; linked: boolean; size: number }[]
    >([]),
    [history, setHistory] = useState<History[]>([]),
    [historyMore, setHistoryMore] = useState(false),
    [historyOffset, setHistoryOffset] = useState(0),
    [historyOpen, setHistoryOpen] = useState(false);
  const [preview, setPreview] = useState<{
      html: string;
      token: string;
      version: number;
      id: string;
    } | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [reason, setReason] = useState("");
  const dialog = useRef<HTMLDialogElement>(null),
    pending = useRef(new Map<string, string>());
  const current =
    tab === "config" ? configDraft : tab === "access" ? assigned : draft;
  const dirty = current !== null && JSON.stringify(current) !== baseline;
  const leave = () =>
    !busy &&
    (!dirty ||
      window.confirm("Есть несохранённые изменения. Уйти без сохранения?"));
  const clearPrivate = () => {
    setOptions(null);
    setEntry(null);
    setDraft(null);
    setRows([]);
    setConfigDraft(null);
    setPhotos([]);
    setHistory([]);
    setAssigned([]);
    setPreview(null);
    setBaseline("");
    pending.current.clear();
  };
  const run = async (work: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e: any) {
      if ([401, 403, 404].includes(e.status)) clearPrivate();
      setError(e.message || "Не удалось выполнить действие.");
    } finally {
      setBusy(false);
    }
  };
  const request = async (part: string, method = "GET", data?: any) => {
    if (data === undefined)
      return api("school-site/" + part, workspace, method);
    const key = JSON.stringify([workspace, part, method, data]);
    const requestId = pending.current.get(key) || crypto.randomUUID();
    pending.current.set(key, requestId);
    const result = await api("school-site/" + part, workspace, method, {
      ...data,
      requestId,
    });
    pending.current.delete(key);
    return result;
  };
  const loadOptions = async () => {
    const value: Options = await request("options");
    setOptions(value);
    return value;
  };
  const list = async (start = offset, filter = status, q = applied) => {
    const result = await request(
      `items?offset=${start}&status=${filter}&q=${encodeURIComponent(q)}`,
    );
    setRows(result.items);
    setMore(result.hasMore);
    setOffset(start);
  };
  const loadPhotos = async (id: string) =>
    setPhotos((await request("items/" + id + "/photos")).items);
  const choose = (r: Entry | null) => {
    const data = r ? copy(r.data) : empty();
    setEntry(r);
    setDraft(data);
    setBaseline(JSON.stringify(data));
    setPreview(null);
    setHistory([]);
    setHistoryOpen(false);
    setPhotos([]);
    setReason("");
  };
  const open = (id: string) => {
    if (!leave()) return;
    void run(async () => {
      const r = await request("items/" + id);
      choose(r);
      await loadPhotos(id);
    });
  };
  const refresh = async () => {
    setTab("items");
    setDraft(null);
    setEntry(null);
    setConfigDraft(null);
    setAssigned([]);
    setPreview(null);
    setHistoryOpen(false);
    setBaseline("");
    setStatus("active");
    setQuery("");
    setApplied("");
    const o = await loadOptions();
    if (o.canEdit) await list(0, "active", "");
    else {
      setRows([]);
      setDraft(null);
    }
  };
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    clearPrivate();
    setTab("items");
    setQuery("");
    setApplied("");
    setStatus("active");
    setOffset(0);
    void run(refresh);
  }, [workspace]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty || busy) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);
  const changeTab = (next: string) => {
    if (!leave()) return;
    setTab(next);
    setPreview(null);
    setHistoryOpen(false);
    setReason("");
    if (next === "config") {
      setConfigDraft(copy(options!.config!.data));
      setBaseline(JSON.stringify(options!.config!.data));
    } else if (next === "access") {
      const ids = options!.assignments
        .filter((a) => a.current)
        .map((a) => a.id);
      setAssigned(ids);
      setBaseline(JSON.stringify(ids));
    } else choose(null);
    if (next === "items") setDraft(null);
  };
  const loadHistory = async (start = 0) => {
    const h = await request(
      `history?id=${tab === "items" ? entry?.id || "" : ""}&offset=${start}`,
    );
    setHistory(h.items);
    setHistoryMore(h.hasMore);
    setHistoryOffset(start);
    setHistoryOpen(true);
  };
  const save = () =>
    void run(async () => {
      if (tab === "config") {
        await request("config", "PUT", {
          version: options!.config!.version,
          data: configDraft,
          reason,
        });
        const o = await loadOptions();
        setConfigDraft(copy(o.config!.data));
        setBaseline(JSON.stringify(o.config!.data));
      } else if (tab === "access") {
        await request("access", "PUT", {
          version: options!.config!.version,
          accountIds: assigned,
          reason,
        });
        await loadOptions();
        setBaseline(JSON.stringify(assigned));
      } else {
        const r = await request(
          entry ? "items/" + entry.id : "items",
          entry ? "PUT" : "POST",
          { version: entry?.version || 0, data: draft, reason },
        );
        choose(r);
        await loadPhotos(r.id);
        await list();
      }
      setPreview(null);
      setReason("");
      setNotice(
        "Сохранено. Публичная версия изменится только после публикации.",
      );
    });
  const showPreview = () =>
    void run(async () => {
      const id = tab === "config" ? "site" : entry!.id;
      const r = await api("school-site/preview/" + id, workspace, "POST", {
        version: tab === "config" ? options!.config!.version : entry!.version,
      });
      setPreview({ ...r, id });
      setConfirmed(false);
    });
  const act = (action: string, historyId?: number) => {
    if (dirty) {
      setError("Сначала сохраните изменения или откройте материал заново.");
      return;
    }
    if (
      action !== "publish" &&
      !window.confirm(
        action === "restore_revision"
          ? "Перенести эту редакцию в черновик? Опубликованная версия сохранится."
          : action === "withdraw"
            ? "Снять публикацию? Посетители потеряют доступ к странице и её фотографиям."
            : "Выполнить действие: " + actions[action] + "?",
      )
    )
      return;
    void run(async () => {
      const id = tab === "config" ? "site" : entry!.id;
      const r = await request("action/" + id, "POST", {
        version: tab === "config" ? options!.config!.version : entry!.version,
        action,
        token: preview?.token || "",
        confirmedPublic: confirmed,
        historyId,
        reason,
      });
      if (id === "site") {
        const o = await loadOptions();
        setConfigDraft(copy(o.config!.data));
        setBaseline(JSON.stringify(o.config!.data));
      } else {
        choose(r);
        await loadPhotos(r.id);
        await list();
      }
      setPreview(null);
      setConfirmed(false);
      setReason("");
      setNotice(actions[action] || "Готово");
    });
  };
  const upload = (file: File) =>
    void run(async () => {
      if (file.size > 10 * 1024 * 1024)
        throw new Error("Фото должно быть не больше 10 МБ.");
      const response = await fetch(
        `/api/school-site/items/${entry!.id}/photos?workspace=${workspace}&itemVersion=${entry!.version}`,
        {
          method: "POST",
          headers: { "X-Atlas-Request": "1", "Content-Type": file.type },
          body: file,
        },
      );
      const result = await response.json();
      if (!response.ok)
        throw Object.assign(
          new Error(
            typeof result.detail === "string"
              ? result.detail
              : "Не удалось загрузить фото.",
          ),
          { status: response.status },
        );
      await loadPhotos(entry!.id);
      setNotice(
        "Фото загружено. Добавьте его в материал и заполните описание, затем сохраните черновик.",
      );
    });
  const move = <T,>(items: T[], i: number, direction: number) => {
    const next = [...items],
      j = i + direction;
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  };
  const change = (key: keyof Content, value: any) =>
    setDraft((d) => d && { ...d, [key]: value });
  return (
    <dialog
      className="school-site-dialog"
      ref={dialog}
      onCancel={(e) => {
        e.preventDefault();
        if (leave()) onClose();
      }}
      aria-labelledby="school-site-title"
    >
      <header>
        <div>
          <small>Публичные материалы</small>
          <h2 id="school-site-title">Сайт школы</h2>
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
      <fieldset disabled={busy}>
        <div className="site-toolbar">
          <label>
            Пространство{" "}
            <select
              value={workspace}
              onChange={(e) => {
                if (leave()) setWorkspace(e.target.value);
              }}
            >
              {spaces.map((s) => (
                <option key={s} value={s}>
                  {s === "demo" ? "Демонстрация" : "Школа"}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              if (leave()) {
                setDraft(null);
                setBaseline("");
                void run(refresh);
              }
            }}
          >
            Обновить
          </button>
          {options?.config?.enabled && (
            <a href={options.url} target="_blank" rel="noreferrer">
              Открыть опубликованный сайт ↗
            </a>
          )}
        </div>
        <p role="status" aria-live="polite">
          {busy ? "Выполняю…" : notice}
        </p>
        {error && (
          <p className="site-error" role="alert">
            {error}
          </p>
        )}
        {options && !options.canEdit && (
          <p>
            Администратор должен назначить вас редактором сайта. Права выдаются
            отдельно для каждого пространства.
          </p>
        )}
        {options?.canEdit && (
          <>
            <nav className="site-tabs" aria-label="Управление сайтом">
              {[
                "items",
                ...(options.canPublish ? ["config", "access"] : []),
              ].map((t) => (
                <button
                  key={t}
                  aria-pressed={tab === t}
                  onClick={() => changeTab(t)}
                >
                  {
                    {
                      items: "Материалы",
                      config: "Сайт и меню",
                      access: "Редакторы",
                    }[t]
                  }
                </button>
              ))}
            </nav>
            {!options.config?.version && (
              <p className="site-note">
                Сначала сохраните название и настройки в разделе «Сайт и меню».
                Публикация сайта включается отдельно.
              </p>
            )}
            {tab === "items" && (
              <div className="site-layout">
                <aside>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (leave()) {
                        setApplied(query);
                        void run(() => list(0, status, query));
                      }
                    }}
                  >
                    <label>
                      Поиск материала
                      <input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        maxLength={100}
                      />
                    </label>
                    <label>
                      Показать
                      <select
                        value={status}
                        onChange={(e) => {
                          if (leave()) {
                            setStatus(e.target.value);
                            void run(() => list(0, e.target.value));
                          }
                        }}
                      >
                        <option value="active">Все действующие</option>
                        <option value="draft">Без публикации</option>
                        <option value="published">Опубликованные</option>
                        <option value="archived">Архив</option>
                      </select>
                    </label>
                    <button>Найти</button>
                  </form>
                  <button
                    disabled={!options.config?.version}
                    onClick={() => {
                      if (leave()) choose(null);
                    }}
                  >
                    Новый материал
                  </button>
                  <div className="site-list">
                    {rows.map((r) => (
                      <button
                        key={r.id}
                        aria-pressed={entry?.id === r.id}
                        onClick={() => open(r.id)}
                      >
                        <strong>{r.title}</strong>
                        <small>
                          {kinds[r.kind]} ·{" "}
                          {r.archived
                            ? "Архив"
                            : r.hasPublished
                              ? "Есть публикация"
                              : "Черновик"}
                        </small>
                      </button>
                    ))}
                  </div>
                  {!rows.length && <p>Материалов пока нет.</p>}
                  <div className="site-toolbar">
                    <button
                      disabled={!offset}
                      onClick={() =>
                        void run(() => list(Math.max(0, offset - 30)))
                      }
                    >
                      Назад
                    </button>
                    <button
                      disabled={!more}
                      onClick={() => void run(() => list(offset + 30))}
                    >
                      Далее
                    </button>
                  </div>
                </aside>
                <section>
                  {!draft ? (
                    <p>Выберите материал или создайте новый.</p>
                  ) : (
                    <>
                      <h3>{entry ? "Редактор материала" : "Новый материал"}</h3>
                      {entry?.published && (
                        <p className="site-note">
                          На сайте доступна опубликованная версия. Изменения
                          ниже сохраняются в отдельный черновик.{" "}
                          <a
                            href={`${options.url}/p/${entry.data.slug}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Открыть страницу ↗
                          </a>
                        </p>
                      )}
                      {entry?.archived ? (
                        <p>
                          Материал в архиве.{" "}
                          {options.canPublish && (
                            <button onClick={() => act("restore")}>
                              Вернуть в черновики
                            </button>
                          )}
                        </p>
                      ) : (
                        <form
                          onSubmit={(e) => {
                            e.preventDefault();
                            save();
                          }}
                        >
                          <label>
                            Тип
                            <select
                              value={draft.kind}
                              onChange={(e) =>
                                setDraft(
                                  (d) =>
                                    d && {
                                      ...d,
                                      kind: e.target.value,
                                      startsAt: null,
                                      endsAt: null,
                                      place: "",
                                    },
                                )
                              }
                            >
                              {Object.entries(kinds).map(([k, v]) => (
                                <option key={k} value={k}>
                                  {v}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            Заголовок
                            <input
                              value={draft.title}
                              onChange={(e) => change("title", e.target.value)}
                              required
                              maxLength={160}
                            />
                          </label>
                          <label>
                            Адрес страницы
                            <input
                              value={draft.slug}
                              onChange={(e) => change("slug", e.target.value)}
                              required
                              pattern="[a-z0-9][a-z0-9-]{0,79}"
                              maxLength={80}
                              disabled={!!entry}
                            />
                            <small>
                              Латинские строчные буквы, цифры и дефисы. После
                              создания адрес сохраняется.
                            </small>
                          </label>
                          <label>
                            Краткое описание
                            <textarea
                              value={draft.summary}
                              onChange={(e) =>
                                change("summary", e.target.value)
                              }
                              maxLength={600}
                            />
                          </label>
                          {draft.kind === "event" && (
                            <div className="site-note">
                              <p>Время школы, UTC+5</p>
                              {(["startsAt", "endsAt"] as const).map((k) => (
                                <label key={k}>
                                  {k === "startsAt" ? "Начало" : "Окончание"}
                                  <input
                                    type="datetime-local"
                                    required
                                    value={localTime(draft[k])}
                                    onChange={(e) =>
                                      change(
                                        k,
                                        e.target.value
                                          ? e.target.value + ":00+05:00"
                                          : null,
                                      )
                                    }
                                  />
                                </label>
                              ))}
                              <label>
                                Место
                                <input
                                  value={draft.place}
                                  onChange={(e) =>
                                    change("place", e.target.value)
                                  }
                                  maxLength={300}
                                />
                              </label>
                            </div>
                          )}
                          <h4>Содержание</h4>
                          {draft.blocks.map((b, i) => (
                            <div className="site-block" key={i}>
                              <label>
                                Блок {i + 1}
                                <select
                                  value={b.kind}
                                  onChange={(e) =>
                                    change(
                                      "blocks",
                                      draft.blocks.map((v, n) =>
                                        n === i
                                          ? {
                                              ...v,
                                              kind: e.target.value,
                                              url: "",
                                            }
                                          : v,
                                      ),
                                    )
                                  }
                                >
                                  <option value="text">Текст</option>
                                  <option value="heading">Подзаголовок</option>
                                  <option value="link">Ссылка</option>
                                </select>
                              </label>
                              <textarea
                                aria-label={`Текст блока ${i + 1}`}
                                required
                                maxLength={5000}
                                value={b.text}
                                onChange={(e) =>
                                  change(
                                    "blocks",
                                    draft.blocks.map((v, n) =>
                                      n === i
                                        ? { ...v, text: e.target.value }
                                        : v,
                                    ),
                                  )
                                }
                              />
                              {b.kind === "link" && (
                                <label>
                                  Адрес HTTPS
                                  <input
                                    type="url"
                                    required
                                    pattern="https://.*"
                                    maxLength={1500}
                                    value={b.url}
                                    onChange={(e) =>
                                      change(
                                        "blocks",
                                        draft.blocks.map((v, n) =>
                                          n === i
                                            ? { ...v, url: e.target.value }
                                            : v,
                                        ),
                                      )
                                    }
                                  />
                                </label>
                              )}
                              <div className="site-toolbar">
                                <button
                                  type="button"
                                  disabled={i === 0}
                                  onClick={() =>
                                    change("blocks", move(draft.blocks, i, -1))
                                  }
                                >
                                  Выше
                                </button>
                                <button
                                  type="button"
                                  disabled={i === draft.blocks.length - 1}
                                  onClick={() =>
                                    change("blocks", move(draft.blocks, i, 1))
                                  }
                                >
                                  Ниже
                                </button>
                                <button
                                  type="button"
                                  onClick={() =>
                                    change(
                                      "blocks",
                                      draft.blocks.filter((_, n) => n !== i),
                                    )
                                  }
                                >
                                  Удалить блок
                                </button>
                              </div>
                            </div>
                          ))}
                          <button
                            type="button"
                            disabled={draft.blocks.length >= 30}
                            onClick={() =>
                              change("blocks", [
                                ...draft.blocks,
                                { kind: "text", text: "", url: "" },
                              ])
                            }
                          >
                            Добавить блок
                          </button>
                          <h4>Фотографии</h4>
                          {!entry && (
                            <p>
                              Сначала сохраните материал, затем загрузите
                              фотографии.
                            </p>
                          )}
                          {draft.photos.map((p, i) => (
                            <div className="site-block site-photo" key={p.id}>
                              <img
                                src={`/api/school-site/photos/${p.id}?workspace=${workspace}`}
                                alt={p.alt || "Выбранное фото"}
                              />
                              <label>
                                Описание для доступности
                                <input
                                  required
                                  maxLength={300}
                                  value={p.alt}
                                  onChange={(e) =>
                                    change(
                                      "photos",
                                      draft.photos.map((v) =>
                                        v.id === p.id
                                          ? { ...v, alt: e.target.value }
                                          : v,
                                      ),
                                    )
                                  }
                                />
                              </label>
                              <label>
                                Подпись
                                <input
                                  maxLength={500}
                                  value={p.caption}
                                  onChange={(e) =>
                                    change(
                                      "photos",
                                      draft.photos.map((v) =>
                                        v.id === p.id
                                          ? { ...v, caption: e.target.value }
                                          : v,
                                      ),
                                    )
                                  }
                                />
                              </label>
                              <div className="site-toolbar">
                                <button
                                  type="button"
                                  disabled={!i}
                                  onClick={() =>
                                    change("photos", move(draft.photos, i, -1))
                                  }
                                >
                                  Выше
                                </button>
                                <button
                                  type="button"
                                  disabled={i === draft.photos.length - 1}
                                  onClick={() =>
                                    change("photos", move(draft.photos, i, 1))
                                  }
                                >
                                  Ниже
                                </button>
                                <button
                                  type="button"
                                  onClick={() =>
                                    change(
                                      "photos",
                                      draft.photos.filter((v) => v.id !== p.id),
                                    )
                                  }
                                >
                                  Убрать из материала
                                </button>
                              </div>
                            </div>
                          ))}
                          {entry && (
                            <>
                              <label>
                                Загрузить JPG или PNG
                                <input
                                  type="file"
                                  accept="image/jpeg,image/png"
                                  onChange={(e) => {
                                    const f = e.target.files?.[0];
                                    e.target.value = "";
                                    if (f) upload(f);
                                  }}
                                />
                                <small>
                                  До 10 МБ и 20 мегапикселей. Снимок будет
                                  уменьшен, исходные метаданные удалены.
                                </small>
                              </label>
                              <div className="site-photo-pool">
                                {photos
                                  .filter(
                                    (p) =>
                                      !draft.photos.some((v) => v.id === p.id),
                                  )
                                  .map((p) => (
                                    <div key={p.id}>
                                      <img
                                        src={`/api/school-site/photos/${p.id}?workspace=${workspace}`}
                                        alt="Загруженное фото"
                                      />
                                      <button
                                        type="button"
                                        disabled={draft.photos.length >= 12}
                                        onClick={() =>
                                          change("photos", [
                                            ...draft.photos,
                                            { id: p.id, alt: "", caption: "" },
                                          ])
                                        }
                                      >
                                        Добавить в материал
                                      </button>
                                      {!p.linked && (
                                        <button
                                          type="button"
                                          onClick={() => {
                                            if (
                                              window.confirm(
                                                "Удалить неиспользованное фото?",
                                              )
                                            )
                                              void run(async () => {
                                                await request(
                                                  "photos/" + p.id,
                                                  "DELETE",
                                                );
                                                await loadPhotos(entry.id);
                                              });
                                          }}
                                        >
                                          Удалить загрузку
                                        </button>
                                      )}
                                    </div>
                                  ))}
                              </div>
                            </>
                          )}
                          <label>
                            Комментарий к изменению
                            <input
                              value={reason}
                              onChange={(e) => setReason(e.target.value)}
                              maxLength={1000}
                            />
                          </label>
                          <button className="primary">
                            Сохранить черновик
                          </button>
                        </form>
                      )}
                      {entry && (
                        <div className="site-toolbar">
                          <button
                            disabled={dirty || entry.archived}
                            onClick={showPreview}
                          >
                            Предпросмотр
                          </button>
                          {options.canPublish && !entry.archived && (
                            <>
                              <button
                                disabled={dirty || !entry.published}
                                onClick={() => act("withdraw")}
                              >
                                Снять публикацию
                              </button>
                              <button
                                disabled={dirty}
                                onClick={() => act("archive")}
                              >
                                В архив
                              </button>
                            </>
                          )}
                          <button onClick={() => void run(() => loadHistory())}>
                            История
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </section>
              </div>
            )}
            {tab === "config" && configDraft && (
              <section className="site-config">
                <h3>Название, контакты и меню</h3>
                <p>
                  Статус:{" "}
                  {options.config?.enabled
                    ? "Сайт опубликован"
                    : "Сайт отключён для посетителей"}
                </p>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    save();
                  }}
                >
                  {(["title", "intro", "contact", "footer"] as const).map(
                    (k) => (
                      <label key={k}>
                        {
                          {
                            title: "Название сайта",
                            intro: "Вступление на главной",
                            contact: "Публичные контакты",
                            footer: "Подвал сайта",
                          }[k]
                        }
                        <textarea
                          required={k === "title"}
                          maxLength={
                            k === "title" ? 160 : k === "intro" ? 3000 : 1500
                          }
                          value={configDraft[k]}
                          onChange={(e) =>
                            setConfigDraft({
                              ...configDraft,
                              [k]: e.target.value,
                            })
                          }
                        />
                      </label>
                    ),
                  )}
                  <h4>Меню</h4>
                  <p>
                    Добавьте опубликованные материалы в нужном порядке. Сначала
                    опубликуйте сами страницы.
                  </p>
                  {configDraft.menuIds.map((id, i) => (
                    <div className="site-toolbar" key={id}>
                      <span>
                        {rows.find((r) => r.id === id)?.title ||
                          options.menuItems.find((r) => r.id === id)?.title ||
                          "Материал"}
                      </span>
                      <button
                        type="button"
                        disabled={!i}
                        onClick={() =>
                          setConfigDraft({
                            ...configDraft,
                            menuIds: move(configDraft.menuIds, i, -1),
                          })
                        }
                      >
                        Выше
                      </button>
                      <button
                        type="button"
                        disabled={i === configDraft.menuIds.length - 1}
                        onClick={() =>
                          setConfigDraft({
                            ...configDraft,
                            menuIds: move(configDraft.menuIds, i, 1),
                          })
                        }
                      >
                        Ниже
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setConfigDraft({
                            ...configDraft,
                            menuIds: configDraft.menuIds.filter(
                              (v) => v !== id,
                            ),
                          })
                        }
                      >
                        Убрать
                      </button>
                    </div>
                  ))}
                  <label>
                    Найти материал для меню
                    <input
                      value={query}
                      maxLength={100}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </label>
                  <div className="site-toolbar">
                    <button
                      type="button"
                      onClick={() => {
                        setApplied(query);
                        void run(() => list(0, "active", query));
                      }}
                    >
                      Найти страницы
                    </button>
                    <button
                      type="button"
                      disabled={!offset}
                      onClick={() =>
                        void run(() => list(Math.max(0, offset - 30), "active"))
                      }
                    >
                      Предыдущие страницы
                    </button>
                    <button
                      type="button"
                      disabled={!more}
                      onClick={() =>
                        void run(() => list(offset + 30, "active"))
                      }
                    >
                      Следующие страницы
                    </button>
                  </div>
                  <label>
                    Добавить страницу в меню
                    <select
                      value=""
                      disabled={configDraft.menuIds.length >= 12}
                      onChange={(e) => {
                        if (e.target.value)
                          setConfigDraft({
                            ...configDraft,
                            menuIds: [...configDraft.menuIds, e.target.value],
                          });
                      }}
                    >
                      <option value="">Выберите найденную страницу</option>
                      {rows
                        .filter(
                          (r) =>
                            !r.archived && !configDraft.menuIds.includes(r.id),
                        )
                        .map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.title}
                          </option>
                        ))}
                    </select>
                  </label>
                  <small>
                    В меню до 12 пунктов. Список кандидатов соответствует
                    последнему поиску в разделе материалов.
                  </small>
                  <label className="site-check">
                    <input
                      type="checkbox"
                      checked={configDraft.indexable}
                      onChange={(e) =>
                        setConfigDraft({
                          ...configDraft,
                          indexable: e.target.checked,
                        })
                      }
                    />
                    Разрешить поисковым системам индексировать опубликованный
                    сайт
                  </label>
                  <label>
                    Комментарий
                    <input
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      maxLength={1000}
                    />
                  </label>
                  <button className="primary">Сохранить настройки</button>
                </form>
                <div className="site-toolbar">
                  <button
                    disabled={dirty || !options.config?.version}
                    onClick={showPreview}
                  >
                    Предпросмотр сайта
                  </button>
                  <button
                    disabled={dirty || !options.config?.enabled}
                    onClick={() => act("withdraw")}
                  >
                    Отключить публичный сайт
                  </button>
                  <button onClick={() => void run(() => loadHistory())}>
                    История настроек
                  </button>
                </div>
              </section>
            )}
            {tab === "access" && (
              <section className="site-config">
                <h3>Редакторы сайта</h3>
                <p>
                  Назначенные сотрудники готовят материалы и фотографии.
                  Публикует и снимает их администратор.
                </p>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    save();
                  }}
                >
                  {options.staff
                    .filter((s) => s.role !== "admin")
                    .map((s) => (
                      <label className="site-check" key={s.id}>
                        <input
                          type="checkbox"
                          checked={assigned.includes(s.id)}
                          onChange={(e) =>
                            setAssigned(
                              e.target.checked
                                ? [...assigned, s.id]
                                : assigned.filter((id) => id !== s.id),
                            )
                          }
                        />
                        {s.name}
                      </label>
                    ))}
                  {options.assignments.some((a) => !a.current) && (
                    <p className="site-note">
                      Некоторые прежние назначения потеряли силу после изменения
                      доступа сотрудника. При необходимости назначьте его
                      заново.
                    </p>
                  )}
                  <button
                    className="primary"
                    disabled={!options.config?.version}
                  >
                    Сохранить редакторов
                  </button>
                </form>
              </section>
            )}
            {preview && (
              <section className="site-preview">
                <h3>Предпросмотр сохранённой редакции</h3>
                <iframe
                  title="Предпросмотр публичной страницы"
                  sandbox="allow-same-origin"
                  referrerPolicy="no-referrer"
                  srcDoc={preview.html}
                />
                {options.canPublish && (
                  <>
                    <label className="site-check">
                      <input
                        type="checkbox"
                        checked={confirmed}
                        onChange={(e) => setConfirmed(e.target.checked)}
                      />
                      Я проверил текст и фото: их можно открыть без входа в
                      аккаунт
                    </label>
                    <button
                      className="primary"
                      disabled={!confirmed || dirty}
                      onClick={() => act("publish")}
                    >
                      {preview.id === "site"
                        ? "Опубликовать сайт"
                        : "Опубликовать материал"}
                    </button>
                  </>
                )}
              </section>
            )}
            {historyOpen && (
              <section className="site-history">
                <h3>История изменений</h3>
                {history.map((h) => (
                  <article key={h.id}>
                    <strong>{actions[h.action] || h.action}</strong>
                    <p>
                      {new Date(h.created).toLocaleString("ru-RU")} · {h.actor}
                    </p>
                    {h.reason && <p>{h.reason}</p>}
                    <details>
                      <summary>Содержание редакции</summary>
                      {h.data.title && <h4>{h.data.title}</h4>}
                      {[
                        h.data.summary,
                        h.data.intro,
                        h.data.contact,
                        h.data.footer,
                      ]
                        .filter(Boolean)
                        .map((v, i) => (
                          <p className="site-text" key={i}>
                            {v}
                          </p>
                        ))}
                      {h.data.blocks?.map((b: Block, i: number) => (
                        <p className="site-text" key={i}>
                          {b.text}
                          {b.url && ` (${b.url})`}
                        </p>
                      ))}
                      {h.data.photos?.map((p: Photo) => (
                        <figure key={p.id}>
                          <img
                            src={`/api/school-site/photos/${p.id}?workspace=${workspace}`}
                            alt={p.alt}
                          />
                          <figcaption>{p.caption}</figcaption>
                        </figure>
                      ))}
                      {h.action === "access" && (
                        <p>Назначено редакторов: {h.data.accountIds.length}</p>
                      )}
                    </details>
                    {options.canPublish &&
                      tab === "items" &&
                      entry &&
                      !entry.archived &&
                      ["save", "publish", "restore_revision"].includes(
                        h.action,
                      ) && (
                        <button
                          disabled={dirty}
                          onClick={() => act("restore_revision", h.id)}
                        >
                          Восстановить в черновик
                        </button>
                      )}
                  </article>
                ))}
                {!history.length && <p>Записей пока нет.</p>}
                <div className="site-toolbar">
                  <button
                    disabled={!historyOffset}
                    onClick={() =>
                      void run(() =>
                        loadHistory(Math.max(0, historyOffset - 30)),
                      )
                    }
                  >
                    Предыдущие
                  </button>
                  <button
                    disabled={!historyMore}
                    onClick={() =>
                      void run(() => loadHistory(historyOffset + 30))
                    }
                  >
                    Более ранние
                  </button>
                </div>
              </section>
            )}
          </>
        )}
      </fieldset>
    </dialog>
  );
}

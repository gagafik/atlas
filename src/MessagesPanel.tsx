import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import MessageAttachments, { messageFileUrl } from "./MessageAttachments";
import type { MessageFile } from "./MessageAttachments";

type Person = { id: string; name: string; role: string };
type Options = {
  userId: string;
  role: string;
  canAnnounce: boolean;
  students: { id: string; name: string }[];
  groups: { id: string; name: string }[];
};
type Thread = {
  id: string;
  kind: string;
  title: string;
  version: number;
  status: string;
  requireAck: boolean;
  unread?: boolean;
  acknowledged: boolean;
  latestId: number;
  authorId: string;
};
type Post = {
  id: number;
  authorId: string;
  author: string;
  created: string;
  text: string;
  files: MessageFile[];
};
type Detail = Thread & {
  userId: string;
  canReply: boolean;
  canManage: boolean;
  posts: Post[];
  nextBefore: number | null;
  participants: {
    id: string;
    name: string;
    read: boolean;
    acknowledged: boolean;
  }[];
};
const roles: Record<string, string> = {
  admin: "Администратор",
  teacher: "Учитель",
  parent: "Родитель",
  student: "Ученик",
  editor: "Составитель",
  viewer: "Сотрудник",
};
const statuses: Record<string, string> = {
  open: "Открыто",
  closed: "Ответы закрыты",
  withdrawn: "Объявление отозвано",
};
const kinds: Record<string, string> = {
  direct: "Личный диалог",
  group: "Групповое обсуждение",
  announcement: "Объявление",
};

function useSearchDelay(value: string) {
  const [ready, setReady] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setReady(value), 300);
    return () => window.clearTimeout(timer);
  }, [value]);
  return ready;
}

export default function MessagesPanel({ workspace }: { workspace: string }) {
  const [options, setOptions] = useState<Options | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [threads, setThreads] = useState<Thread[]>([]),
    [next, setNext] = useState<number | null>(null),
    [query, setQuery] = useState(""),
    [unread, setUnread] = useState(false);
  const [selected, setSelected] = useState(""),
    [detail, setDetail] = useState<Detail | null>(null),
    [composing, setComposing] = useState(false);
  const [kind, setKind] = useState("direct"),
    [student, setStudent] = useState(""),
    [recipient, setRecipient] = useState(""),
    [contacts, setContacts] = useState<Person[]>([]);
  const [groupMode, setGroupMode] = useState("contacts"),
    [selectedPeople, setSelectedPeople] = useState<Person[]>([]);
  const [composeFiles, setComposeFiles] = useState<MessageFile[]>([]),
    [replyFiles, setReplyFiles] = useState<MessageFile[]>([]);
  const [composeDraft, setComposeDraft] = useState(() => crypto.randomUUID()),
    [replyDraft, setReplyDraft] = useState(() => crypto.randomUUID());
  const usingContacts =
    kind === "direct" || (kind === "group" && groupMode === "contacts");
  const [contactQuery, setContactQuery] = useState(""),
    [contactNext, setContactNext] = useState<number | null>(null);
  const [contactLoading, setContactLoading] = useState(false);
  const searchQuery = useSearchDelay(query),
    contactSearch = useSearchDelay(contactQuery);
  const [title, setTitle] = useState(""),
    [text, setText] = useState(""),
    [groups, setGroups] = useState<string[]>([]),
    [audience, setAudience] = useState(["parent", "student"]);
  const [schoolwide, setSchoolwide] = useState(false),
    [ack, setAck] = useState(false),
    [preview, setPreview] = useState<{
      count: number;
      byRole: Record<string, number>;
    } | null>(null);
  const [reply, setReply] = useState("");
  const nonce = useRef(crypto.randomUUID()),
    replyNonce = useRef(crypto.randomUUID()),
    alive = useRef(true);
  const form = JSON.stringify({
    kind,
    title,
    text,
    recipient,
    student,
    groups,
    audience,
    schoolwide,
    ack,
    groupMode,
    selectedPeople,
    composeFiles,
  });
  useEffect(() => {
    nonce.current = crypto.randomUUID();
    setPreview(null);
  }, [form]);
  useEffect(() => {
    replyNonce.current = crypto.randomUUID();
  }, [replyFiles]);
  useEffect(() => {
    setSelectedPeople([]);
  }, [student]);
  useEffect(() => {
    alive.current = true;
    let live = true;
    api("messages/options", workspace)
      .then((r) => {
        if (live) {
          setOptions(r);
          setStudent(r.students[0]?.id || "");
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
      alive.current = false;
    };
  }, [workspace]);
  useEffect(() => {
    if (!options) return;
    let live = true;
    api(
      `messages/threads?${new URLSearchParams({ q: searchQuery, unreadOnly: String(unread) })}`,
      workspace,
    )
      .then((r) => {
        if (live) {
          setThreads(r.items);
          setNext(r.nextBefore);
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [options, searchQuery, unread, workspace]);
  useEffect(() => {
    if (!options || !composing || !usingContacts) return;
    let live = true;
    setContactLoading(true);
    setContacts([]);
    setRecipient("");
    const params = new URLSearchParams({ q: contactSearch });
    if (student) params.set("studentId", student);
    api(`messages/contacts?${params}`, workspace)
      .then((r) => {
        if (live) {
          setContacts(r.items);
          setContactNext(r.nextOffset);
          setRecipient((old) =>
            r.items.some((p: Person) => p.id === old)
              ? old
              : r.items[0]?.id || "",
          );
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setContactLoading(false);
      });
    return () => {
      live = false;
    };
  }, [options, composing, student, usingContacts, contactSearch, workspace]);
  useEffect(() => {
    if (!reply && !text && !title && !composeFiles.length && !replyFiles.length)
      return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [reply, text, title, composeFiles, replyFiles]);
  async function run(fn: () => Promise<void>) {
    if (busy) return;
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
  async function list(before?: number) {
    const params = new URLSearchParams({
      q: query,
      unreadOnly: String(unread),
    });
    if (before) params.set("before", String(before));
    const r = await api(`messages/threads?${params}`, workspace);
    if (alive.current) {
      setThreads((old) => (before ? [...old, ...r.items] : r.items));
      setNext(r.nextBefore);
    }
  }
  async function open(id: string, before?: number) {
    const r: Detail = await api(
      `messages/threads/${id}${before ? `?before=${before}` : ""}`,
      workspace,
    );
    if (!alive.current) return;
    if (selected !== id) {
      setReply("");
      setReplyFiles([]);
      setReplyDraft(crypto.randomUUID());
    }
    setDetail((old) =>
      before && old ? { ...r, posts: [...r.posts, ...old.posts] } : r,
    );
    setSelected(id);
    setComposing(false);
    if (!before) {
      await api(`messages/threads/${id}/read`, workspace, "PUT", {
        throughId: r.latestId,
      });
      await list();
    }
  }
  function leave() {
    if (busy) return false;
    if (
      (reply || text || title || composeFiles.length || replyFiles.length) &&
      !window.confirm("Закрыть неотправленное сообщение и его вложения?")
    )
      return false;
    setReply("");
    setText("");
    setTitle("");
    setComposeFiles([]);
    setReplyFiles([]);
    setComposeDraft(crypto.randomUUID());
    setReplyDraft(crypto.randomUUID());
    setSelectedPeople([]);
    return true;
  }
  function body() {
    return {
      requestId: nonce.current,
      kind,
      title,
      text,
      draftId: composeDraft,
      fileIds: composeFiles.map((f) => f.id),
      ...(kind === "direct"
        ? { recipientId: recipient, studentId: student || null }
        : kind === "group" && usingContacts
          ? {
              recipientIds: selectedPeople.map((p) => p.id),
              studentId: student || null,
            }
          : {
              groupIds: kind === "announcement" && schoolwide ? [] : groups,
              roles:
                kind === "group"
                  ? audience.filter((r) => ["parent", "student"].includes(r))
                  : audience,
              schoolwide: kind === "announcement" && schoolwide,
              requireAck: kind === "announcement" && ack,
            }),
    };
  }
  async function change(action: string) {
    if (!detail) return;
    await api(`messages/threads/${detail.id}/change`, workspace, "POST", {
      version: detail.version,
      action,
    });
    await open(detail.id);
  }
  if (!options)
    return (
      <section className="messages-panel">
        <p role={error ? "alert" : "status"}>
          {error || "Загружаю сообщения…"}
        </p>
      </section>
    );
  return (
    <section className="messages-panel">
      <p className="muted">
        Личная переписка доступна её участникам. Объявления получают выбранные
        аккаунты; список других семей им не показывается.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div className="dated-toolbar">
        <button
          disabled={busy}
          onClick={() => {
            if (leave()) {
              setSelected("");
              setDetail(null);
              setComposing(false);
              void run(() => list());
            }
          }}
        >
          Входящие
        </button>
        <button
          className="primary"
          disabled={busy}
          onClick={() => {
            if (leave()) {
              setSelected("");
              setDetail(null);
              setComposing(true);
            }
          }}
        >
          Написать сообщение
        </button>
        <button
          disabled={busy}
          onClick={() => void run(() => (selected ? open(selected) : list()))}
        >
          Обновить сообщения
        </button>
      </div>
      {composing ? (
        <fieldset className="access-fields message-compose" disabled={busy}>
          <h3>Новое сообщение</h3>
          {
            <label className="field">
              <span>Вид сообщения</span>
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="direct">Личный диалог</option>
                <option value="group">Групповое обсуждение</option>
                {options.canAnnounce && (
                  <option value="announcement">Объявление</option>
                )}
              </select>
            </label>
          }
          {kind === "group" && (
            <>
              <p className="info-line">
                В этом обсуждении все участники видят общий текст и имена
                собеседников. До 200 участников, включая автора.
              </p>
              {options.canAnnounce && (
                <label className="field">
                  <span>Выбор участников</span>
                  <select
                    value={groupMode}
                    onChange={(e) => setGroupMode(e.target.value)}
                  >
                    <option value="contacts">Выбрать собеседников</option>
                    <option value="audience">По учебным группам</option>
                  </select>
                </label>
              )}
            </>
          )}
          {usingContacts ? (
            <>
              <label className="field">
                <span>По поводу ученика</span>
                <select
                  value={student}
                  onChange={(e) => setStudent(e.target.value)}
                >
                  {!["parent", "student"].includes(options.role) && (
                    <option value="">Переписка сотрудников</option>
                  )}
                  {options.students.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Найти собеседника</span>
                <input
                  type="search"
                  maxLength={100}
                  value={contactQuery}
                  onChange={(e) => setContactQuery(e.target.value)}
                />
              </label>
              {kind === "direct" ? (
                <label className="field">
                  <span>Кому</span>
                  <select
                    value={recipient}
                    disabled={contactLoading}
                    onChange={(e) => setRecipient(e.target.value)}
                  >
                    <option value="">Выберите собеседника</option>
                    {contacts.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} · {roles[p.role]}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <fieldset className="message-contact-choices">
                  <legend>Собеседники · выбрано {selectedPeople.length}</legend>
                  {contacts.map((p) => (
                    <label className="check-line" key={p.id}>
                      <input
                        type="checkbox"
                        checked={selectedPeople.some((v) => v.id === p.id)}
                        onChange={(e) =>
                          setSelectedPeople((old) =>
                            e.target.checked
                              ? [...old, p]
                              : old.filter((v) => v.id !== p.id),
                          )
                        }
                      />
                      {p.name} · {roles[p.role]}
                    </label>
                  ))}
                  {!!selectedPeople.length && (
                    <>
                      <p>
                        Выбраны: {selectedPeople.map((p) => p.name).join(", ")}
                      </p>
                      <button onClick={() => setSelectedPeople([])}>
                        Очистить выбор
                      </button>
                    </>
                  )}
                </fieldset>
              )}
              {contactLoading && (
                <p role="status">Загружаю доступных собеседников…</p>
              )}
              {!contactLoading && !contacts.length && (
                <p>
                  Доступных аккаунтов по этому выбору нет. Аккаунты и связь с
                  учеником назначает администратор.
                </p>
              )}
              {contactNext !== null && (
                <button
                  onClick={() =>
                    void run(async () => {
                      const params = new URLSearchParams({
                        q: contactQuery,
                        offset: String(contactNext),
                      });
                      if (student) params.set("studentId", student);
                      const r = await api(
                        `messages/contacts?${params}`,
                        workspace,
                      );
                      setContacts((old) => [...old, ...r.items]);
                      setContactNext(r.nextOffset);
                    })
                  }
                >
                  Ещё собеседники
                </button>
              )}
            </>
          ) : (
            <>
              {options.role === "admin" && kind === "announcement" && (
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={schoolwide}
                    onChange={(e) => {
                      setSchoolwide(e.target.checked);
                      if (!e.target.checked)
                        setAudience((old) =>
                          old.filter((r) => ["parent", "student"].includes(r)),
                        );
                    }}
                  />
                  Вся школа
                </label>
              )}
              {(!schoolwide || kind === "group") && (
                <label className="field">
                  <span>Классы и группы получателей</span>
                  <select
                    multiple
                    size={6}
                    value={groups}
                    onChange={(e) =>
                      setGroups(
                        Array.from(e.target.selectedOptions, (o) => o.value),
                      )
                    }
                  >
                    {options.groups.map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </select>
                  <small>
                    Можно выбрать несколько групп; каждый аккаунт будет включён
                    в аудиторию один раз.
                  </small>
                </label>
              )}
              <fieldset className="dated-multi">
                <legend>Кому из выбранной аудитории</legend>
                {(schoolwide && kind === "announcement"
                  ? Object.keys(roles)
                  : ["parent", "student"]
                ).map((role) => (
                  <label className="check-line" key={role}>
                    <input
                      type="checkbox"
                      checked={audience.includes(role)}
                      onChange={(e) =>
                        setAudience((old) =>
                          e.target.checked
                            ? [...old, role]
                            : old.filter((r) => r !== role),
                        )
                      }
                    />
                    {roles[role]}
                  </label>
                ))}
              </fieldset>
              {kind === "announcement" && (
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={ack}
                    onChange={(e) => setAck(e.target.checked)}
                  />
                  Попросить подтвердить ознакомление
                </label>
              )}
            </>
          )}
          <label className="field">
            <span>Тема</span>
            <input
              maxLength={160}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Текст сообщения</span>
            <textarea
              rows={7}
              maxLength={10000}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <MessageAttachments
            key={composeDraft}
            workspace={workspace}
            draftId={composeDraft}
            items={composeFiles}
            setItems={setComposeFiles}
            busy={busy}
            run={run}
          />
          {preview && (
            <p role="status">
              Получателей: {preview.count}.{" "}
              {Object.entries(preview.byRole)
                .map(([role, count]) => `${roles[role]}: ${count}`)
                .join(" · ")}
            </p>
          )}
          <div className="dated-toolbar">
            {kind !== "direct" && (
              <button
                disabled={
                  !title.trim() || (!text.trim() && !composeFiles.length)
                }
                onClick={() =>
                  void run(async () =>
                    setPreview(
                      await api("messages/preview", workspace, "POST", body()),
                    ),
                  )
                }
              >
                Проверить получателей
              </button>
            )}
            <button
              className="primary"
              disabled={
                !title.trim() ||
                (!text.trim() && !composeFiles.length) ||
                (kind === "direct" ? !recipient : !preview)
              }
              onClick={() =>
                void run(async () => {
                  const r = await api(
                    "messages/threads",
                    workspace,
                    "POST",
                    body(),
                  );
                  if (!alive.current) return;
                  setText("");
                  setTitle("");
                  setComposeFiles([]);
                  setComposeDraft(crypto.randomUUID());
                  nonce.current = crypto.randomUUID();
                  await open(r.id);
                })
              }
            >
              Отправить {kind === "announcement" ? "объявление" : "сообщение"}
            </button>
          </div>
        </fieldset>
      ) : selected && detail ? (
        <div className="message-detail">
          <h3>{detail.title}</h3>
          <p>
            {kinds[detail.kind]} · {statuses[detail.status]}
          </p>
          {!!detail.participants.length && (
            <details className="message-receipts">
              <summary>
                {detail.kind === "announcement"
                  ? "Прочтение и подтверждения получателей"
                  : "Участники и прочтение"}{" "}
                · {detail.participants.length} собеседников
              </summary>
              {detail.participants.map((p, i) => (
                <p key={i}>
                  {p.name} · {p.read ? "Прочитано" : "Не прочитано"}
                  {detail.requireAck
                    ? ` · ${p.acknowledged ? "Ознакомление подтверждено" : "Ожидается подтверждение"}`
                    : ""}
                  {detail.kind === "group" && detail.canManage && (
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (
                          !window.confirm(
                            `Исключить ${p.name} из обсуждения? Доступ к сообщениям и вложениям закроется.`,
                          )
                        )
                          return;
                        void run(async () => {
                          await api(
                            `messages/threads/${detail.id}/participants`,
                            workspace,
                            "POST",
                            {
                              version: detail.version,
                              userId: p.id,
                              action: "remove",
                            },
                          );
                          await open(detail.id);
                        });
                      }}
                    >
                      Исключить
                    </button>
                  )}
                </p>
              ))}
            </details>
          )}
          {detail.nextBefore !== null && (
            <button
              disabled={busy}
              onClick={() =>
                void run(() => open(detail.id, detail.nextBefore!))
              }
            >
              Предыдущие сообщения
            </button>
          )}
          <div className="message-posts">
            {detail.posts.map((p) => (
              <article
                key={p.id}
                className={p.authorId === detail.userId ? "message-own" : ""}
              >
                <strong>{p.author}</strong>
                <time dateTime={p.created}>
                  {new Date(p.created).toLocaleString("ru-RU")}
                </time>
                <p className="journal-text">{p.text}</p>
                {!!p.files?.length && (
                  <ul className="homework-files">
                    {p.files.map((f) => (
                      <li key={f.id}>
                        <a href={messageFileUrl(workspace, f.id)} download>
                          {f.filename}
                        </a>{" "}
                        · {Math.max(1, Math.ceil(f.size / 1024))} КБ
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            ))}
          </div>
          {detail.requireAck && !detail.canManage && (
            <p>
              {detail.acknowledged ? (
                "Вы подтвердили ознакомление."
              ) : (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => void run(() => change("acknowledge"))}
                >
                  Подтверждаю ознакомление
                </button>
              )}
            </p>
          )}
          {detail.canReply && (
            <fieldset className="access-fields message-reply" disabled={busy}>
              <label className="field">
                <span>Ваш ответ</span>
                <textarea
                  rows={4}
                  value={reply}
                  maxLength={10000}
                  onChange={(e) => {
                    setReply(e.target.value);
                    replyNonce.current = crypto.randomUUID();
                  }}
                />
              </label>
              <MessageAttachments
                key={replyDraft}
                workspace={workspace}
                draftId={replyDraft}
                threadId={detail.id}
                items={replyFiles}
                setItems={setReplyFiles}
                busy={busy}
                run={run}
              />
              <button
                className="primary"
                disabled={!reply.trim() && !replyFiles.length}
                onClick={() =>
                  void run(async () => {
                    await api(
                      `messages/threads/${detail.id}/reply`,
                      workspace,
                      "POST",
                      {
                        requestId: replyNonce.current,
                        version: detail.version,
                        text: reply,
                        draftId: replyDraft,
                        fileIds: replyFiles.map((f) => f.id),
                      },
                    );
                    if (alive.current) {
                      setReply("");
                      setReplyFiles([]);
                      setReplyDraft(crypto.randomUUID());
                      replyNonce.current = crypto.randomUUID();
                      await open(detail.id);
                    }
                  })
                }
              >
                Отправить ответ
              </button>
            </fieldset>
          )}
          {detail.kind === "group" && !detail.canManage && (
            <button
              disabled={busy}
              onClick={() => {
                if (
                  !window.confirm(
                    "Выйти из обсуждения? Доступ к его сообщениям и вложениям закроется.",
                  )
                )
                  return;
                void run(async () => {
                  await api(
                    `messages/threads/${detail.id}/participants`,
                    workspace,
                    "POST",
                    {
                      version: detail.version,
                      userId: detail.userId,
                      action: "leave",
                    },
                  );
                  setSelected("");
                  setDetail(null);
                  setReply("");
                  setReplyFiles([]);
                  await list();
                });
              }}
            >
              Выйти из обсуждения
            </button>
          )}
          {detail.canManage && detail.status !== "withdrawn" && (
            <button
              disabled={busy}
              onClick={() => {
                if (
                  detail.kind === "announcement" &&
                  !window.confirm(
                    "Отозвать объявление у всех получателей? История автора сохранится.",
                  )
                )
                  return;
                void run(() =>
                  change(
                    detail.kind === "announcement"
                      ? "withdraw"
                      : detail.status === "closed"
                        ? "reopen"
                        : "close",
                  ),
                );
              }}
            >
              {detail.kind === "announcement"
                ? "Отозвать объявление"
                : detail.status === "closed"
                  ? "Открыть ответы"
                  : "Закрыть ответы"}
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="dated-fields">
            <label className="field">
              <span>Поиск по теме и тексту</span>
              <input
                type="search"
                maxLength={100}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <label className="check-line">
              <input
                type="checkbox"
                checked={unread}
                onChange={(e) => setUnread(e.target.checked)}
              />
              Только непрочитанные
            </label>
          </div>
          <div className="message-inbox">
            {!threads.length && <p>Сообщений по этому запросу нет.</p>}
            {threads.map((t) => (
              <button
                key={t.id}
                disabled={busy}
                className={t.unread ? "message-unread" : ""}
                onClick={() => void run(() => open(t.id))}
              >
                <strong>{t.title}</strong>
                <span>
                  {kinds[t.kind]} · {statuses[t.status]}
                  {t.unread ? " · Новое" : ""}
                  {t.requireAck &&
                  !t.acknowledged &&
                  t.authorId !== options.userId
                    ? " · Нужно подтвердить"
                    : ""}
                </span>
              </button>
            ))}
          </div>
          {next !== null && (
            <button disabled={busy} onClick={() => void run(() => list(next))}>
              Ещё переписки
            </button>
          )}
        </>
      )}
    </section>
  );
}

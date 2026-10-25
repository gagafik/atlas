import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import MessageAttachments, { messageFileUrl } from "./MessageAttachments";
import type { MessageFile } from "./MessageAttachments";
import MessageGroupManagement from "./MessageGroupManagement";
import MessageModeration, { ReportMessage } from "./MessageModeration";

type Person = { id: string; name: string; role: string };
type Draft = {
  id: string;
  version: number;
  threadId: string | null;
  title: string;
  kind: string;
  updated: string;
};
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
  managerId: string;
  readOnly?: boolean;
};
type Post = {
  id: number;
  hidden: boolean;
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
  const [drafts, setDrafts] = useState<Draft[]>([]),
    [showDrafts, setShowDrafts] = useState(false),
    [composeVersion, setComposeVersion] = useState(0),
    [replyVersion, setReplyVersion] = useState(0),
    [savedCompose, setSavedCompose] = useState(""),
    [savedReply, setSavedReply] = useState("");
  const restoring = useRef(false),
    restoringReply = useRef(false);
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
  const replyForm = JSON.stringify({ reply, replyFiles });
  const composeDirty =
    composing &&
    (composeVersion > 0
      ? form !== savedCompose
      : !!(text || title || composeFiles.length));
  const replyDirty =
    !composing &&
    (replyVersion > 0
      ? replyForm !== savedReply
      : !!(reply || replyFiles.length));
  useEffect(() => {
    if (restoring.current) {
      setSavedCompose(form);
      restoring.current = false;
    }
  }, [form, composeDraft, composeVersion]);
  useEffect(() => {
    if (restoringReply.current) {
      setSavedReply(replyForm);
      restoringReply.current = false;
    }
  }, [replyForm, replyDraft, replyVersion]);
  useEffect(() => {
    nonce.current = crypto.randomUUID();
    setPreview(null);
  }, [form]);
  useEffect(() => {
    replyNonce.current = crypto.randomUUID();
  }, [replyFiles]);
  useEffect(() => {
    alive.current = true;
    let live = true;
    api("messages/options", workspace)
      .then((r) => {
        if (live) {
          setOptions(r);
          setStudent(r.students[0]?.id || "");
          void loadDrafts().catch((e) => {
            if (live) setError(e.message);
          });
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
    const params = new URLSearchParams({ q: contactSearch });
    if (student) params.set("studentId", student);
    api(`messages/contacts?${params}`, workspace)
      .then((r) => {
        if (live) {
          setContacts(r.items);
          setContactNext(r.nextOffset);
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
    if (!composeDirty && !replyDirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [composeDirty, replyDirty]);
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
  async function loadDrafts() {
    const r = await api("messages/drafts", workspace);
    if (alive.current) setDrafts(r.items);
  }
  async function saveDraft(isReply = false) {
    const id = isReply ? replyDraft : composeDraft;
    const content = isReply
      ? {
          requestId: replyNonce.current,
          kind: "direct",
          title: detail?.title || "Ответ",
          text: reply,
          fileIds: replyFiles.map((f) => f.id),
          draftId: id,
        }
      : { ...body(), groupMode };
    const r = await api(`messages/drafts/${id}`, workspace, "PUT", {
      version: isReply ? replyVersion : composeVersion,
      threadId: isReply ? detail?.id : null,
      content,
    });
    if (!alive.current) return;
    if (isReply) {
      setReplyVersion(r.version);
      setSavedReply(replyForm);
    } else {
      setComposeVersion(r.version);
      setSavedCompose(form);
    }
    await loadDrafts();
  }
  async function openDraft(id: string) {
    const r = await api(`messages/drafts/${id}`, workspace);
    if (!alive.current) return;
    if (r.threadId) {
      await open(r.threadId);
      if (!alive.current) return;
      restoringReply.current = true;
      setReply(r.content.text);
      setReplyFiles(r.files);
      setReplyDraft(r.id);
      setReplyVersion(r.version);
    } else {
      const c = r.content;
      restoring.current = true;
      setKind(c.kind);
      setTitle(c.title);
      setText(c.text);
      setStudent(c.studentId || "");
      setRecipient(c.recipientId || "");
      setContactQuery("");
      setContacts(r.people);
      setSelectedPeople(
        c.recipientIds.map(
          (id: string) =>
            r.people.find((p: Person) => p.id === id) || {
              id,
              name: "Собеседник недоступен",
              role: "",
            },
        ),
      );
      setGroupMode(c.groupMode);
      setGroups(c.groupIds);
      setAudience(c.kind === "direct" ? ["parent", "student"] : c.roles);
      setSchoolwide(c.schoolwide);
      setAck(c.requireAck);
      setComposeDraft(r.id);
      setComposeVersion(r.version);
      setComposeFiles(r.files);
      setSelected("");
      setDetail(null);
      setComposing(true);
    }
    setShowDrafts(false);
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
      setReplyVersion(0);
      setSavedReply("");
    }
    setDetail((old) =>
      before && old ? { ...r, posts: [...r.posts, ...old.posts] } : r,
    );
    setSelected(id);
    setComposing(false);
    setShowDrafts(false);
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
      (composeDirty || replyDirty) &&
      !window.confirm(
        "В форме есть несохранённые изменения. Закрыть её? Сохранённый ранее черновик останется в списке.",
      )
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
    setComposeVersion(0);
    setReplyVersion(0);
    setSavedCompose("");
    setSavedReply("");
    setRecipient("");
    setContactQuery("");
    return true;
  }
  function body() {
    return {
      requestId: nonce.current,
      kind,
      title,
      text,
      draftId: composeDraft,
      draftVersion: composeVersion || null,
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
      <MessageModeration
        workspace={workspace}
        admin={options.role === "admin"}
      />
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
              setShowDrafts(false);
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
              setShowDrafts(false);
            }
          }}
        >
          Написать сообщение
        </button>
        <button
          disabled={busy}
          onClick={() => {
            if (!leave()) return;
            setComposing(false);
            setSelected("");
            setDetail(null);
            setShowDrafts(true);
            void run(loadDrafts);
          }}
        >
          Черновики · {drafts.length}
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void run(() =>
              showDrafts ? loadDrafts() : selected ? open(selected) : list(),
            )
          }
        >
          Обновить сообщения
        </button>
      </div>
      {showDrafts ? (
        <div className="message-drafts">
          <h3>Личные черновики</h3>
          <p>
            До 50 черновиков в этом пространстве. Сохранённые текст, получатели
            и выбранные вложения доступны с другого устройства. Получатели их не
            видят.
          </p>
          {!drafts.length && <p>Сохранённых черновиков пока нет.</p>}
          {drafts.map((d) => (
            <article key={d.id} className="message-draft-item">
              <div>
                <strong>{d.title || "Без темы"}</strong>
                <p>
                  {d.threadId ? "Ответ" : kinds[d.kind]} ·{" "}
                  {new Date(d.updated).toLocaleString("ru-RU")}
                </p>
              </div>
              <button
                disabled={busy}
                onClick={() => void run(() => openDraft(d.id))}
              >
                Продолжить
              </button>
              <button
                disabled={busy}
                onClick={() => {
                  if (
                    !window.confirm(
                      "Удалить этот черновик? Неотправленные файлы будут очищены автоматически.",
                    )
                  )
                    return;
                  void run(async () => {
                    await api(
                      `messages/drafts/${d.id}?version=${d.version}`,
                      workspace,
                      "DELETE",
                    );
                    await loadDrafts();
                  });
                }}
              >
                Удалить черновик
              </button>
            </article>
          ))}
        </div>
      ) : composing ? (
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
                  onChange={(e) => {
                    setStudent(e.target.value);
                    setSelectedPeople([]);
                    setRecipient("");
                  }}
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
                    {recipient && !contacts.some((p) => p.id === recipient) && (
                      <option value={recipient}>
                        Ранее выбранный собеседник · проверьте выбор
                      </option>
                    )}
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
            <button onClick={() => void run(() => saveDraft())}>
              Сохранить черновик
            </button>
            <span role="status">
              {composeVersion
                ? composeDirty
                  ? "Есть несохранённые изменения"
                  : "Черновик сохранён на сервере"
                : "Черновик ещё не сохранён"}
            </span>
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
                  setComposeVersion(0);
                  setSavedCompose("");
                  nonce.current = crypto.randomUUID();
                  await loadDrafts();
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
          {detail.readOnly && (
            <p role="status">
              Только чтение: учебная связь или участие в переписке изменились.
              Доступна история до этого момента; новые сообщения отправить
              нельзя.
            </p>
          )}
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
                </p>
              ))}
            </details>
          )}
          {detail.kind === "group" && (
            <p>
              Управляет обсуждением:{" "}
              {detail.canManage
                ? "вы"
                : detail.participants.find((p) => p.id === detail.managerId)
                    ?.name || "участник обсуждения"}
              .
            </p>
          )}
          {detail.kind === "group" && detail.canManage && (
            <MessageGroupManagement
              key={detail.id}
              workspace={workspace}
              threadId={detail.id}
              version={detail.version}
              busy={busy}
              run={run}
              onChanged={() => open(detail.id)}
            />
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
                {!p.hidden && p.authorId !== detail.userId && (
                  <ReportMessage
                    key={p.id}
                    workspace={workspace}
                    postId={p.id}
                  />
                )}
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
              ) : detail.readOnly ? (
                "Подтверждение недоступно: связь с учеником изменилась."
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
          {!detail.readOnly && (detail.canReply || replyVersion > 0) && (
            <fieldset className="access-fields message-reply" disabled={busy}>
              {!detail.canReply && (
                <p>Ответы закрыты. Черновик можно редактировать и сохранять.</p>
              )}
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
                busy={busy || !detail.canReply}
                run={run}
              />
              <button onClick={() => void run(() => saveDraft(true))}>
                Сохранить черновик ответа
              </button>
              <p role="status">
                {replyVersion
                  ? replyDirty
                    ? "Есть несохранённые изменения"
                    : "Черновик ответа сохранён на сервере"
                  : "Черновик ответа ещё не сохранён"}
              </p>
              <button
                className="primary"
                disabled={
                  !detail.canReply || (!reply.trim() && !replyFiles.length)
                }
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
                        draftVersion: replyVersion || null,
                        fileIds: replyFiles.map((f) => f.id),
                      },
                    );
                    if (alive.current) {
                      setReply("");
                      setReplyFiles([]);
                      setReplyDraft(crypto.randomUUID());
                      setReplyVersion(0);
                      setSavedReply("");
                      replyNonce.current = crypto.randomUUID();
                      await loadDrafts();
                      await open(detail.id);
                    }
                  })
                }
              >
                Отправить ответ
              </button>
            </fieldset>
          )}
          {detail.kind === "group" && !detail.canManage && !detail.readOnly && (
            <button
              disabled={busy}
              onClick={() => {
                if (
                  !window.confirm(
                    "Выйти из обсуждения? Новые сообщения станут недоступны; прежняя история останется только для чтения.",
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
                  {t.readOnly ? " · Только чтение" : ""}
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

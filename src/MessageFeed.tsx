import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { MessageFileList, type MessageFile } from "./MessageAttachments";
import { ReportMessage } from "./MessageModeration";
import { mergeInbox, noTitle } from "./messages";
import { startPolling } from "./polling";

type Reply = {
  id: number;
  authorId: string;
  author: string;
  created: string;
  text: string;
  hidden: boolean;
  own: boolean;
};
export type RepliesMode = "off" | "author" | "all";
// One shared empty list: a new array on every render would reset the replies shown.
export const noReplies: Reply[] = [];
type Item = {
  id: string;
  title: string;
  status: string;
  version: number;
  author: string;
  created: string;
  text: string;
  files: MessageFile[];
  requireAck: boolean;
  acknowledged: boolean;
  unread: boolean;
  readOnly: boolean;
  latestId: number;
  canManage: boolean;
  postId: number;
  repliesMode: RepliesMode;
  replyCount: number;
  lastReplies: Reply[];
  likes: number;
  liked: boolean;
  canLike: boolean;
  canRespond: boolean;
};

const modeNote: Record<RepliesMode, string> = {
  off: "",
  author: "Ответы видит только автор объявления; вы видите свои.",
  all: "Ответы видят все получатели объявления, с именами авторов.",
};

/** Replies under an announcement: the last few, all of them on request, and a form while the author allows answers. */
export function FeedReplies({
  workspace,
  threadId,
  mode,
  count,
  shown,
  canReply,
  expanded = false,
  onChanged,
  onPending,
}: {
  workspace: string;
  threadId: string;
  mode: RepliesMode;
  count: number;
  shown: Reply[];
  canReply: boolean;
  expanded?: boolean;
  onChanged: () => void;
  onPending?: (pending: boolean) => void;
}) {
  const [replies, setReplies] = useState<Reply[]>(shown),
    [all, setAll] = useState(false),
    [next, setNext] = useState<number | null>(null);
  const [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const request = useRef(crypto.randomUUID()),
    alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (!all) setReplies(shown);
  }, [shown, all]);
  useEffect(() => {
    onPending?.(!!text.trim());
  }, [text]);
  useEffect(
    () => () => {
      onPending?.(false);
    },
    [],
  );
  async function load(before?: number) {
    const r = await api(
      `messages/threads/${threadId}/replies${before ? `?before=${before}` : ""}`,
      workspace,
    );
    if (!alive.current) return;
    setReplies((old) => (before ? [...r.items, ...old] : r.items));
    setNext(r.nextBefore);
    setAll(true);
  }
  useEffect(() => {
    if (expanded && count)
      void load().catch((e) => alive.current && setError(e.message));
  }, [count, expanded]);
  async function run(task: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e: any) {
      if (alive.current) setError(e.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  if (mode === "off" && !count) return null;
  return (
    <section className="feed-replies" aria-label="Ответы под объявлением">
      {mode !== "off" && <small>{modeNote[mode]}</small>}
      {!!count && !all && count > replies.length && (
        <button disabled={busy} onClick={() => void run(() => load())}>
          Показать все ответы · {count}
        </button>
      )}
      {all && next !== null && (
        <button disabled={busy} onClick={() => void run(() => load(next))}>
          Более ранние ответы
        </button>
      )}
      {replies.map((r) => (
        <div
          key={r.id}
          className={r.own ? "feed-reply message-own" : "feed-reply"}
        >
          <strong>{r.author}</strong>
          <time dateTime={r.created}>
            {new Date(r.created).toLocaleString("ru-RU")}
          </time>
          <p className="journal-text">{r.text}</p>
          {!r.own && !r.hidden && (
            <ReportMessage workspace={workspace} postId={r.id} />
          )}
        </div>
      ))}
      {canReply && (
        <fieldset className="access-fields" disabled={busy}>
          <label className="field">
            <span>Ваш ответ</span>
            <textarea
              rows={3}
              maxLength={2000}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                request.current = crypto.randomUUID();
              }}
            />
          </label>
          <button
            className="primary"
            disabled={!text.trim()}
            onClick={() =>
              void run(async () => {
                await api(
                  `messages/threads/${threadId}/replies`,
                  workspace,
                  "POST",
                  {
                    requestId: request.current,
                    text,
                  },
                );
                if (!alive.current) return;
                setText("");
                request.current = crypto.randomUUID();
                if (all || expanded) await load();
                onChanged();
              })
            }
          >
            Ответить
          </button>
        </fieldset>
      )}
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
    </section>
  );
}

/** Home page: announcements addressed to the account, newest first, with files, acknowledgement, likes and replies. */
export default function MessageFeed({ workspace }: { workspace: string }) {
  const [items, setItems] = useState<Item[]>([]),
    [next, setNext] = useState<number | null>(null),
    [loaded, setLoaded] = useState(false),
    [error, setError] = useState(""),
    [closed, setClosed] = useState(false),
    [busy, setBusy] = useState("");
  const alive = useRef(true),
    pending = useRef(new Set<string>()),
    marked = useRef(new Set<string>()),
    paged = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  // The first page only, so a reply being typed or a page the reader scrolled to is not disturbed.
  async function first() {
    const r = await api("messages/feed", workspace);
    if (!alive.current) return;
    setItems((old) => mergeInbox(old, r.items, r.nextBefore !== null));
    if (!paged.current) setNext(r.nextBefore);
    setLoaded(true);
  }
  useEffect(() => {
    const poll = startPolling(
      async () => {
        if (pending.current.size) return;
        try {
          await first();
        } catch (e: any) {
          if (!alive.current) return;
          if (e.status === 403) setClosed(true);
          else return false;
        }
      },
      { interval: 60000 },
    );
    return () => poll.stop();
  }, [workspace]);
  useEffect(() => {
    const todo = items.filter(
      (i) => i.unread && !marked.current.has(`${i.id}:${i.latestId}`),
    );
    if (!todo.length) return;
    let live = true;
    void (async () => {
      for (const i of todo) {
        if (!live) return;
        marked.current.add(`${i.id}:${i.latestId}`);
        try {
          await api(`messages/threads/${i.id}/read`, workspace, "PUT", {
            throughId: i.latestId,
          });
        } catch {
          continue; // the announcement stays marked as new and is retried on the next visit
        }
        if (!alive.current) return;
        setItems((old) =>
          old.map((x) => (x.id === i.id ? { ...x, unread: false } : x)),
        );
        window.dispatchEvent(new Event("atlas-messages-read"));
      }
    })();
    return () => {
      live = false;
    };
  }, [items]);
  async function act(id: string, task: () => Promise<void>) {
    if (busy) return;
    setBusy(id);
    setError("");
    try {
      await task();
    } catch (e: any) {
      if (!alive.current) return;
      setError(e.message);
      // The announcement changed under the reader (withdrawn, closed, link lost): show how it stands now.
      if ([404, 409].includes(e.status)) await first().catch(() => {});
    } finally {
      if (alive.current) setBusy("");
    }
  }
  if (closed)
    return (
      <p className="info-line">
        Лента объявлений недоступна для этого аккаунта в выбранном пространстве.
      </p>
    );
  return (
    <section className="message-feed">
      <p className="muted">
        Объявления, отправленные вам. Ответ под объявлением виден тем, кому его
        открыл автор.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {loaded && !items.length && (
        <p className="info-line">Объявлений пока нет.</p>
      )}
      {!loaded && !error && <p role="status">Загружаю объявления…</p>}
      <div className="message-posts">
        {items.map((i) => (
          <article key={i.id} className={i.unread ? "message-unread" : ""}>
            <h3>{i.title || noTitle}</h3>
            <strong>{i.author}</strong>
            <time dateTime={i.created}>
              {new Date(i.created).toLocaleString("ru-RU")}
              {i.unread ? " · Новое" : ""}
              {i.status === "withdrawn" ? " · Объявление отозвано" : ""}
              {i.readOnly ? " · Только чтение" : ""}
            </time>
            <p className="journal-text">{i.text}</p>
            <MessageFileList workspace={workspace} files={i.files} />
            {i.requireAck &&
              !i.canManage &&
              (i.acknowledged ? (
                <p>Вы подтвердили ознакомление.</p>
              ) : !i.readOnly ? (
                <button
                  className="primary"
                  disabled={!!busy}
                  onClick={() =>
                    void act(i.id, async () => {
                      await api(
                        `messages/threads/${i.id}/change`,
                        workspace,
                        "POST",
                        {
                          version: i.version,
                          action: "acknowledge",
                        },
                      );
                      await first();
                    })
                  }
                >
                  Подтверждаю ознакомление
                </button>
              ) : (
                <p>Подтверждение недоступно.</p>
              ))}
            <div className="feed-actions">
              <button
                aria-pressed={i.liked}
                disabled={!!busy || !i.canLike}
                onClick={() =>
                  void act(i.id, async () => {
                    const r = await api(
                      `messages/posts/${i.postId}/like`,
                      workspace,
                      "PUT",
                      {
                        liked: !i.liked,
                      },
                    );
                    setItems((old) =>
                      old.map((x) =>
                        x.id === i.id
                          ? { ...x, likes: r.likes, liked: r.liked }
                          : x,
                      ),
                    );
                  })
                }
              >
                {i.liked ? "Вам нравится" : "Нравится"} · {i.likes}
              </button>
            </div>
            <FeedReplies
              workspace={workspace}
              threadId={i.id}
              mode={i.repliesMode}
              count={i.replyCount}
              shown={i.lastReplies}
              canReply={i.canRespond}
              onChanged={() => void first().catch(() => {})}
              onPending={(has) => {
                if (has) pending.current.add(i.id);
                else pending.current.delete(i.id);
              }}
            />
          </article>
        ))}
      </div>
      {next !== null && (
        <button
          disabled={!!busy}
          onClick={() =>
            void act("more", async () => {
              const r = await api(`messages/feed?before=${next}`, workspace);
              if (!alive.current) return;
              paged.current = true;
              setItems((old) => [
                ...old,
                ...r.items.filter((x: Item) => !old.some((o) => o.id === x.id)),
              ]);
              setNext(r.nextBefore);
            })
          }
        >
          Ещё объявления
        </button>
      )}
    </section>
  );
}

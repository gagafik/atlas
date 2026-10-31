import { useEffect, useState } from "react";
import { api } from "./api";
import type { MessageFile } from "./MessageAttachments";
import { noTitle } from "./messages";

const statuses: Record<string, string> = {
  open: "Ожидает рассмотрения",
  dismissed: "Жалоба отклонена",
  hidden: "Принято решение скрыть",
  restored: "Принято решение восстановить",
};
const actions: Record<string, string> = {
  hide: "Скрыть сообщение и вложения",
  restore: "Восстановить сообщение и вложения",
  dismiss: "Отклонить жалобу",
};
type Case = {
  id: string;
  status: string;
  version: number;
  postVersion: number;
  created: string;
  reporter: string;
  reason: string;
  note: string;
  hidden: boolean;
  canReview: boolean;
  evidence?: {
    title: string;
    author: string;
    created: string;
    text: string;
    files: MessageFile[];
  };
  history: { created: string; action: string; note: string }[];
};
type Summary = Pick<Case, "id" | "status" | "created" | "reporter">;
type Recovery = {
  id: string;
  title: string;
  version: number;
  candidates: { id: string; name: string; role: string }[];
};

export function ReportMessage({
  workspace,
  postId,
}: {
  workspace: string;
  postId: number;
}) {
  const [reason, setReason] = useState(""),
    [share, setShare] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [sent, setSent] = useState(false);
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const changed = () => {
    setRequestId(crypto.randomUUID());
    setError("");
  };
  if (sent)
    return (
      <p role="status">
        Жалоба отправлена. Результат появится в разделе «Жалобы».
      </p>
    );
  return (
    <details className="message-report">
      <summary>Пожаловаться на сообщение</summary>
      <fieldset className="access-fields" disabled={busy}>
        <label className="field">
          <span>Причина жалобы</span>
          <textarea
            maxLength={2000}
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              changed();
            }}
          />
        </label>
        <label className="inline">
          <input
            type="checkbox"
            checked={share}
            onChange={(e) => {
              setShare(e.target.checked);
              changed();
            }}
          />
          Передать администраторам это сообщение, его вложения, тему переписки и
          моё имя
        </label>
        <p>
          Другие сообщения и список участников не передаются. После отправки
          отозвать материалы жалобы нельзя.
        </p>
        <button
          disabled={!share || !reason.trim()}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await api(`messages/posts/${postId}/report`, workspace, "POST", {
                requestId,
                reason,
                shareEvidence: share,
              });
              setSent(true);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          Отправить жалобу
        </button>
        {error && <p role="alert">{error}</p>}
      </fieldset>
    </details>
  );
}

function RecoveryItem({
  workspace,
  item,
  onDone,
}: {
  workspace: string;
  item: Recovery;
  onDone: () => Promise<void>;
}) {
  const [target, setTarget] = useState(""),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  return (
    <article className="message-case">
      <h4>{item.title || noTitle}</h4>
      {!item.candidates.length ? (
        <p>
          Нет подходящего действующего участника. Проверьте аккаунты и учебные
          назначения. Восстановление не добавляет посторонних в переписку.
        </p>
      ) : (
        <fieldset className="access-fields" disabled={busy}>
          <label className="field">
            <span>Новый управляющий</span>
            <select
              value={target}
              onChange={(e) => {
                setTarget(e.target.value);
                setRequestId(crypto.randomUUID());
              }}
            >
              <option value="">Выберите участника</option>
              {item.candidates.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Причина восстановления — видна участникам</span>
            <textarea
              maxLength={1000}
              value={reason}
              onChange={(e) => {
                setReason(e.target.value);
                setRequestId(crypto.randomUUID());
              }}
            />
          </label>
          <button
            disabled={!target || !reason.trim()}
            onClick={async () => {
              if (
                !window.confirm(
                  "Назначить выбранного участника управляющим и вернуть группе доступ?",
                )
              )
                return;
              setBusy(true);
              setError("");
              try {
                await api(`messages/recovery/${item.id}`, workspace, "POST", {
                  requestId,
                  version: item.version,
                  userId: target,
                  reason,
                });
                await onDone();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            Восстановить управление
          </button>
        </fieldset>
      )}
      {error && <p role="alert">{error}</p>}
    </article>
  );
}

export default function MessageModeration({
  workspace,
  admin,
}: {
  workspace: string;
  admin: boolean;
}) {
  const [expanded, setExpanded] = useState(false),
    [items, setItems] = useState<Summary[]>([]),
    [next, setNext] = useState<number | null>(null),
    [filter, setFilter] = useState("all"),
    [current, setCurrent] = useState<Case | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [note, setNote] = useState(""),
    [action, setAction] = useState("dismiss"),
    [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [groups, setGroups] = useState<Recovery[] | null>(null),
    [groupNext, setGroupNext] = useState<number | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const load = async (offset = 0) => {
    const r = await api(
      `messages/reports?status=${filter}&offset=${offset}`,
      workspace,
    );
    setItems((old) => (offset ? [...old, ...r.items] : r.items));
    setNext(r.nextOffset);
  };
  const open = async (id: string) => {
    const r = await api(`messages/reports/${id}`, workspace);
    setCurrent(r);
    setNote("");
    setAction("dismiss");
    setRequestId(crypto.randomUUID());
  };
  const recovery = async (offset = 0) => {
    const r = await api(`messages/recovery?offset=${offset}`, workspace);
    setGroups((old) => (offset ? [...(old || []), ...r.items] : r.items));
    setGroupNext(r.nextOffset);
  };
  useEffect(() => {
    if (!expanded) return;
    let live = true;
    setBusy(true);
    setError("");
    setCurrent(null);
    api(`messages/reports?status=${filter}`, workspace)
      .then((r) => {
        if (live) {
          setItems(r.items);
          setNext(r.nextOffset);
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
  }, [expanded, filter, workspace]);
  return (
    <details
      className="message-moderation"
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>
        {admin ? "Жалобы и восстановление групп" : "Мои жалобы"}
      </summary>
      {expanded && (
        <>
          <p>
            {admin
              ? "Доступны только переданные в жалобе материалы. Просмотр материалов и решения записываются в журнал действий."
              : "Здесь сохраняются причина вашей жалобы и результат рассмотрения."}
          </p>
          {error && (
            <p role="alert" className="error-banner">
              {error}
            </p>
          )}
          <fieldset className="access-fields" disabled={busy}>
            <label className="field">
              <span>Состояние жалоб</span>
              <select
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              >
                <option value="all">Все</option>
                {Object.entries(statuses).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() =>
                void run(async () => {
                  await load();
                  if (current) await open(current.id);
                })
              }
            >
              Обновить жалобы
            </button>
            {!items.length && <p>В этом списке жалоб нет.</p>}
            {items.map((item) => (
              <button
                key={item.id}
                className="message-case-link"
                onClick={() => void run(() => open(item.id))}
              >
                {statuses[item.status]} ·{" "}
                {new Date(item.created).toLocaleString("ru-RU")}
                {admin ? ` · ${item.reporter}` : ""}
              </button>
            ))}
            {next !== null && (
              <button onClick={() => void run(() => load(next))}>
                Ещё жалобы
              </button>
            )}
          </fieldset>
          {current && (
            <article className="message-case">
              <h3>{statuses[current.status]}</h3>
              <p className="journal-text">Причина: {current.reason}</p>
              <p>
                Сообщение сейчас {current.hidden ? "скрыто" : "не скрыто"}.
                Решения по другим жалобам на него тоже учитываются.
              </p>
              {current.evidence && (
                <>
                  <h4>{current.evidence.title || noTitle}</h4>
                  <p>
                    {current.evidence.author} ·{" "}
                    {new Date(current.evidence.created).toLocaleString("ru-RU")}
                  </p>
                  <p className="journal-text">{current.evidence.text}</p>
                  <ul>
                    {current.evidence.files.map((f) => (
                      <li key={f.id}>
                        <a
                          href={`/api/messages/reports/${current.id}/files/${f.id}?workspace=${encodeURIComponent(workspace)}`}
                          download
                        >
                          {f.filename}
                        </a>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {current.history.map((h, i) => (
                <p key={i} className="journal-text">
                  {new Date(h.created).toLocaleString("ru-RU")} ·{" "}
                  {actions[h.action]}: {h.note}
                </p>
              ))}
              {admin && !current.canReview && (
                <p>
                  Жалобу на ваше сообщение должен рассмотреть другой
                  администратор.
                </p>
              )}
              {current.canReview && (
                <fieldset className="access-fields" disabled={busy}>
                  <label className="field">
                    <span>Решение</span>
                    <select
                      value={action}
                      onChange={(e) => {
                        setAction(e.target.value);
                        setRequestId(crypto.randomUUID());
                      }}
                    >
                      <option value="dismiss">Отклонить жалобу</option>
                      <option value={current.hidden ? "restore" : "hide"}>
                        {actions[current.hidden ? "restore" : "hide"]}
                      </option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Объяснение заявителю</span>
                    <textarea
                      maxLength={2000}
                      value={note}
                      onChange={(e) => {
                        setNote(e.target.value);
                        setRequestId(crypto.randomUUID());
                      }}
                    />
                  </label>
                  <button
                    disabled={!note.trim()}
                    onClick={() => {
                      if (
                        !window.confirm(
                          `${actions[action]}? Объяснение получит заявитель.`,
                        )
                      )
                        return;
                      void run(async () => {
                        await api(
                          `messages/reports/${current.id}/decision`,
                          workspace,
                          "POST",
                          {
                            requestId,
                            version: current.version,
                            postVersion: current.postVersion,
                            action,
                            note,
                          },
                        );
                        await open(current.id);
                        await load();
                      });
                    }}
                  >
                    Сохранить решение
                  </button>
                </fieldset>
              )}
            </article>
          )}
          {admin && (
            <>
              <h3>Группы без доступного управляющего</h3>
              <p>
                Назначается только действующий участник с подходящими учебными
                связями. История переписки администратору не открывается.
              </p>
              <button
                disabled={busy}
                onClick={() => void run(() => recovery())}
              >
                Проверить группы
              </button>
              {groups?.length === 0 && (
                <p>На просмотренной странице таких групп нет.</p>
              )}
              {groups?.map((g) => (
                <RecoveryItem
                  key={`${g.id}:${g.version}`}
                  workspace={workspace}
                  item={g}
                  onDone={() => recovery()}
                />
              ))}
              {groupNext !== null && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => recovery(groupNext))}
                >
                  Проверить следующие группы
                </button>
              )}
            </>
          )}
        </>
      )}
    </details>
  );
}

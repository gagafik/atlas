import { useEffect, useState } from "react";
import { api } from "./api";

type Channel = {
  id: string;
  sender: string;
  label: string;
  enabled: boolean;
  default: boolean;
};
type Entry = {
  id: number;
  version: number;
  created: string;
  actor: string;
  reason: string;
  changes: { id: string; label: string; enabled: boolean }[];
};
const senders: [string, string][] = [
  ["student", "Ученик"],
  ["parent", "Родитель"],
  ["teacher", "Учитель"],
  ["staff", "Сотрудники"],
  ["feed", "Лента объявлений"],
];

/** Administrator's matrix of who may start a conversation with whom. Every change is versioned, explained and kept in the history. */
export default function MessageChannels({
  workspace,
  onSaved,
}: {
  workspace: string;
  onSaved: () => void;
}) {
  const [expanded, setExpanded] = useState(false),
    [version, setVersion] = useState(0),
    [items, setItems] = useState<Channel[]>([]),
    [enabled, setEnabled] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [history, setHistory] = useState<Entry[]>([]),
    [next, setNext] = useState<number | null>(null);
  async function load() {
    const [matrix, log] = await Promise.all([
      api("messages/channels", workspace),
      api("messages/channels/history", workspace),
    ]);
    setVersion(matrix.version);
    setItems(matrix.items);
    setEnabled(
      Object.fromEntries(matrix.items.map((c: Channel) => [c.id, c.enabled])),
    );
    setHistory(log.items);
    setNext(log.nextBefore);
  }
  async function run(task: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (expanded) void run(load);
  }, [expanded, workspace]);
  const changed = items.some((c) => enabled[c.id] !== c.enabled);
  return (
    <details
      className="message-moderation message-channels"
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Каналы сообщений</summary>
      {expanded && (
        <>
          <p>
            Каналы определяют, кто может первым написать кому. Администратор
            пишет всегда. Если канал выключен в обе стороны, существующие
            диалоги остаются только для чтения, пока канал не включат снова;
            данные не удаляются.
          </p>
          {error && (
            <p role="alert" className="error-banner">
              {error}
            </p>
          )}
          <fieldset className="access-fields" disabled={busy}>
            {senders.map(([sender, name]) => (
              <fieldset key={sender} className="message-channel-group">
                <legend>{name}</legend>
                {items
                  .filter((c) => c.sender === sender)
                  .map((c) => (
                    <label className="check-line" key={c.id}>
                      <input
                        type="checkbox"
                        checked={!!enabled[c.id]}
                        onChange={(e) =>
                          setEnabled((old) => ({
                            ...old,
                            [c.id]: e.target.checked,
                          }))
                        }
                      />
                      <span>
                        {c.label}
                        {enabled[c.id] !== c.default && (
                          <small> · отличается от стандартной настройки</small>
                        )}
                      </span>
                    </label>
                  ))}
              </fieldset>
            ))}
            <label className="field">
              <span>Причина изменения</span>
              <input
                maxLength={500}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <div className="dated-toolbar">
              <button
                className="primary"
                disabled={!reason.trim() || !changed}
                onClick={() =>
                  void run(async () => {
                    await api("messages/channels", workspace, "PUT", {
                      version,
                      reason,
                      enabled: items
                        .filter((c) => enabled[c.id])
                        .map((c) => c.id),
                    });
                    setReason("");
                    await load();
                    onSaved();
                  })
                }
              >
                Сохранить настройку
              </button>
              <button
                onClick={() =>
                  setEnabled(
                    Object.fromEntries(items.map((c) => [c.id, c.default])),
                  )
                }
              >
                Вернуть значения по умолчанию
              </button>
            </div>
            <small>
              Значения по умолчанию совпадают с правилами до появления этой
              настройки. После возврата нужно сохранить с причиной.
            </small>
          </fieldset>
          <h4>История изменений</h4>
          {!history.length && <p>Настройка ещё не менялась.</p>}
          {history.map((h) => (
            <p key={h.id} className="journal-text">
              {new Date(h.created).toLocaleString("ru-RU")} · {h.actor} ·
              редакция {h.version}: {h.reason}
              {h.changes.map((c) => (
                <span key={c.id} className="block-line">
                  {c.enabled ? "Включён" : "Выключен"}: {c.label}
                </span>
              ))}
            </p>
          ))}
          {next !== null && (
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const r = await api(
                    `messages/channels/history?before=${next}`,
                    workspace,
                  );
                  setHistory((old) => [...old, ...r.items]);
                  setNext(r.nextBefore);
                })
              }
            >
              Ещё записи
            </button>
          )}
        </>
      )}
    </details>
  );
}

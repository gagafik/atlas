import { useEffect, useRef, useState } from "react";
import { api } from "./api";

type Person = { id: string; name: string; role: string };
type Roster = {
  version: number;
  managerId: string;
  limit: number;
  members: (Person & {
    accessible: boolean;
    history: boolean;
    canManage: boolean;
  })[];
};
const roleNames: Record<string, string> = {
  admin: "Администратор",
  teacher: "Учитель",
  parent: "Родитель",
  student: "Ученик",
  editor: "Составитель",
  viewer: "Сотрудник",
};

export default function MessageGroupManagement({
  workspace,
  threadId,
  version,
  busy,
  run,
  onChanged,
}: {
  workspace: string;
  threadId: string;
  version: number;
  busy: boolean;
  run: (fn: () => Promise<void>) => Promise<void>;
  onChanged: () => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [roster, setRoster] = useState<Roster | null>(null),
    [query, setQuery] = useState(""),
    [candidates, setCandidates] = useState<Person[]>([]),
    [next, setNext] = useState<number | null>(null),
    [searching, setSearching] = useState(false),
    [selected, setSelected] = useState<Person[]>([]),
    [share, setShare] = useState(false),
    [successor, setSuccessor] = useState("");
  const addNonce = useRef(crypto.randomUUID()),
    transferNonce = useRef(crypto.randomUUID());
  useEffect(() => {
    addNonce.current = crypto.randomUUID();
  }, [selected, share, version]);
  useEffect(() => {
    transferNonce.current = crypto.randomUUID();
  }, [successor, version]);
  useEffect(() => {
    if (!expanded) return;
    let live = true;
    setLoading(true);
    setError("");
    setSelected([]);
    setShare(false);
    setSuccessor("");
    api(`messages/threads/${threadId}/management`, workspace)
      .then((r) => {
        if (live) setRoster(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [expanded, threadId, workspace, version]);
  useEffect(() => {
    if (!expanded) return;
    let live = true;
    setSearching(true);
    setCandidates([]);
    setNext(null);
    const timer = window.setTimeout(() => {
      api(
        `messages/threads/${threadId}/candidates?${new URLSearchParams({ q: query })}`,
        workspace,
      )
        .then((r) => {
          if (live) {
            setCandidates(r.items);
            setNext(r.nextOffset);
          }
        })
        .catch((e) => {
          if (live) setError(e.message);
        })
        .finally(() => {
          if (live) setSearching(false);
        });
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [expanded, threadId, workspace, version, query]);
  return (
    <details
      className="message-group-management"
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Управление участниками</summary>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">Загружаю состав обсуждения…</p>}
      {roster && (
        <fieldset
          className="access-fields"
          disabled={busy || loading || roster.version !== version}
        >
          <h4>
            Участники · {roster.members.length} из {roster.limit}
          </h4>
          {roster.version !== version && (
            <p>Обсуждение изменилось. Нажмите «Обновить сообщения».</p>
          )}
          <div className="message-management-roster">
            {roster.members.map((person) => (
              <div key={person.id}>
                <span>
                  {person.name} · {roleNames[person.role] || "Аккаунт"}
                  {person.id === roster.managerId
                    ? " · Управляет обсуждением"
                    : ""}
                  {person.history
                    ? " · Нет учебной связи: только чтение"
                    : !person.accessible
                      ? " · Нет доступа к обсуждению"
                      : ""}
                </span>
                {person.id !== roster.managerId && (
                  <button
                    onClick={() => {
                      if (
                        !window.confirm(
                          person.accessible || person.history
                            ? `Исключить ${person.name}? Новые сообщения станут недоступны; история до исключения останется у участника только для чтения.`
                            : `Исключить ${person.name}? У этого аккаунта сейчас нет доступа к обсуждению.`,
                        )
                      )
                        return;
                      void run(async () => {
                        await api(
                          `messages/threads/${threadId}/participants`,
                          workspace,
                          "POST",
                          { version, userId: person.id, action: "remove" },
                        );
                        await onChanged();
                      });
                    }}
                  >
                    Исключить
                  </button>
                )}
              </div>
            ))}
          </div>
          <h4>Добавить участников</h4>
          <p>
            Новые и возвращённые участники увидят всю историю, файлы и имена
            собеседников. Учебная аудитория обсуждения остаётся прежней.
          </p>
          <label className="field">
            <span>Поиск новых участников</span>
            <input
              type="search"
              value={query}
              maxLength={100}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          {searching && <p role="status">Ищу доступные аккаунты…</p>}
          {!searching && !candidates.length && (
            <p>Доступных аккаунтов на этой странице нет.</p>
          )}
          <div className="message-contact-choices">
            {candidates.map((person) => (
              <label className="check-line" key={person.id}>
                <input
                  type="checkbox"
                  checked={selected.some((p) => p.id === person.id)}
                  disabled={
                    !selected.some((p) => p.id === person.id) &&
                    selected.length + roster.members.length >= roster.limit
                  }
                  onChange={(e) => {
                    setShare(false);
                    setSelected((old) =>
                      e.target.checked
                        ? [...old, person]
                        : old.filter((p) => p.id !== person.id),
                    );
                  }}
                />
                {person.name} · {roleNames[person.role]}
              </label>
            ))}
          </div>
          {next !== null && (
            <button
              disabled={searching}
              onClick={() =>
                void run(async () => {
                  const r = await api(
                    `messages/threads/${threadId}/candidates?${new URLSearchParams({ q: query, offset: String(next) })}`,
                    workspace,
                  );
                  setCandidates((old) => [
                    ...old,
                    ...r.items.filter(
                      (p: Person) => !old.some((v) => v.id === p.id),
                    ),
                  ]);
                  setNext(r.nextOffset);
                })
              }
            >
              Ещё доступные аккаунты
            </button>
          )}
          {!!selected.length && (
            <>
              <p>Выбраны: {selected.map((p) => p.name).join(", ")}</p>
              <button
                onClick={() => {
                  setSelected([]);
                  setShare(false);
                }}
              >
                Очистить выбор
              </button>
            </>
          )}
          <label className="check-line">
            <input
              type="checkbox"
              checked={share}
              onChange={(e) => setShare(e.target.checked)}
            />
            Подтверждаю доступ выбранных участников ко всей истории и файлам
          </label>
          <button
            className="primary"
            disabled={!share || !selected.length || searching}
            onClick={() =>
              void run(async () => {
                await api(
                  `messages/threads/${threadId}/participants/add`,
                  workspace,
                  "POST",
                  {
                    version,
                    requestId: addNonce.current,
                    userIds: selected.map((p) => p.id),
                    shareHistory: share,
                  },
                );
                await onChanged();
              })
            }
          >
            Добавить выбранных участников
          </button>
          <h4>Передать управление</h4>
          <p>
            Новый управляющий сможет добавлять и исключать участников, закрывать
            ответы и передавать управление. Вы останетесь обычным участником и
            сможете выйти. Авторство прежних сообщений сохраняется.
          </p>
          <label className="field">
            <span>Новый управляющий</span>
            <select
              value={successor}
              onChange={(e) => setSuccessor(e.target.value)}
            >
              <option value="">Выберите участника</option>
              {roster.members
                .filter((p) => p.id !== roster.managerId && p.canManage)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {roleNames[p.role]}
                  </option>
                ))}
            </select>
          </label>
          <p className="muted">
            Для обсуждения учебных групп подходят администратор или учитель,
            ведущий все выбранные группы. Сначала добавьте его в обсуждение.
          </p>
          <button
            disabled={!successor}
            onClick={() => {
              const person = roster.members.find((p) => p.id === successor);
              if (
                !person ||
                !window.confirm(
                  `Передать управление ${person.name}? Вы потеряете права управления этим обсуждением.`,
                )
              )
                return;
              void run(async () => {
                await api(
                  `messages/threads/${threadId}/manager`,
                  workspace,
                  "POST",
                  {
                    version,
                    requestId: transferNonce.current,
                    userId: successor,
                  },
                );
                await onChanged();
              });
            }}
          >
            Передать управление
          </button>
        </fieldset>
      )}
    </details>
  );
}

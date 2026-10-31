import { useEffect, useState } from "react";
import { api } from "./api";
import { roleNames } from "./auth";

export type SavedGroup = {
  id: string;
  name: string;
  version: number;
  total: number;
  available: number;
};
type Person = { id: string; name: string; role: string };
type Editing = {
  id: string;
  version: number;
  name: string;
  people: Person[];
  unavailable: number;
};

/** The sender's own recipient groups. A group keeps account ids only; who can be reached is decided again at every send. */
export default function MessageSavedGroups({
  workspace,
  groups,
  reload,
  busy,
  run,
}: {
  workspace: string;
  groups: SavedGroup[];
  reload: () => Promise<void>;
  busy: boolean;
  run: (task: () => Promise<void>) => Promise<void>;
}) {
  const [editing, setEditing] = useState<Editing | null>(null),
    [query, setQuery] = useState(""),
    [found, setFound] = useState<Person[]>([]),
    [next, setNext] = useState<number | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    if (!editing) return;
    let live = true;
    const timer = window.setTimeout(() => {
      api(
        `messages/saved-groups/candidates?${new URLSearchParams({ q: query })}`,
        workspace,
      )
        .then((r) => {
          if (live) {
            setFound(r.items);
            setNext(r.nextOffset);
          }
        })
        .catch((e) => live && setError(e.message));
    }, 300);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [!!editing, query, workspace]);
  return (
    <details className="message-saved-groups">
      <summary>Мои группы получателей · {groups.length}</summary>
      <p>
        Группа хранит только список аккаунтов. При каждой отправке проверяется,
        кому вы можете писать сейчас; недоступные получатели пропускаются.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {!groups.length && <p>Сохранённых групп пока нет.</p>}
      {groups.map((g) => (
        <article key={g.id} className="message-draft-item">
          <div>
            <strong>{g.name}</strong>
            <p>
              Доступно сейчас: {g.available} из {g.total}
            </p>
          </div>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const r = await api(`messages/saved-groups/${g.id}`, workspace);
                setEditing({
                  id: g.id,
                  version: r.version,
                  name: r.name,
                  people: r.people,
                  unavailable: r.unavailable,
                });
                setQuery("");
              })
            }
          >
            Изменить
          </button>
          <button
            disabled={busy}
            onClick={() => {
              if (!window.confirm(`Удалить группу «${g.name}»?`)) return;
              void run(async () => {
                await api(
                  `messages/saved-groups/${g.id}?version=${g.version}`,
                  workspace,
                  "DELETE",
                );
                if (editing?.id === g.id) setEditing(null);
                await reload();
              });
            }}
          >
            Удалить
          </button>
        </article>
      ))}
      {!editing && (
        <button
          disabled={busy}
          onClick={() => {
            setEditing({
              id: crypto.randomUUID(),
              version: 0,
              name: "",
              people: [],
              unavailable: 0,
            });
            setQuery("");
          }}
        >
          Создать группу
        </button>
      )}
      {editing && (
        <fieldset className="access-fields" disabled={busy}>
          <legend>
            {editing.version ? "Изменение группы" : "Новая группа"}
          </legend>
          <label className="field">
            <span>Название</span>
            <input
              maxLength={120}
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
            />
          </label>
          {!!editing.unavailable && (
            <p>
              Сейчас недоступно получателей: {editing.unavailable}. При
              сохранении они будут убраны из группы.
            </p>
          )}
          <label className="field">
            <span>Найти получателя</span>
            <input
              type="search"
              maxLength={100}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <fieldset className="message-contact-choices">
            <legend>Получатели · выбрано {editing.people.length}</legend>
            {[
              ...editing.people,
              ...found.filter(
                (p) => !editing.people.some((v) => v.id === p.id),
              ),
            ].map((p) => (
              <label className="check-line" key={p.id}>
                <input
                  type="checkbox"
                  checked={editing.people.some((v) => v.id === p.id)}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      people: e.target.checked
                        ? [...editing.people, p]
                        : editing.people.filter((v) => v.id !== p.id),
                    })
                  }
                />
                {p.name} ·{" "}
                {roleNames[p.role as keyof typeof roleNames] || p.role}
              </label>
            ))}
            {!found.length && !editing.people.length && (
              <p>Подходящих аккаунтов нет.</p>
            )}
            {next !== null && (
              <button
                onClick={() =>
                  void run(async () => {
                    const r = await api(
                      `messages/saved-groups/candidates?${new URLSearchParams({ q: query, offset: String(next) })}`,
                      workspace,
                    );
                    setFound((old) => [...old, ...r.items]);
                    setNext(r.nextOffset);
                  })
                }
              >
                Ещё аккаунты
              </button>
            )}
          </fieldset>
          <div className="dated-toolbar">
            <button
              className="primary"
              disabled={!editing.name.trim() || !editing.people.length}
              onClick={() =>
                void run(async () => {
                  await api(
                    `messages/saved-groups/${editing.id}`,
                    workspace,
                    "PUT",
                    {
                      version: editing.version,
                      name: editing.name,
                      userIds: editing.people.map((p) => p.id),
                    },
                  );
                  setEditing(null);
                  await reload();
                })
              }
            >
              Сохранить группу
            </button>
            <button onClick={() => setEditing(null)}>
              Закрыть без сохранения
            </button>
          </div>
        </fieldset>
      )}
    </details>
  );
}

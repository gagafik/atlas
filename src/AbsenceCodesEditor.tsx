import { useState } from "react";
import { api } from "./api";
import { type AbsenceCode, codeSetError } from "./absenceForm";

type Entry = {
  id: number;
  version: number;
  created: string;
  actor: string;
  reason: string;
  before: AbsenceCode[];
  after: AbsenceCode[];
};

// What one save changed, in words; types are never removed, only switched off.
const changes = (before: AbsenceCode[], after: AbsenceCode[]) =>
  after.flatMap((c) => {
    const old = before.find((b) => b.id === c.id);
    if (!old) return [`добавлен «${c.name}»`];
    return [
      old.name !== c.name ? `«${old.name}» → «${c.name}»` : "",
      old.active !== c.active
        ? `«${c.name}» ${c.active ? "включён" : "выключен"}`
        : "",
      old.parents !== c.parents
        ? `«${c.name}»: родителям ${c.parents ? "можно" : "нельзя"} выбирать`
        : "",
    ].filter(Boolean);
  });

export default function AbsenceCodesEditor({
  workspace,
  onSaved,
}: {
  workspace: string;
  onSaved: () => void;
}) {
  const [version, setVersion] = useState(0),
    [saved, setSaved] = useState<AbsenceCode[]>([]),
    [items, setItems] = useState<AbsenceCode[]>([]),
    [reason, setReason] = useState(""),
    [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [loaded, setLoaded] = useState(false);
  const [history, setHistory] = useState<Entry[] | null>(null),
    [next, setNext] = useState<number | null>(null);
  const problem = codeSetError(items);
  const dirty = JSON.stringify(items) !== JSON.stringify(saved);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const apply = (r: { version: number; items: AbsenceCode[] }) => {
    setVersion(r.version);
    setSaved(r.items);
    setItems(r.items);
  };
  // A changed list is a different request; a retry of the same list keeps its id.
  const edit = (value: AbsenceCode[]) => {
    setItems(value);
    setRequestId(crypto.randomUUID());
  };
  const change = (id: string, patch: Partial<AbsenceCode>) =>
    edit(items.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  const load = () =>
    run(async () => {
      apply(await api("journal/absences/codes", workspace));
      setLoaded(true);
    });
  const page = async (before?: number) => {
    const r = await api(
      `journal/absences/codes/history${before ? `?before=${before}` : ""}`,
      workspace,
    );
    setHistory((old) => (before && old ? [...old, ...r.items] : r.items));
    setNext(r.nextBefore);
  };
  const save = async () => {
    if (!window.confirm("Сохранить список типов отсутствия?")) return;
    const r = await api("journal/absences/codes", workspace, "PUT", {
      requestId,
      version,
      reason,
      items,
    });
    apply(r);
    setReason("");
    setRequestId(crypto.randomUUID());
    if (history) await page();
    setNotice("Типы отсутствия сохранены.");
    onSaved();
  };
  return (
    <details
      className="absence-card"
      onToggle={(e) => {
        if (e.currentTarget.open && !loaded) void load();
      }}
    >
      <summary>Типы отсутствия</summary>
      <p>
        Тип выбирают при внесении записки. Тип нельзя удалить: выключите его, и
        прежние записки сохранят название. Переименование меняет название и в
        старых записках и отчётах; история хранит прежнее.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <fieldset disabled={busy} className="access-fields">
        {items.map((c) => (
          <div className="absence-card" key={c.id}>
            <label className="field">
              <span>Название типа</span>
              <input
                value={c.name}
                maxLength={80}
                onChange={(e) => change(c.id, { name: e.target.value })}
              />
            </label>
            <label className="absence-file">
              <input
                type="checkbox"
                checked={c.parents}
                onChange={(e) => change(c.id, { parents: e.target.checked })}
              />
              Можно выбирать родителям
            </label>
            <label className="absence-file">
              <input
                type="checkbox"
                checked={c.active}
                onChange={(e) => change(c.id, { active: e.target.checked })}
              />
              Действует
            </label>
            {!saved.some((s) => s.id === c.id) && (
              <button
                type="button"
                onClick={() => edit(items.filter((x) => x.id !== c.id))}
              >
                Убрать новый тип
              </button>
            )}
          </div>
        ))}
        <button
          type="button"
          disabled={items.length >= 30}
          onClick={() =>
            edit([
              ...items,
              {
                id: `c-${crypto.randomUUID().slice(0, 8)}`,
                name: "",
                parents: true,
                active: true,
              },
            ])
          }
        >
          Добавить тип
        </button>
        <label className="field">
          <span>Причина изменения (от 3 знаков, попадёт в историю)</span>
          <textarea
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        {dirty && problem && <p className="info-line">{problem}</p>}
        <div className="dated-toolbar">
          <button
            className="primary"
            disabled={!dirty || !!problem || reason.trim().length < 3}
            onClick={() => void run(save)}
          >
            Сохранить типы
          </button>
          <button
            disabled={!dirty}
            onClick={() => {
              setItems(saved);
              setRequestId(crypto.randomUUID());
            }}
          >
            Отменить правки
          </button>
          <button disabled={!loaded} onClick={() => void run(() => page())}>
            История изменений
          </button>
        </div>
      </fieldset>
      {history && !history.length && <p>Изменений ещё не было.</p>}
      {history?.map((h) => (
        <div className="family-history" key={h.id}>
          <p>
            {new Date(h.created).toLocaleString("ru-RU")} · {h.actor} · редакция{" "}
            {h.version}
          </p>
          <p className="journal-text">{h.reason}</p>
          <p>{changes(h.before, h.after).join("; ") || "Без изменений"}</p>
        </div>
      ))}
      {next !== null && (
        <button disabled={busy} onClick={() => void run(() => page(next))}>
          Предыдущие изменения
        </button>
      )}
    </details>
  );
}

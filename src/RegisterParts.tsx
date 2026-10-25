import { useState, type ReactNode } from "react";
import { displayTime, schoolInput, schoolISO } from "./meetings";
import {
  capabilityNames,
  changedFields,
  fieldNames,
  historyActions,
  type Capability,
  type History,
  type HistoryEntry,
  type Place,
} from "./registerTypes";

export function Fact({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className="rg-prose">{children || "—"}</dd>
    </div>
  );
}

const placeText = (p?: Place) =>
  p ? [p.roomName, p.location].filter(Boolean).join(", ") || "—" : "—";

function show(key: string, value: any): string {
  if (value === null || value === undefined || value === "") return "—";
  if (key === "parentNotices")
    return (
      (value as { by: string; at: string }[])
        .map((n) => `${n.by} (${displayTime(n.at)})`)
        .join("; ") || "—"
    );
  if (key === "occurredAt") return displayTime(value);
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  return String(value);
}

function Detail({ entry }: { entry: HistoryEntry }) {
  const d = entry.data;
  if (!d) return null;
  if (entry.action === "moved")
    return (
      <p>
        {placeText(d.before)} → {placeText(d.after)}
        {d.movedOn ? ` · ${d.movedOn}` : ""}
      </p>
    );
  if (entry.action === "departed" && d.departed)
    return <p>Ушёл: {displayTime(d.departed)}</p>;
  if (entry.action === "configured") {
    const lines = (d.after?.assignments ?? []) as {
      name: string;
      capabilities: Capability[];
    }[];
    return (
      <>
        <p>
          Права:{" "}
          {lines
            .map(
              (a) =>
                `${a.name} — ${a.capabilities.map((c) => capabilityNames[c]).join(", ")}`,
            )
            .join("; ") || "никому не назначены"}
        </p>
        <p>
          Сводки для семей:{" "}
          {d.after?.familySummaries ? "показываются" : "скрыты"}
        </p>
      </>
    );
  }
  if (
    (entry.action === "written_off" || entry.action === "restored") &&
    d.writtenOff?.on
  )
    return (
      <p>
        {entry.action === "restored"
          ? "Отменено списание от "
          : "Дата списания: "}
        {d.writtenOff.on}
        {entry.action === "restored" && d.writtenOff.reason
          ? ` (причина списания: ${d.writtenOff.reason})`
          : ""}
        {d.writtenOff.by ? ` · оформил: ${d.writtenOff.by}` : ""}
      </p>
    );
  const names = changedFields(d.before, d.after);
  if (!names.length) return null;
  const keys = Object.keys(fieldNames).filter(
    (k) =>
      JSON.stringify(d.before?.[k]) !== JSON.stringify(d.after?.[k]) &&
      !["hostId", "roomId", "responsibleId", "studentIds", "staffIds"].includes(
        k,
      ),
  );
  return (
    <details>
      <summary>Изменено: {names.join(", ")}</summary>
      <dl>
        {keys.map((k) => (
          <div key={k}>
            <dt>{fieldNames[k]}</dt>
            <dd>
              Было: {show(k, d.before?.[k])}
              <br />
              Стало: {show(k, d.after?.[k])}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export function HistoryList({
  history,
  onMore,
}: {
  history: History;
  onMore: () => void;
}) {
  return (
    <section className="rg-history rg-noprint" aria-label="История изменений">
      <h4>История</h4>
      {!history.items.length && <p>Записей истории нет.</p>}
      <ol>
        {history.items.map((e) => (
          <li key={e.seq}>
            <strong>{historyActions[e.action] || e.action}</strong>
            <span>
              {e.actor} · {displayTime(e.created)}
            </span>
            {e.reason && <p>Причина: {e.reason}</p>}
            <Detail entry={e} />
          </li>
        ))}
      </ol>
      {history.nextBefore && <button onClick={onMore}>Ещё записи</button>}
    </section>
  );
}

// A date and time field that always reads and writes school time.
export function TimeInput({
  label,
  value,
  onChange,
  required,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (iso: string) => void;
  required?: boolean;
  disabled?: boolean;
}) {
  return (
    <label>
      {label}
      <input
        type="datetime-local"
        required={required}
        disabled={disabled}
        value={value ? schoolInput(value) : ""}
        onChange={(e) => {
          try {
            onChange(schoolISO(e.target.value));
          } catch {
            // Partial input: keep the last full value until a whole date and time is typed.
          }
        }}
      />
    </label>
  );
}

type Choice = { id: string; name: string; group?: string };
// Adds people to a list one at a time; names kept with an old record stay readable.
export function Picker({
  label,
  choices,
  chosen,
  onChange,
  groupLabel,
  kept = {},
}: {
  label: string;
  choices: Choice[];
  chosen: string[];
  onChange: (ids: string[]) => void;
  groupLabel?: string;
  kept?: Record<string, string>;
}) {
  const [group, setGroup] = useState(""),
    [pick, setPick] = useState("");
  const groups = [...new Set(choices.map((c) => c.group || ""))]
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, "ru", { numeric: true }));
  const names = new Map(choices.map((c) => [c.id, c.name]));
  const shown = choices.filter(
    (c) => (!groupLabel || c.group === group) && !chosen.includes(c.id),
  );
  return (
    <fieldset className="rg-picker">
      <legend>{label}</legend>
      <div className="rg-chips">
        {!chosen.length && <span>Никто не выбран.</span>}
        {chosen.map((id) => (
          <span key={id} className="rg-chip">
            {names.get(id) || kept[id] || "Недоступная запись"}
            <button
              type="button"
              aria-label={`Убрать: ${names.get(id) || kept[id] || id}`}
              onClick={() => onChange(chosen.filter((x) => x !== id))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="rg-row">
        {groupLabel && (
          <label>
            {groupLabel}
            <select
              value={group}
              onChange={(e) => {
                setGroup(e.target.value);
                setPick("");
              }}
            >
              <option value="">Выберите…</option>
              {groups.map((g) => (
                <option key={g}>{g}</option>
              ))}
            </select>
          </label>
        )}
        <label>
          Кого добавить
          <select value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Выберите…</option>
            {shown.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={!pick}
          onClick={() => {
            onChange([...chosen, pick]);
            setPick("");
          }}
        >
          Добавить
        </button>
      </div>
    </fieldset>
  );
}

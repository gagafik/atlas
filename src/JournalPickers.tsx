import { useEffect, useState } from "react";
import {
  daysBetween,
  type PeriodChoice,
  type PeriodSelection,
} from "./pupilJournal";

export const CUSTOM_PERIOD = "custom";

// A parent with several children chooses one; the choice holds for all four screens.
export function ChildPicker({
  students,
  value,
  onChange,
  label = "Ребёнок",
}: {
  students: { id: string; name: string; className?: string | null }[];
  value: string;
  onChange: (id: string) => void;
  label?: string;
}) {
  if (students.length < 2) return null;
  return (
    <label className="field pupil-picker">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {students.map((s) => (
          <option key={s.id} value={s.id}>
            {s.className ? `${s.name}, ${s.className}` : s.name}
          </option>
        ))}
      </select>
    </label>
  );
}

// The school's periods and the whole year, or a range of one's own up to a year long.
export function PeriodPicker({
  choices,
  value,
  onChange,
}: {
  choices: PeriodChoice[];
  value: PeriodSelection;
  onChange: (value: PeriodSelection) => void;
}) {
  const [draft, setDraft] = useState({ start: value.start, end: value.end });
  useEffect(
    () => setDraft({ start: value.start, end: value.end }),
    [value.start, value.end],
  );
  const problem =
    !draft.start || !draft.end
      ? ""
      : draft.end < draft.start
        ? "Конец периода раньше начала."
        : daysBetween(draft.start, draft.end) > 366
          ? "Выберите период до 367 дней."
          : "";
  const change = (next: { start: string; end: string }) => {
    setDraft(next);
    if (
      next.start &&
      next.end &&
      next.start <= next.end &&
      daysBetween(next.start, next.end) <= 366
    )
      onChange({ id: CUSTOM_PERIOD, ...next });
  };
  return (
    <div className="pupil-picker pupil-period-picker">
      <label className="field">
        <span>Период</span>
        <select
          value={value.id}
          onChange={(e) => {
            const choice = choices.find((c) => c.id === e.target.value);
            onChange(
              choice
                ? { id: choice.id, start: choice.start, end: choice.end }
                : { id: CUSTOM_PERIOD, start: value.start, end: value.end },
            );
          }}
        >
          {choices.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
          <option value={CUSTOM_PERIOD}>Свой период</option>
        </select>
      </label>
      {value.id === CUSTOM_PERIOD && (
        <>
          <label className="field">
            <span>С</span>
            <input
              type="date"
              value={draft.start}
              onChange={(e) => change({ ...draft, start: e.target.value })}
            />
          </label>
          <label className="field">
            <span>По</span>
            <input
              type="date"
              value={draft.end}
              onChange={(e) => change({ ...draft, end: e.target.value })}
            />
          </label>
          {problem && (
            <p role="alert" className="pupil-problem">
              {problem}
            </p>
          )}
        </>
      )}
    </div>
  );
}

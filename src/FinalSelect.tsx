import type { Named } from "./finalGradesTypes";

// A labelled choice from a list, with an empty first option; shared by the report-card screens.
export default function FinalSelect({
  label,
  value,
  onChange,
  items,
  empty = "Выберите",
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  items: Named[];
  empty?: string;
  disabled?: boolean;
}) {
  return (
    <label className="field">
      {label}
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{empty}</option>
        {items.map((i) => (
          <option key={i.id} value={i.id}>
            {i.name}
          </option>
        ))}
      </select>
    </label>
  );
}

import type { Named } from "./finalGradesTypes";

// A labelled choice from a list, with an empty first option; shared by the report-card screens.
export default function FinalSelect({
  label,
  value,
  onChange,
  items,
  empty = "Выберите",
  required = false,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  items: Named[];
  empty?: string;
  // No empty option once there is something to choose: the list always has a value.
  required?: boolean;
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
        {!(required && items.length) && <option value="">{empty}</option>}
        {items.map((i) => (
          <option key={i.id} value={i.id}>
            {i.name}
          </option>
        ))}
      </select>
    </label>
  );
}

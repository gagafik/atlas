import { useState } from "react";
import type { Named } from "./types";
import { named } from "./utils";

export function DateField({
  label,
  value,
  onChange,
  min,
  max,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  min: string;
  max: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="date"
        required
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
export function Multi({
  label,
  items,
  values,
  onChange,
}: {
  label: string;
  items: Named[];
  values: string[];
  onChange: (v: string[]) => void;
}) {
  const [search, setSearch] = useState("");
  return (
    <fieldset className="dated-multi">
      <legend>{label}</legend>
      <input
        aria-label={`Поиск: ${label}`}
        placeholder="Поиск"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div>
        {items
          .filter(
            (x) =>
              !search ||
              x.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
          )
          .map((x) => (
            <label className="check-line" key={x.id}>
              <input
                type="checkbox"
                checked={values.includes(x.id)}
                onChange={(e) =>
                  onChange(
                    e.target.checked
                      ? [...values, x.id]
                      : values.filter((v) => v !== x.id),
                  )
                }
              />
              {x.name}
            </label>
          ))}
      </div>
      <small>
        Выбрано: {values.length}.{" "}
        {values.map((id) => named(items, id)).join(", ")}
      </small>
    </fieldset>
  );
}

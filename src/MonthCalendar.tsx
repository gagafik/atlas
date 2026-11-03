import { useEffect, useState } from "react";
import { monthCells, shiftDate } from "./pupilJournal";

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const monthName = (day: string) =>
  new Date(`${day}T00:00:00Z`).toLocaleDateString("ru-RU", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
const nextMonth = (day: string, direction: 1 | -1) =>
  shiftDate(`${day.slice(0, 7)}-01`, direction > 0 ? 32 : -1).slice(0, 7) +
  "-01";

// A month of days, Monday first. A click selects a day; `mark` may give a day an extra class (a screen's own colouring).
export default function MonthCalendar({
  value,
  today,
  min,
  max,
  mark,
  onSelect,
}: {
  value: string;
  today: string;
  min?: string;
  max?: string;
  mark?: (day: string) => string | undefined;
  onSelect: (day: string) => void;
}) {
  const [anchor, setAnchor] = useState(value);
  useEffect(() => setAnchor(value), [value]);
  const month = anchor.slice(0, 7);
  return (
    <div className="month-calendar" aria-label="Календарь">
      <div className="month-calendar-head">
        <button
          type="button"
          className="secondary pupil-arrow"
          aria-label="Предыдущий месяц"
          onClick={() => setAnchor(nextMonth(anchor, -1))}
        >
          ‹
        </button>
        <strong>{monthName(anchor)}</strong>
        <button
          type="button"
          className="secondary pupil-arrow"
          aria-label="Следующий месяц"
          onClick={() => setAnchor(nextMonth(anchor, 1))}
        >
          ›
        </button>
      </div>
      <div className="month-calendar-grid">
        {WEEKDAYS.map((name, i) => (
          <span
            key={name}
            className={i > 4 ? "month-calendar-weekend" : undefined}
          >
            {name}
          </span>
        ))}
        {monthCells(`${month}-01`).map((cell, i) => {
          const off = (!!min && cell.date < min) || (!!max && cell.date > max);
          return (
            <button
              key={cell.date}
              type="button"
              disabled={off}
              aria-label={cell.date.split("-").reverse().join(".")}
              aria-pressed={cell.date === value}
              className={[
                "month-calendar-day",
                i % 7 > 4 ? "month-calendar-weekend" : "",
                cell.inMonth ? "" : "month-calendar-outside",
                cell.date === today ? "month-calendar-today" : "",
                cell.date === value ? "month-calendar-selected" : "",
                mark?.(cell.date) ?? "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => onSelect(cell.date)}
            >
              {Number(cell.date.slice(8))}
            </button>
          );
        })}
      </div>
    </div>
  );
}

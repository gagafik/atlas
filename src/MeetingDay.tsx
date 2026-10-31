import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import MeetingPrint from "./MeetingPrint";
import type { DayView, Options } from "./meetings";
import { addDays, clockTime, labels, shortDate, todaySchool } from "./meetings";

type Row = {
  key: string;
  start: string;
  end: string;
  kind: string;
  title: string;
  detail: string;
  place: string;
  warning: string;
};

// One chronological list for the date: timetable lessons, consultation and parent-meeting slots, class meetings.
function rowsOf(view: DayView): Row[] {
  const rows: Row[] = [
    ...view.lessons.map((l, i) => ({
      key: "lesson" + i,
      start: l.start,
      end: l.end,
      kind: l.activity === "lesson" ? "Урок" : "Занятость",
      title: [l.subject, l.group].filter(Boolean).join(" · "),
      detail: l.status === "cancelled" ? "Отменён" : "",
      place: l.rooms.join(", "),
      warning: "",
    })),
    ...view.slots.map((s) => ({
      key: "slot" + s.id,
      start: clockTime(s.startsAt),
      end: clockTime(s.endsAt),
      kind: s.kind === "consultation" ? "Консультация" : "Родительская встреча",
      title: s.eventTitle,
      detail: s.booking
        ? `${s.booking.studentName} · ${s.booking.parentName}`
        : labels[s.status] || s.status,
      place: s.roomName || s.location,
      warning: s.conflict || "",
    })),
    ...view.classMeetings.map((m) => ({
      key: "class" + m.id,
      start: clockTime(m.startsAt),
      end: clockTime(m.endsAt),
      kind: "Собрание класса",
      title: m.title,
      detail: `${m.classNames.join(", ")} · приглашено: ${m.pupils}`,
      place: m.place,
      warning: "",
    })),
  ];
  return rows.sort(
    (a, b) => a.start.localeCompare(b.start) || a.end.localeCompare(b.end),
  );
}

export default function MeetingDay({
  workspace,
  admin,
  options,
}: {
  workspace: string;
  admin: boolean;
  options: Options;
}) {
  const [date, setDate] = useState(todaySchool()),
    [hostId, setHostId] = useState(""),
    [view, setView] = useState<DayView | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const sequence = useRef(0);
  useEffect(() => {
    const n = ++sequence.current;
    setBusy(true);
    setError("");
    const query = new URLSearchParams({
      onDate: date,
      ...(hostId ? { hostId } : {}),
    });
    api(`meetings/day?${query}`, workspace)
      .then((r) => {
        if (n === sequence.current) setView(r);
      })
      .catch((e) => {
        if (n === sequence.current) {
          setView(null);
          setError(e.message);
        }
      })
      .finally(() => {
        if (n === sequence.current) setBusy(false);
      });
    return () => {
      ++sequence.current;
    };
  }, [workspace, date, hostId]);
  const rows = view ? rowsOf(view) : [];
  return (
    <section className="meeting-day" aria-label="День ведущего">
      <div className="meeting-toolbar">
        <button
          disabled={busy}
          onClick={() => setDate(addDays(date, -1))}
          aria-label="Предыдущий день"
        >
          ←
        </button>
        <label>
          День
          <input
            type="date"
            value={date}
            onChange={(e) => e.target.value && setDate(e.target.value)}
          />
        </label>
        <button
          disabled={busy}
          onClick={() => setDate(addDays(date, 1))}
          aria-label="Следующий день"
        >
          →
        </button>
        {admin && (
          <label>
            Сотрудник
            <select value={hostId} onChange={(e) => setHostId(e.target.value)}>
              <option value="">Мой день</option>
              {options.hosts.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <button disabled={busy || !rows.length} onClick={() => window.print()}>
          Печать
        </button>
      </div>
      {error && (
        <p role="alert" className="meeting-error">
          {error}
        </p>
      )}
      {busy && <p role="status">Загружаю…</p>}
      {view && (
        <>
          <h3>
            {view.hostName} · {shortDate(date)}
          </h3>
          <table className="meeting-table">
            <thead>
              <tr>
                <th>Время</th>
                <th>Что</th>
                <th>Подробности</th>
                <th>Место</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className={r.warning ? "warning" : undefined}>
                  <td data-label="Время">
                    {r.start}–{r.end}
                  </td>
                  <td data-label="Что">
                    <strong>{r.kind}</strong>
                    {r.title ? ` · ${r.title}` : ""}
                  </td>
                  <td data-label="Подробности">
                    {r.detail}
                    {r.warning && (
                      <span className="meeting-error"> {r.warning}</span>
                    )}
                  </td>
                  <td data-label="Место">{r.place}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && !busy && (
            <p>На этот день ничего не запланировано.</p>
          )}
          <MeetingPrint
            title={`День: ${view.hostName}`}
            subtitle={shortDate(date)}
            head={["Время", "Что", "Подробности", "Место"]}
            rows={rows.map((r) => [
              `${r.start}–${r.end}`,
              [r.kind, r.title].filter(Boolean).join(" · "),
              [r.detail, r.warning].filter(Boolean).join(" · "),
              r.place,
            ])}
          />
        </>
      )}
    </section>
  );
}

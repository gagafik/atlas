import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import { DRILL_PRINT, printNode } from "./printReport";
import { dayLabel, type Drill, type SafetyOptions } from "./safety";
import "./Safety.css";

export default function FireDrill({
  user,
  initialWorkspace,
  onClose,
}: {
  user: Account;
  initialWorkspace: string;
  onClose: () => void;
}) {
  const spaces = user.role === "admin" ? ["school", "demo"] : ["school"];
  const [workspace, setWorkspace] = useState(
    spaces.includes(initialWorkspace) ? initialWorkspace : spaces[0],
  );
  const [options, setOptions] = useState<SafetyOptions | null>(null),
    [report, setReport] = useState<Drill | null>(null);
  const [date, setDate] = useState(""),
    [time, setTime] = useState(""),
    [tick, setTick] = useState(0);
  const [marked, setMarked] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null),
    printable = useRef<HTMLDivElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  useEffect(() => {
    let live = true;
    setOptions(null);
    setReport(null);
    setError("");
    api("safety/options", workspace)
      .then((o: SafetyOptions) => {
        if (!live) return;
        setOptions(o);
        setDate(o.now.slice(0, 10));
        setTime(o.now.slice(11, 16));
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  const valid = /^\d{4}-\d{2}-\d{2}$/.test(date) && /^\d{2}:\d{2}$/.test(time);
  useEffect(() => {
    // A cleared or half-typed moment has nothing to list: drop the old result and send nothing.
    if (!options || !valid) {
      setLoading(false);
      setReport(null);
      return;
    }
    let live = true;
    setLoading(true);
    api(`safety/drill?at=${date}T${time}`, workspace)
      .then((r: Drill) => {
        if (!live) return;
        setReport(r);
        setMarked(new Set());
        setError("");
      })
      .catch((e) => {
        if (live) {
          setReport(null);
          setError(e.message);
        }
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [options, valid, date, time, workspace, tick]);
  const now = () => {
    if (!options) return;
    // The school clock was read when the screen opened; ask again for the current minute.
    api("safety/options", workspace)
      .then((o: SafetyOptions) => {
        setDate(o.now.slice(0, 10));
        setTime(o.now.slice(11, 16));
        setTick((n) => n + 1);
      })
      .catch((e) => setError(e.message));
  };
  const print = () => {
    setNotice("");
    if (
      !printNode(
        printable.current,
        `Противопожарные учения ${date} ${time}`,
        DRILL_PRINT,
      )
    )
      setNotice("Браузер не открыл окно печати");
  };
  const toggle = (id: string) =>
    setMarked((old) => {
      const next = new Set(old);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  return (
    <dialog
      ref={dialog}
      className="sf-dialog"
      aria-labelledby="fire-drill-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <div>
          <h2 id="fire-drill-title">Противопожарные учения</h2>
          <p>
            Кто должен быть в школе по расписанию дня, без отсутствующих,
            ушедших и забранных
          </p>
        </div>
        <button onClick={onClose}>Закрыть</button>
      </header>
      {error && (
        <p role="alert" className="sf-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="sf-controls">
        {spaces.length > 1 && (
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => setWorkspace(e.target.value)}
            >
              {spaces.map((s) => (
                <option key={s} value={s}>
                  {s === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          Дата
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </label>
        <label>
          Время
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
          />
        </label>
        <div className="sf-actions">
          <button onClick={now}>Сейчас</button>
          <button onClick={() => setTick((n) => n + 1)}>Обновить</button>
          <button onClick={print} disabled={!report}>
            Печать
          </button>
        </div>
      </div>
      {loading && <p role="status">Считаю…</p>}
      {report && (
        <div ref={printable}>
          <h3>
            Кто в школе: {dayLabel(report.date)} {report.time}
          </h3>
          <p className="sf-figures">
            В школе по расписанию: {report.totals.expected} · сотрудников:{" "}
            {report.totals.staff} · отсутствуют по данным школы:{" "}
            {report.totals.away}
          </p>
          {!report.classes.length && (
            <p>На это время по расписанию в школе нет учеников</p>
          )}
          <div className="sf-classes">
            {report.classes.map((c) => (
              <article key={c.classId} className="sf-class">
                <h3>
                  {c.className}
                  {c.classTeacher && (
                    <small className="sf-muted">
                      {" "}
                      · классный руководитель {c.classTeacher}
                    </small>
                  )}
                </h3>
                {c.places.map((p, i) => (
                  <section key={i} className="sf-place">
                    <h4>
                      {p.kind === "lesson"
                        ? `Кабинет ${p.rooms || "не указан"}`
                        : "Не на уроке (перемена или окно)"}
                      {p.responsible && ` · ${p.responsible}`}
                    </h4>
                    <ul className="sf-list">
                      {p.pupils.map((s) => (
                        <li key={s.id}>
                          <label className="sf-check">
                            <input
                              type="checkbox"
                              checked={marked.has(s.id)}
                              onChange={() => toggle(s.id)}
                              aria-label={`Отмечен на месте сбора: ${s.name}`}
                            />
                            <span>{s.name}</span>
                          </label>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
                {!!c.away.length && (
                  <p className="sf-away">
                    Отсутствуют по данным школы:{" "}
                    {c.away.map((s) => s.name).join(", ")}
                  </p>
                )}
              </article>
            ))}
          </div>
          {!!report.staff.length && (
            <section>
              <h3>Сотрудники</h3>
              <div className="sf-wide">
                <table className="sf-table">
                  <thead>
                    <tr>
                      <th>Сотрудник</th>
                      <th>Урок/Дежурство</th>
                      <th>Место</th>
                      <th>Классы</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.staff.map((p, i) => (
                      <tr key={i}>
                        <td data-label="Сотрудник">{p.name}</td>
                        <td data-label="Урок/Дежурство">
                          {p.kind === "duty" ? "Дежурство" : "Урок"}
                        </td>
                        <td data-label="Место">{p.place || "—"}</td>
                        <td data-label="Классы">{p.classes || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      )}
    </dialog>
  );
}

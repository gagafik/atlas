import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import { printNode } from "./printReport";
import {
  KINDS,
  OUTCOMES,
  clip,
  hours,
  monthOf,
  validPeriod,
  weekOf,
  within,
  type WorkloadFigures,
  type WorkloadLesson,
  type WorkloadOptions,
  type WorkloadReport,
  type WorkloadTeacher,
} from "./workloadReport";
import "./TeacherWorkload.css";

const WORKLOAD_PRINT =
  "body{font:14px sans-serif;padding:24px;color:#111}h3,h4,p{margin:8px 0}table{border-collapse:collapse;width:100%}th,td{border:1px solid #999;padding:4px 8px;text-align:left}button{border:0;background:none;font:inherit;padding:0}dl{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 16px}dt{font-size:12px;color:#555}dd{margin:0;font-weight:bold}small{display:block;font-weight:normal}";
const VIEWS = [
  ["teachers", "По учителям"],
  ["weeks", "По неделям"],
  ["lessons", "По урокам"],
] as const;
const dayLabel = (iso: string) =>
  `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;
const signed = (n: number) => (n > 0 ? `+${n}` : String(n));

function Tiles({ t }: { t: WorkloadFigures }) {
  return (
    <dl className="workload-figures">
      <div>
        <dt>По расписанию на дату</dt>
        <dd>{t.plannedToDate}</dd>
      </div>
      <div>
        <dt>Проведено уроков</dt>
        <dd>{t.taught}</dd>
        <small>
          свои {t.own}, замены {t.substitutions}, дополнительные {t.extra}, по
          журналу вне расписания {t.journalOnly}
        </small>
      </div>
      <div>
        <dt>Часы по звонкам</dt>
        <dd>{hours(t.minutes)}</dd>
        <small>{t.minutes} мин</small>
      </div>
      <div>
        <dt>Разница с расписанием</dt>
        <dd>{signed(t.delta)}</dd>
      </div>
      <div>
        <dt>Пропущено</dt>
        <dd>{t.missed}</dd>
        <small>
          при отсутствии {t.missedAbsent}, без замены {t.uncovered}, отменено{" "}
          {t.cancelled}
        </small>
      </div>
      <div>
        <dt>Предстоит</dt>
        <dd>{t.upcoming}</dd>
      </div>
      <div>
        <dt>Подтверждено журналом</dt>
        <dd>{t.confirmed}</dd>
        <small>расхождений с журналом: {t.differs}</small>
      </div>
      <div>
        <dt>Присоединено к другим урокам</dt>
        <dd>{t.merged}</dd>
        <small>часов не добавляет</small>
      </div>
    </dl>
  );
}

function Lessons({ items }: { items: WorkloadLesson[] }) {
  const days = items.reduce<[string, WorkloadLesson[]][]>((all, lesson) => {
    const last = all[all.length - 1];
    if (last && last[0] === lesson.date) last[1].push(lesson);
    else all.push([lesson.date, [lesson]]);
    return all;
  }, []);
  return (
    <div className="workload-lessons">
      {days.map(([date, lessons]) => (
        <section key={date}>
          <h4>{dayLabel(date)}</h4>
          <ul>
            {lessons.map((l, i) => (
              <li key={i}>
                <strong>
                  {l.start}–{l.end}
                </strong>{" "}
                {l.group} · {l.subject}
                <small>
                  {OUTCOMES[l.outcome] ?? l.outcome}
                  {l.kind && l.outcome === "taught"
                    ? ` · ${KINDS[l.kind] ?? l.kind}`
                    : ""}
                  {l.outcome === "taught" && !l.confirmed
                    ? " · журнал не опубликован"
                    : ""}
                  {l.differs ? " · расходится с расписанием" : ""}
                  {l.absent && l.outcome !== "taught"
                    ? " · учитель отсутствует"
                    : ""}
                </small>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

// Lessons taught by date: the timetable of that day, substitutions and cancellations, confirmed by the published journal.
export default function TeacherWorkload({
  user,
  initialWorkspace,
  onClose,
}: {
  user: Account;
  initialWorkspace: string;
  onClose: () => void;
}) {
  const spaces =
    user.role === "admin"
      ? ["school", "demo"]
      : Array.from(
          new Set(
            user.grants
              .filter((g) => g.view === "teacher")
              .map((g) => g.workspace),
          ),
        );
  const [workspace, setWorkspace] = useState(
    spaces.includes(initialWorkspace)
      ? initialWorkspace
      : spaces[0] || "school",
  );
  const [options, setOptions] = useState<WorkloadOptions | null>(null),
    [report, setReport] = useState<WorkloadReport | null>(null);
  const [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [teacherId, setTeacherId] = useState(""),
    [view, setView] = useState<string>("teachers");
  const [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null),
    printable = useRef<HTMLDivElement>(null);
  const admin = user.role === "admin";
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  useEffect(() => {
    let live = true;
    setOptions(null);
    setReport(null);
    setError("");
    setTeacherId("");
    api("staff-time/workload/options", workspace)
      .then((o: WorkloadOptions) => {
        if (!live) return;
        setOptions(o);
        const first =
          within(monthOf(o.today), o) ?? clip(monthOf(o.yearStart), o);
        setStart(first.start);
        setEnd(first.end);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  const counted = validPeriod(start, end);
  useEffect(() => {
    // A date being edited (empty or after the other) has nothing to count: drop the old report and any pending wait.
    if (!options || !counted) {
      setLoading(false);
      setReport(null);
      return;
    }
    let live = true;
    setLoading(true);
    api(
      `staff-time/workload?start=${start}&end=${end}&teacherId=${encodeURIComponent(teacherId)}`,
      workspace,
    )
      .then((r: WorkloadReport) => {
        if (!live) return;
        setReport(r);
        setError("");
        // The server clips the period to the school year: the inputs show what was counted.
        if (r.start !== start) setStart(r.start);
        if (r.end !== end) setEnd(r.end);
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
  }, [options, counted, start, end, teacherId, workspace]);
  const preset = (range: { start: string; end: string }) => {
    const found = options ? within(range, options) : range;
    if (!found) {
      setError("Эти даты вне учебного года.");
      return;
    }
    setStart(found.start);
    setEnd(found.end);
  };
  const matched =
    options?.periods.find((p) => p.start === start && p.end === end)?.id ?? "";
  const detailed =
    !!report &&
    report.teachers.length > 0 &&
    report.teachers.every((t) => t.weeks);
  const title = admin ? "Нагрузка учителей" : "Моя нагрузка";
  const heading = detailed
    ? `${admin ? "Нагрузка" : "Моя нагрузка"}: ${report!.teachers.map((t) => t.name).join(", ")}`
    : title;
  const exportUrl = `/api/staff-time/workload/export?workspace=${encodeURIComponent(workspace)}&start=${start}&end=${end}&teacherId=${encodeURIComponent(teacherId)}&view=${view}`;
  const pick = (t: WorkloadTeacher) => setTeacherId(t.teacherId);
  return (
    <dialog
      ref={dialog}
      className="teacher-workload"
      aria-labelledby="teacher-workload-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <div>
          <h2 id="teacher-workload-title">{title}</h2>
          <p>Уроки по дате: расписание того дня, замены, отмены и журнал</p>
        </div>
        <button onClick={onClose}>Закрыть</button>
      </header>
      {error && (
        <p role="alert" className="workload-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="workload-controls">
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
        {options && (options.canSeeAll || options.teachers.length > 1) && (
          <label>
            Учитель
            <select
              value={teacherId}
              onChange={(e) => setTeacherId(e.target.value)}
            >
              <option value="">
                {options.canSeeAll ? "Все учителя" : "Все мои"}
              </option>
              {options.teachers.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {options && !!options.periods.length && (
          <label>
            Учебный период
            <select
              value={matched}
              onChange={(e) => {
                const found = options.periods.find(
                  (p) => p.id === e.target.value,
                );
                if (found) preset(found);
              }}
            >
              <option value="">Свой период</option>
              {options.periods.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          С
          <input
            type="date"
            value={start}
            min={options?.yearStart}
            max={options?.yearEnd}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          По
          <input
            type="date"
            value={end}
            min={options?.yearStart}
            max={options?.yearEnd}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <div className="workload-presets">
          <button
            disabled={!options}
            onClick={() => options && preset(weekOf(options.today))}
          >
            Эта неделя
          </button>
          <button
            disabled={!options}
            onClick={() => options && preset(monthOf(options.today))}
          >
            Этот месяц
          </button>
        </div>
      </div>
      <p className="muted">
        Считается по дате: расписание того дня, замены и отмены. В «проведено»
        входят только завершившиеся уроки; публикация журнала подтверждает урок
        и показывает, кто его провёл. Уроки без публикации считаются по
        расписанию и помечены как неподтверждённые. Урок учителя с внесённым
        отсутствием и без замены считается «Без замены» и «Пропущено» сразу, как
        только отсутствие внесено, в том числе на будущие даты, пока не
        назначена замена.
      </p>
      {options && !counted && (
        <p role="status">
          Укажите обе даты периода: конец не должен быть раньше начала.
        </p>
      )}
      {loading && <p role="status">Считаю нагрузку…</p>}
      {report && counted && !loading && (
        <>
          <div className="workload-actions">
            <label>
              Выгрузка
              <select value={view} onChange={(e) => setView(e.target.value)}>
                {VIEWS.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <a href={exportUrl} download>
              Скачать CSV
            </a>
            <button
              onClick={() => {
                setNotice(
                  printNode(
                    printable.current,
                    `${heading} ${report.start}`,
                    WORKLOAD_PRINT,
                  )
                    ? ""
                    : "Разрешите открытие окна для печати.",
                );
              }}
            >
              Печать
            </button>
          </div>
          {!report.teachers.length && <p>За период нет уроков и замен.</p>}
          <div ref={printable}>
            <h3>
              {heading} · {dayLabel(report.start)} — {dayLabel(report.end)}
            </h3>
            {!detailed && !!report.teachers.length && (
              <div className="workload-table">
                <table>
                  <thead>
                    <tr>
                      <th>Учитель</th>
                      <th>По расписанию на дату</th>
                      <th>Проведено</th>
                      <th>Часы</th>
                      <th>Замены</th>
                      <th>Пропущено</th>
                      <th>Без замены</th>
                      <th>Подтверждено журналом</th>
                      <th>Предстоит</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.teachers.map((t) => (
                      <tr key={t.teacherId}>
                        <th scope="row">
                          <button
                            className="workload-name"
                            onClick={() => pick(t)}
                          >
                            {t.name}
                          </button>
                        </th>
                        <td>{t.plannedToDate}</td>
                        <td>{t.taught}</td>
                        <td>{hours(t.minutes)}</td>
                        <td>{t.substitutions}</td>
                        <td>{t.missed}</td>
                        <td>{t.uncovered}</td>
                        <td>{t.confirmed}</td>
                        <td>{t.upcoming}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th scope="row">Итого</th>
                      <td>{report.totals.plannedToDate}</td>
                      <td>{report.totals.taught}</td>
                      <td>{hours(report.totals.minutes)}</td>
                      <td>{report.totals.substitutions}</td>
                      <td>{report.totals.missed}</td>
                      <td>{report.totals.uncovered}</td>
                      <td>{report.totals.confirmed}</td>
                      <td>{report.totals.upcoming}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
            {detailed &&
              report.teachers.map((t) => (
                <section key={t.teacherId} className="workload-teacher">
                  {report.teachers.length > 1 && <h4>{t.name}</h4>}
                  <Tiles t={t} />
                  <div className="workload-table">
                    <table>
                      <thead>
                        <tr>
                          <th>Неделя</th>
                          <th>По расписанию на дату</th>
                          <th>Проведено</th>
                          <th>Часы</th>
                          <th>Замены</th>
                          <th>Пропущено</th>
                          <th>Предстоит</th>
                          <th>Разница</th>
                        </tr>
                      </thead>
                      <tbody>
                        {t.weeks!.map((w) => (
                          <tr key={w.start}>
                            <th scope="row">
                              {dayLabel(w.start).slice(0, 5)} –{" "}
                              {dayLabel(w.end).slice(0, 5)}
                            </th>
                            <td>{w.plannedToDate}</td>
                            <td>{w.taught}</td>
                            <td>{hours(w.minutes)}</td>
                            <td>{w.substitutions}</td>
                            <td>{w.missed}</td>
                            <td>{w.upcoming}</td>
                            <td>{signed(w.delta)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ))}
          </div>
          {detailed &&
            report.teachers.map((t) => (
              <section key={t.teacherId}>
                {report.teachers.length > 1 && <h4>{t.name}</h4>}
                {t.lessons?.length ? (
                  <Lessons items={t.lessons} />
                ) : (
                  <p>За период уроков нет.</p>
                )}
              </section>
            ))}
        </>
      )}
    </dialog>
  );
}

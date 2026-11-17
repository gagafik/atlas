import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import { printNode } from "./printReport";
import {
  LessonsTable,
  LESSON_PAGE,
  TeachersTable,
  WeeksTable,
  WorkloadFiguresStrip,
  workloadNameId,
} from "./WorkloadTables";
import {
  LESSON_FILTERS,
  clip,
  dayLabel,
  lessonCounts,
  monthOf,
  reportWeeks,
  validPeriod,
  weekLabel,
  weekOf,
  within,
  type LessonFilter,
  type WorkloadOptions,
  type WorkloadReport,
  type WorkloadTeacher,
  type WorkloadWeek,
} from "./workloadReport";
import "./TeacherWorkload.css";

const WORKLOAD_PRINT =
  "body{font:14px sans-serif;padding:24px;color:#111}h3,h4,p{margin:8px 0}table{border-collapse:collapse;width:100%}th,td{border:1px solid #999;padding:4px 8px;text-align:left}thead{display:table-header-group}tr{break-inside:avoid}button{border:0;background:none;font:inherit;padding:0}dl{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 16px}dt{font-size:12px;color:#555}dd{margin:0;font-weight:bold}small{display:block;font-weight:normal}.workload-noprint{display:none}";
const VIEWS = [
  ["teachers", "По учителям"],
  ["weeks", "По неделям"],
  ["lessons", "По урокам"],
] as const;

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
    [former, setFormer] = useState<{ id: string; name: string } | null>(null),
    [view, setView] = useState<string>("teachers");
  // The tab of the detailed report, the week and the filter of the lessons tab, and how many rows each teacher shows.
  const [tab, setTab] = useState<"weeks" | "lessons">("weeks"),
    [weekStart, setWeekStart] = useState(""),
    [filter, setFilter] = useState<LessonFilter>("all"),
    [limits, setLimits] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null),
    printable = useRef<HTMLDivElement>(null),
    headingRef = useRef<HTMLHeadingElement>(null),
    // Where the focus goes when the report that was asked for has loaded: the heading or the id of a button.
    focusAfter = useRef<string | null>(null);
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
    setFormer(null);
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
  // Another workspace or teacher starts the report over; a new period keeps the tab.
  useEffect(() => {
    setTab("weeks");
    setWeekStart("");
    setFilter("all");
  }, [workspace, teacherId]);
  // A week that the new report does not have returns the select to the whole period; the rows shown start over.
  useEffect(() => {
    if (
      report &&
      weekStart &&
      !reportWeeks(report.teachers).some((w) => w.start === weekStart)
    )
      setWeekStart("");
  }, [report, weekStart]);
  useEffect(() => {
    setLimits({});
  }, [report, weekStart, filter]);
  // After a teacher is opened or closed the focus follows the page, once the new report is there.
  useEffect(() => {
    const target = focusAfter.current;
    if (!target || loading) return;
    focusAfter.current = null;
    if (!report) return;
    if (target === "heading") headingRef.current?.focus();
    else document.getElementById(target)?.focus();
  }, [report, loading]);
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
  // A teacher who has left the roster still has a row for the periods he taught in; the select offers him while he is open.
  const pick = (t: WorkloadTeacher) => {
    focusAfter.current = "heading";
    setTeacherId(t.teacherId);
    setFormer(
      options?.teachers.some((x) => x.id === t.teacherId)
        ? null
        : { id: t.teacherId, name: t.name },
    );
  };
  const back = () => {
    focusAfter.current = workloadNameId(teacherId);
    setTeacherId("");
    setFormer(null);
  };
  const weeks = report ? reportWeeks(report.teachers) : [],
    week = weeks.find((w) => w.start === weekStart) ?? null,
    lessonsOf = report?.teachers.flatMap((t) => t.lessons ?? []) ?? [],
    counts = lessonCounts(lessonsOf, week),
    many = (report?.teachers.length ?? 0) > 1,
    canBack =
      !!teacherId &&
      !!options &&
      (options.canSeeAll || options.teachers.length > 1);
  const openWeek = (w: WorkloadWeek) => {
    setWeekStart(w.start);
    setTab("lessons");
  };
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
      <div className="workload-body">
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
                onChange={(e) => {
                  focusAfter.current = null;
                  setTeacherId(e.target.value);
                  if (e.target.value !== former?.id) setFormer(null);
                }}
              >
                <option value="">
                  {options.canSeeAll ? "Все учителя" : "Все мои"}
                </option>
                {options.teachers.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
                {former && (
                  <option value={former.id}>{former.name} (вне состава)</option>
                )}
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
        <details className="workload-help">
          <summary>Как считается</summary>
          <p className="muted">
            Считается по дате: расписание того дня, замены и отмены. В
            «проведено» входят только завершившиеся уроки; публикация журнала,
            сделанная с начала урока, подтверждает его и показывает, кто его
            провёл (тема, добавленная заранее, не подтверждает). Уроки без такой
            публикации считаются по расписанию и помечены как неподтверждённые.
            Урок учителя с внесённым отсутствием и без замены считается «Без
            замены» и «Пропущено» сразу, как только отсутствие внесено, в том
            числе на будущие даты, пока не назначена замена.
          </p>
        </details>
        {options && !counted && (
          <p role="status">
            Укажите обе даты периода: конец не должен быть раньше начала.
          </p>
        )}
        {loading && <p role="status">Считаю нагрузку…</p>}
        {report && counted && !loading && (
          <div ref={printable}>
            <div className="workload-head">
              <div className="workload-title">
                {canBack && (
                  <button
                    className="workload-back workload-noprint"
                    onClick={back}
                  >
                    {options?.canSeeAll ? "← Все учителя" : "← Все мои"}
                  </button>
                )}
                <h3 ref={headingRef} tabIndex={-1}>
                  {heading} · {dayLabel(report.start)} — {dayLabel(report.end)}
                </h3>
              </div>
              <div className="workload-actions workload-noprint">
                <label>
                  Выгрузка
                  <select
                    value={view}
                    onChange={(e) => setView(e.target.value)}
                  >
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
            </div>
            {!report.teachers.length && <p>За период нет уроков и замен.</p>}
            {!detailed && !!report.teachers.length && (
              <TeachersTable
                teachers={report.teachers}
                totals={report.totals}
                onPick={pick}
              />
            )}
            {detailed && (
              <div className="workload-tabs workload-noprint">
                <div className="segmented" role="group" aria-label="Вид отчёта">
                  <button
                    type="button"
                    className={tab === "weeks" ? "selected" : ""}
                    aria-pressed={tab === "weeks"}
                    onClick={() => setTab("weeks")}
                  >
                    По неделям
                  </button>
                  <button
                    type="button"
                    className={tab === "lessons" ? "selected" : ""}
                    aria-pressed={tab === "lessons"}
                    onClick={() => setTab("lessons")}
                  >
                    Уроки <span>{lessonsOf.length}</span>
                  </button>
                </div>
                {tab === "lessons" && (
                  <div className="workload-lessons-bar">
                    <label>
                      Неделя
                      <select
                        value={weekStart}
                        onChange={(e) => setWeekStart(e.target.value)}
                      >
                        <option value="">Весь период</option>
                        {weeks.map((w) => (
                          <option key={w.start} value={w.start}>
                            {weekLabel(w)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div
                      className="workload-chips"
                      role="group"
                      aria-label="Итог урока"
                    >
                      {LESSON_FILTERS.map(([id, label]) => (
                        <button
                          key={id}
                          type="button"
                          className={
                            filter === id
                              ? "workload-chip selected"
                              : "workload-chip"
                          }
                          aria-pressed={filter === id}
                          onClick={() => setFilter(id)}
                        >
                          {label} {counts[id]}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
            {detailed &&
              report.teachers.map((t) => (
                <section key={t.teacherId} className="workload-teacher">
                  {many && <h4>{t.name}</h4>}
                  <WorkloadFiguresStrip t={t} />
                  {tab === "weeks" ? (
                    <WeeksTable weeks={t.weeks!} onOpenWeek={openWeek} />
                  ) : (
                    <LessonsTable
                      lessons={t.lessons ?? []}
                      filter={filter}
                      week={week}
                      limit={limits[t.teacherId] ?? LESSON_PAGE}
                      onMore={() =>
                        setLimits((all) => ({
                          ...all,
                          [t.teacherId]:
                            (all[t.teacherId] ?? LESSON_PAGE) + LESSON_PAGE,
                        }))
                      }
                    />
                  )}
                </section>
              ))}
          </div>
        )}
      </div>
    </dialog>
  );
}

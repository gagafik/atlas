import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { useAssessmentCategories } from "./assessmentCategories";
import { weekOf } from "./attendanceGrid";
import { placeInSlots, profileFor, slotsFor } from "./bells";
import type { JournalFocus } from "./JournalPanel";
import {
  loadRemainingReport,
  mergeReportPage,
  type JournalOptions,
  type JournalReport,
  type JournalReportPage,
} from "./journal";
import MonthCalendar from "./MonthCalendar";
import PublishedRows from "./PublishedRows";
import {
  dayText,
  mergeDay,
  shiftDate,
  weekdayName,
  type DayRow,
  type TimetableRow,
} from "./pupilJournal";

const STATUS: Record<string, string> = {
  cancelled: "Отменено",
  changed: "Изменено",
  added: "Разовый урок",
};

// «Классный журнал» of a pupil or a parent: the lessons of one day with what the teachers published.
export default function PupilDay({
  workspace,
  options,
  child,
  today,
  focus,
}: {
  workspace: string;
  options: JournalOptions;
  child: string;
  today: string;
  focus?: JournalFocus;
}) {
  const clamp = (day: string) =>
    day < options.yearStart
      ? options.yearStart
      : day > options.yearEnd
        ? options.yearEnd
        : day;
  const [day, setDay] = useState(() => clamp(focus?.day ?? today));
  // A notification points at a lesson of its own day.
  useEffect(() => {
    if (focus?.day) setDay(clamp(focus.day));
  }, [focus]);
  const categories = useAssessmentCategories(workspace);
  const week = weekOf(day);
  const timetableKey = `${child}|${day}`,
    weekKey = `${child}|${week.start}`;
  const [timetable, setTimetable] = useState<{
    key: string;
    rows: TimetableRow[];
  } | null>(null);
  const [report, setReport] = useState<{
    key: string;
    report: JournalReport | null;
    error: string;
  } | null>(null);
  const [epoch, setEpoch] = useState(0);
  const weeks = useRef(new Map<string, JournalReport>());
  useEffect(() => {
    let live = true;
    api(
      `portal/schedule?${new URLSearchParams({ view: "student", entityId: child, day })}`,
      workspace,
    )
      .then((r: { rows: TimetableRow[] }) => {
        if (live) setTimetable({ key: timetableKey, rows: r.rows });
      })
      // A pupil who left the schedule, or a link the school ended: the published records still show.
      .catch(() => {
        if (live) setTimetable({ key: timetableKey, rows: [] });
      });
    return () => {
      live = false;
    };
  }, [workspace, timetableKey, epoch]);
  useEffect(() => {
    const kept = weeks.current.get(weekKey);
    if (kept) {
      setReport({ key: weekKey, report: kept, error: "" });
      return;
    }
    let live = true;
    const params = (after?: string) =>
      new URLSearchParams({
        studentId: child,
        start: week.start,
        end: week.end,
        limit: "200",
        ...(after ? { after } : {}),
      });
    api(`journal/report?${params()}`, workspace)
      .then(async (page: JournalReportPage) => {
        const full = await loadRemainingReport(
          mergeReportPage(null, page),
          (after) => api(`journal/report?${params(after)}`, workspace),
        );
        weeks.current.set(weekKey, full);
        if (live) setReport({ key: weekKey, report: full, error: "" });
      })
      .catch((e: Error) => {
        if (live) setReport({ key: weekKey, report: null, error: e.message });
      });
    return () => {
      live = false;
    };
  }, [workspace, weekKey, epoch]);
  const ready = timetable?.key === timetableKey && report?.key === weekKey;
  const profile = profileFor(options, child);
  const lessons = useMemo(() => {
    if (!ready) return [];
    const published =
      report!.report?.rows.filter((r) => r.lesson.date === day) ?? [];
    return placeInSlots<DayRow>(
      profile ? slotsFor(profile, day) : [],
      mergeDay(timetable!.rows, published),
    ).filter((slot) => slot.items.length);
  }, [ready, timetable, report, day, profile]);
  // The lesson a notification named comes into view once the day is on screen.
  useEffect(() => {
    if (!ready || !focus?.lessonKey) return;
    document
      .getElementById(`lesson-${focus.lessonKey}`)
      ?.scrollIntoView({ block: "center" });
  }, [ready, focus, day]);
  const className = options.students.find((s) => s.id === child)?.className;
  const move = (days: number) => setDay((old) => clamp(shiftDate(old, days)));
  return (
    <section className="pupil-screen" aria-label="Классный журнал">
      <div className="pupil-day-layout">
        <div className="pupil-day-main">
          <header className="pupil-day-head">
            <h2>
              {className ? `${className}, ` : ""}
              {weekdayName(day)}, {dayText(day)}
            </h2>
            <div className="pupil-actions">
              <button
                type="button"
                className="pupil-arrow"
                aria-label="Предыдущий день"
                disabled={day <= options.yearStart}
                onClick={() => move(-1)}
              >
                ‹
              </button>
              <button
                type="button"
                className="pupil-arrow"
                aria-label="Следующий день"
                disabled={day >= options.yearEnd}
                onClick={() => move(1)}
              >
                ›
              </button>
              <button type="button" onClick={() => setDay(clamp(today))}>
                Сегодня
              </button>
              <input
                type="date"
                aria-label="Дата журнала"
                value={day}
                min={options.yearStart}
                max={options.yearEnd}
                onChange={(e) =>
                  e.target.value && setDay(clamp(e.target.value))
                }
              />
              <button
                type="button"
                onClick={() => {
                  weeks.current.clear();
                  setEpoch((n) => n + 1);
                }}
              >
                Обновить
              </button>
              <button type="button" onClick={() => window.print()}>
                Печать
              </button>
            </div>
          </header>
          {report?.key === weekKey && report.error && (
            <p role="alert" className="error-banner">
              {report.error}
            </p>
          )}
          {!ready && !report?.error && <p role="status">Загружаю уроки…</p>}
          {ready && !lessons.length && !report?.error && (
            <p className="empty-state">Уроков нет.</p>
          )}
          <div className="pupil-lessons">
            {lessons.map((slot) =>
              slot.items.map((row) => (
                <article
                  key={row.key}
                  id={row.record ? `lesson-${row.record.lessonKey}` : undefined}
                  className={`pupil-lesson${
                    focus?.lessonKey &&
                    row.record?.lessonKey === focus.lessonKey
                      ? " pupil-lesson-focus"
                      : ""
                  }${row.status === "cancelled" ? " pupil-lesson-cancelled" : ""}`}
                >
                  <div className="pupil-lesson-slot">
                    <strong>{slot.label}</strong>
                    <span>
                      {row.start}–{row.end}
                    </span>
                  </div>
                  <div className="pupil-lesson-body">
                    <h3>
                      {row.subject}
                      {STATUS[row.status] && (
                        <span className={`badge pupil-status-${row.status}`}>
                          {STATUS[row.status]}
                        </span>
                      )}
                    </h3>
                    <p className="pupil-muted">
                      {[row.group, row.teachers, row.rooms]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                    {row.record ? (
                      <PublishedRows
                        rows={[row.record]}
                        workspace={workspace}
                        categories={categories}
                        codeNames={report?.report?.absenceCodes}
                        bare
                      />
                    ) : (
                      row.status !== "cancelled" && (
                        <p className="pupil-muted">
                          Запись урока ещё не опубликована
                        </p>
                      )
                    )}
                  </div>
                </article>
              )),
            )}
          </div>
        </div>
        <MonthCalendar
          value={day}
          today={today}
          min={options.yearStart}
          max={options.yearEnd}
          onSelect={setDay}
        />
      </div>
    </section>
  );
}

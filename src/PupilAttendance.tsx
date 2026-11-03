import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import AbsencePanel from "./AbsencePanel";
import { dayTitle } from "./attendanceGrid";
import { profileFor, slotsFor } from "./bells";
import { PeriodPicker } from "./JournalPickers";
import {
  attendanceText,
  lateShare,
  lessonsText,
  type JournalOptions,
} from "./journal";
import {
  foldAttendance,
  markKind,
  type MarkKind,
  type PeriodChoice,
  type PeriodSelection,
  type PupilAttendanceRow,
} from "./pupilJournal";
import { usePupilPeriod } from "./usePupilPeriod";

const MARKS: Record<MarkKind, { sign: string; name: string }> = {
  present: { sign: "✓", name: "Присутствовал" },
  absent: { sign: "Н", name: "Отсутствовал" },
  excused: { sign: "У", name: "Отсутствовал по уважительной причине" },
  late: { sign: "О", name: "Опоздал" },
  left: { sign: "→", name: "Ушёл раньше" },
  school: { sign: "Ш", name: "Представляет школу" },
  unmarked: { sign: "?", name: "Не отмечено" },
};
interface NoteOptions {
  parent: boolean;
  students: {
    id: string;
    electronicNotes?: boolean;
    classTeacher?: string | null;
  }[];
}

function Mark({
  row,
  codes,
}: {
  row: PupilAttendanceRow;
  codes: Record<string, string>;
}) {
  const kind = markKind(row);
  const text = `${attendanceText(row, row, codes)}. ${row.subject}${
    row.teachers ? `, ${row.teachers}` : ""
  }, ${row.start}–${row.end}${
    row.byNote ? ". Объяснительная принята школой" : ""
  }`;
  const share =
    kind === "late" ? lateShare(row.lateMinutes, row.start, row.end) : 0;
  return (
    <span
      className={`pupil-att pupil-att-${kind}`}
      role="img"
      aria-label={text}
      title={text}
    >
      <b>{MARKS[kind].sign}</b>
      {kind === "late" && (
        <small>
          {row.lateMinutes} мин
          {share > 0 ? ` · ${Math.round(share * 100)}%` : ""}
        </small>
      )}
      {kind === "left" && row.leftAt && <small>в {row.leftAt}</small>}
    </span>
  );
}

const monthTitle = (date: string) =>
  new Date(`${date}T00:00:00Z`).toLocaleDateString("ru-RU", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });

export default function PupilAttendance({
  workspace,
  options,
  child,
  choices,
  period,
  onPeriod,
  showNotes,
}: {
  workspace: string;
  options: JournalOptions;
  child: string;
  choices: PeriodChoice[];
  period: PeriodSelection;
  onPeriod: (value: PeriodSelection) => void;
  // Opened by an absence-note notification: the notes are open and in sight.
  showNotes: boolean;
}) {
  const data = usePupilPeriod(workspace, child, period.start, period.end);
  const profile = profileFor(options, child);
  const fold = useMemo(
    () =>
      data.data
        ? foldAttendance(data.data.attendance, (date) =>
            profile ? slotsFor(profile, date) : [],
          )
        : null,
    [data.data, profile],
  );
  const [notes, setNotes] = useState<NoteOptions | null>(null);
  const hasNotes = !!options.panels?.absences;
  useEffect(() => {
    if (!hasNotes) return;
    let live = true;
    api("journal/absences/options", workspace)
      .then((r: NoteOptions) => {
        if (live) setNotes(r);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [workspace, hasNotes]);
  const [notesOpen, setNotesOpen] = useState(showNotes),
    [compose, setCompose] = useState(false),
    notesRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!showNotes) return;
    setNotesOpen(true);
    notesRef.current?.scrollIntoView({ block: "start" });
  }, [showNotes]);
  const pupil = notes?.students.find((s) => s.id === child);
  const parent = !!notes?.parent;
  const notesOff = parent && pupil?.electronicNotes === false;
  const summary = data.data?.summary;
  const totals: [string, string][] = summary
    ? [
        [
          "Всего пропусков",
          String(summary.attendance.absent + summary.attendance.excused),
        ],
        ["По уважительной причине", String(summary.attendance.excused)],
        ["Без уважительной причины", String(summary.attendance.absent)],
        [
          "Опоздания",
          `${summary.attendance.late}${
            summary.lateMinutes ? `, ${summary.lateMinutes} мин` : ""
          }${summary.lateShare ? ` · ${lessonsText(summary.lateShare)}` : ""}`,
        ],
        ["Ушёл раньше", String(summary.attendance.left)],
        ["Не отмечено", String(summary.attendance.unmarked)],
      ]
    : [];
  return (
    <section className="pupil-screen" aria-label="Посещаемость">
      <div className="pupil-toolbar">
        <PeriodPicker choices={choices} value={period} onChange={onPeriod} />
        <div className="pupil-actions">
          <button type="button" disabled={data.loading} onClick={data.reload}>
            Обновить
          </button>
          <button type="button" onClick={() => window.print()}>
            Печать
          </button>
          {hasNotes && (
            <button
              type="button"
              aria-pressed={notesOpen}
              onClick={() => setNotesOpen((open) => !open)}
            >
              Записки об отсутствии
            </button>
          )}
          {parent && (
            <button
              type="button"
              className="primary"
              onClick={() => {
                setNotesOpen(true);
                setCompose(true);
              }}
            >
              Добавить новую записку об отсутствии
            </button>
          )}
        </div>
      </div>
      {data.error && (
        <p role="alert" className="error-banner">
          {data.error}
        </p>
      )}
      {data.loading && !data.data && (
        <p role="status">Загружаю посещаемость…</p>
      )}
      {!!totals.length && (
        <dl className="pupil-totals" aria-label="Итоги за период">
          {totals.map(([name, value]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {fold && !fold.days.length && (
        <p className="empty-state">Опубликованных уроков за этот период нет.</p>
      )}
      {fold && !!fold.days.length && (
        <>
          <p className="pupil-muted">
            В таблице только дни, по которым учителя опубликовали записи
            журнала.
          </p>
          <div className="pupil-scroll">
            <table className="pupil-table pupil-grid">
              <thead>
                <tr>
                  <th scope="col" className="pupil-sticky">
                    Дата
                  </th>
                  {fold.columns.map((c) => (
                    <th key={c.label} scope="col">
                      {c.label}
                    </th>
                  ))}
                  <th scope="col" title="Всего пропусков за день">
                    Пропуски
                  </th>
                  <th scope="col" title="По уважительной причине">
                    Уваж.
                  </th>
                  <th scope="col" title="Без уважительной причины">
                    Неуваж.
                  </th>
                  <th scope="col">Опоздания</th>
                </tr>
              </thead>
              <tbody>
                {fold.days.map((day, i) => (
                  <Fragment key={day.date}>
                    {(i === 0 ||
                      fold.days[i - 1].date.slice(0, 7) !==
                        day.date.slice(0, 7)) && (
                      <tr className="pupil-month">
                        <th colSpan={fold.columns.length + 5}>
                          {monthTitle(day.date)}
                        </th>
                      </tr>
                    )}
                    <tr>
                      <th scope="row" className="pupil-sticky">
                        {dayTitle(day.date)}
                      </th>
                      {day.cells.map((cell, c) => (
                        <td key={c}>
                          {cell.map((row) => (
                            <Mark
                              key={row.lessonKey}
                              row={row}
                              codes={data.data!.absenceCodes}
                            />
                          ))}
                        </td>
                      ))}
                      <td>{day.absent + day.excused || ""}</td>
                      <td>{day.excused || ""}</td>
                      <td>{day.absent || ""}</td>
                      <td>
                        {day.late
                          ? day.lateShare
                            ? `${day.late} · ${lessonsText(day.lateShare)}`
                            : day.late
                          : ""}
                      </td>
                    </tr>
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="pupil-legend" aria-label="Обозначения">
            {(Object.keys(MARKS) as MarkKind[]).map((kind) => (
              <li key={kind}>
                <span className={`pupil-att pupil-att-${kind}`}>
                  <b>{MARKS[kind].sign}</b>
                </span>
                {MARKS[kind].name}
              </li>
            ))}
          </ul>
        </>
      )}
      {notesOff && compose && (
        <p role="status" className="info-line">
          Классный руководитель не позволяет использовать электронные записки.
          Свяжитесь с классным руководителем
          {pupil?.classTeacher ? `: ${pupil.classTeacher}` : ""}.
        </p>
      )}
      {notesOpen && (
        <div ref={notesRef} id="pupil-absence-notes">
          <AbsencePanel
            key={compose && !notesOff ? "compose" : "notes"}
            workspace={workspace}
            defaultOpen
            studentId={child}
            compose={compose && !notesOff}
          />
        </div>
      )}
    </section>
  );
}

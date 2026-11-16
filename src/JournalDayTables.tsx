import { Fragment, type ReactNode } from "react";
import {
  classLabel,
  groupLabel,
  lessonAttendance,
  rowFill,
  rowState,
  type ClassSummary,
  type ClockNow,
  type FillCount,
  type LessonRow,
} from "./journalDay";
import "./JournalDay.css";

/** «Классный журнал» of an administrator, the first view: the classes of the school for the day, one compact row each. */
export function ClassesTable({
  classes,
  total,
  busy,
  onOpen,
}: {
  classes: ClassSummary[];
  total: FillCount;
  // A lesson is being opened: the class it belongs to is chosen when it arrives, not by a click in the meantime.
  busy: boolean;
  onOpen: (name: string) => void;
}) {
  return (
    <section className="journal-classes" aria-label="Классы">
      <p className="journal-day-total">
        Уроков: <strong>{total.lessons}</strong> · заполнено:{" "}
        <strong>{total.filled}</strong> · не заполнено:{" "}
        <strong>{total.unfilled}</strong>
        {total.upcoming > 0 && (
          <>
            {" "}
            · ещё впереди: <strong>{total.upcoming}</strong>
          </>
        )}
      </p>
      <div className="journal-classes-scroll">
        <table className="journal-classes-table">
          <thead>
            <tr>
              <th>Класс</th>
              <th>Уроков</th>
              <th>Заполнено</th>
              <th>Не заполнено</th>
              <th>Состояние</th>
            </tr>
          </thead>
          <tbody>
            {classes.map((c) => (
              <tr
                key={c.name}
                className={c.unfilled ? "journal-class-missing" : undefined}
                onClick={() => !busy && onOpen(c.name)}
              >
                <td data-label="Класс">
                  <button
                    type="button"
                    className="journal-class-open"
                    disabled={busy}
                    aria-label={`Открыть класс ${c.name}: ${c.state}`}
                  >
                    {c.name}
                  </button>
                </td>
                <td data-label="Уроков">{c.lessons}</td>
                <td data-label="Заполнено">{c.filled}</td>
                <td data-label="Не заполнено">{c.unfilled}</td>
                <td data-label="Состояние" className="journal-class-state">
                  {c.state}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * The lessons of a day as a compact table, one row per lesson in time order. The lesson editor opens in a row of its own
 * under the row of its lesson (again to close it).
 */
export function LessonsTable({
  rows,
  label,
  showClass,
  now,
  selectedKey,
  busy,
  onOpen,
  onPupils,
  onPlan,
  renderEditor,
}: {
  rows: LessonRow[];
  label: string;
  // The class column: not needed where the whole table is one class.
  showClass: boolean;
  // The moment the day is read at: a lesson that has not ended is not yet «не заполнено».
  now: ClockNow;
  // The lesson open in the editor.
  selectedKey?: string;
  busy: boolean;
  onOpen: (key: string) => void;
  // Open the lesson scrolled to the pupil cards, and to the choice of the topic from the course plan.
  onPupils: (key: string) => void;
  onPlan: (key: string) => void;
  renderEditor: (key: string) => ReactNode;
}) {
  // A column of groups only where some lesson has a group other than its whole class.
  const showGroup = rows.some((r) => groupLabel(r.lesson));
  const columns = 8 + (showClass ? 1 : 0) + (showGroup ? 1 : 0);
  return (
    <div className="journal-lessons-box">
      <table className="journal-lessons-table" aria-label={label}>
        <thead>
          <tr>
            <th className="journal-col-extra">№</th>
            <th>Время</th>
            {showClass && <th>Класс</th>}
            <th>Предмет</th>
            {showGroup && <th>Группа</th>}
            <th>Учитель</th>
            <th className="journal-col-extra">Кабинет</th>
            <th className="journal-col-extra">Тема</th>
            <th>Посещаемость</th>
            <th>Состояние</th>
            <th>
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ lesson: l, number }) => {
            const open = l.lessonKey === selectedKey,
              fill = rowFill(l, now),
              topic = l.topic.trim();
            return (
              <Fragment key={l.lessonKey}>
                <tr
                  className={[
                    fill === "cancelled" && "journal-lesson-cancelled",
                    open && "journal-lesson-open",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                >
                  <td data-label="№" className="journal-col-extra">
                    {number || "—"}
                  </td>
                  <td data-label="Время" className="journal-lesson-time">
                    {l.lesson.start}–{l.lesson.end}
                  </td>
                  {showClass && (
                    <td data-label="Класс">
                      <strong>{classLabel(l)}</strong>
                    </td>
                  )}
                  <td className="journal-lesson-subject">{l.lesson.subject}</td>
                  {showGroup && (
                    <td data-label="Группа">{groupLabel(l) || "—"}</td>
                  )}
                  <td data-label="Учитель">{l.lesson.teachers || "—"}</td>
                  <td data-label="Кабинет" className="journal-col-extra">
                    {l.lesson.rooms || "—"}
                  </td>
                  <td data-label="Тема" className="journal-col-extra">
                    <span
                      className={
                        topic
                          ? "journal-lesson-topic"
                          : "journal-lesson-topic journal-lesson-no-topic"
                      }
                      title={topic || undefined}
                    >
                      {topic || "Тема не заполнена"}
                    </span>
                  </td>
                  <td data-label="Посещаемость">
                    <button
                      type="button"
                      className="journal-row-button"
                      disabled={busy}
                      title="Открыть урок на карточках учеников"
                      onClick={() => onPupils(l.lessonKey)}
                    >
                      {lessonAttendance(l)}
                    </button>
                  </td>
                  <td
                    data-label="Состояние"
                    className={`journal-lesson-state journal-lesson-state-${fill}`}
                  >
                    {rowState(l, now)}
                  </td>
                  <td className="journal-lesson-actions">
                    <button
                      type="button"
                      className="journal-row-button"
                      disabled={busy}
                      aria-pressed={open}
                      aria-label={`Открыть урок · ${l.lesson.start}–${l.lesson.end} · ${l.lesson.subject} ${l.lesson.group}`}
                      onClick={() => onOpen(l.lessonKey)}
                    >
                      Открыть
                    </button>
                    {fill !== "cancelled" && fill !== "archived" && (
                      <button
                        type="button"
                        className="journal-row-button"
                        disabled={busy}
                        aria-label={`Выбрать тему из плана · ${l.lesson.start}–${l.lesson.end} · ${l.lesson.subject} ${l.lesson.group}`}
                        onClick={() => onPlan(l.lessonKey)}
                      >
                        Из плана
                      </button>
                    )}
                  </td>
                </tr>
                {open && (
                  <tr className="journal-lessons-editor">
                    <td colSpan={columns}>{renderEditor(l.lessonKey)}</td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

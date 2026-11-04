import { Fragment, useMemo, type ReactNode } from "react";
import { placeInSlots } from "./bells";
import { dayLessonState, type BellSlot, type DayLesson } from "./journal";
import { ruDate } from "./journalCoverage";
import { dayText } from "./pupilJournal";
import "./JournalDay.css";

// «Классный журнал» of a teacher: the bell periods of one day, the own lessons in place with what is already written in them.
// The lesson editor opens right under the row of its lesson.
export default function TeacherDayGrid({
  day,
  lessons,
  slots,
  selectedKey,
  busy,
  onOpen,
  onPupils,
  onPlan,
  renderEditor,
}: {
  day: string;
  lessons: DayLesson[];
  slots: BellSlot[];
  // The lesson open in the editor.
  selectedKey?: string;
  busy: boolean;
  onOpen: (key: string) => void;
  // Open the lesson scrolled to the pupil cards, and to the choice of the topic from the course plan.
  onPupils: (key: string) => void;
  onPlan: (key: string) => void;
  renderEditor: (key: string) => ReactNode;
}) {
  const rows = useMemo(
    () =>
      placeInSlots(
        slots,
        lessons.map((l) => ({
          start: l.lesson.start,
          end: l.lesson.end,
          subject: l.lesson.subject,
          day: l,
        })),
      ),
    [slots, lessons],
  );
  return (
    <section className="teacher-day" aria-label={`Уроки ${dayText(day)}`}>
      {rows.map((row) => (
        <Fragment key={`${row.slot}|${row.start}`}>
          <div className="teacher-day-row">
            <div className="teacher-day-slot">
              <strong>{row.label}</strong>
              <span>
                {row.start}–{row.end}
              </span>
            </div>
            <div className="teacher-day-body">
              {!row.items.length && (
                <p className="teacher-day-nothing" aria-label="Урока нет">
                  —
                </p>
              )}
              {row.items.map(({ day: l }) => (
                <article
                  key={l.lessonKey}
                  className={[
                    "teacher-lesson",
                    l.lesson.status === "cancelled" &&
                      "teacher-lesson-cancelled",
                    l.lessonKey === selectedKey && "teacher-lesson-open",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                >
                  <header>
                    <h3>{l.lesson.subject}</h3>
                    <p className="pupil-muted">
                      {[
                        l.classes.join(", ") || l.lesson.group,
                        // A group named like its class says nothing more.
                        l.classes.length &&
                        l.lesson.group !== l.classes.join(", ")
                          ? l.lesson.group
                          : "",
                        l.lesson.rooms,
                        l.lesson.teachers,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </header>
                  <p
                    className={
                      l.topic.trim()
                        ? "teacher-topic"
                        : "teacher-topic pupil-muted"
                    }
                  >
                    {l.topic.trim() || "Тема не заполнена"}
                  </p>
                  {l.homework.trim() && (
                    <p className="teacher-homework">
                      Домашнее задание: {l.homework.trim()}
                      {l.dueDate && ` · срок ${ruDate(l.dueDate)}`}
                    </p>
                  )}
                  <div className="teacher-lesson-line">
                    <button
                      type="button"
                      className="secondary teacher-count"
                      disabled={busy}
                      title="Открыть урок на карточках учеников"
                      onClick={() => onPupils(l.lessonKey)}
                    >
                      присутствуют {l.present === null ? "—" : l.present}/
                      {l.studentCount}
                    </button>
                    <span className="pupil-muted">{dayLessonState(l)}</span>
                  </div>
                  <div className="teacher-lesson-actions">
                    <button
                      type="button"
                      disabled={busy}
                      aria-pressed={l.lessonKey === selectedKey}
                      aria-label={`Открыть урок · ${l.lesson.start}–${l.lesson.end} · ${l.lesson.subject} ${l.lesson.group}`}
                      onClick={() => onOpen(l.lessonKey)}
                    >
                      Открыть урок
                    </button>
                    {l.lesson.status !== "cancelled" && !l.archived && (
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy}
                        onClick={() => onPlan(l.lessonKey)}
                      >
                        Выбрать из плана
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </div>
          {selectedKey &&
            row.items.some(({ day: l }) => l.lessonKey === selectedKey) &&
            renderEditor(selectedKey)}
        </Fragment>
      ))}
    </section>
  );
}

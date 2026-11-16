import { Fragment, useState } from "react";
import CategoryBadge from "./CategoryBadge";
import { categoryOf, type CategoryConfig } from "./assessmentCategories";
import {
  LessonMarkChips,
  LessonMarkChoices,
  LessonMarkError,
  type LessonMarks,
} from "./LessonMarks";
import {
  LATE_SHARE_FROM,
  attendanceNames,
  changeAttendance,
  lateShare,
  lessonLength,
  type Assessment,
  type Grade,
  type JournalEntry,
  type PupilRecord,
} from "./journal";
import {
  ATTENDANCE_CHOICES,
  attendanceButtonName,
  needsDetails,
  pupilRows,
  showClasses,
} from "./lessonEditorModel";
import "./LessonPupilTable.css";

type Panel = "marks" | "comments";

// One row per pupil of the lesson: the mark with its fields, the grade of every work, the praise and remarks, comments.
// The table does not rely on a fieldset: every field turns itself off when the lesson is read-only or has not begun,
// and the comment toggle stays alive so a read-only user can still read what was written.
export default function LessonPupilTable({
  entry,
  categories,
  lessonMarks,
  onPupil,
  onGrade,
  readOnly,
}: {
  entry: JournalEntry;
  categories?: CategoryConfig | null;
  lessonMarks: LessonMarks;
  onPupil: (id: string, update: (p: PupilRecord) => PupilRecord) => void;
  onGrade: (id: string, a: Assessment, update: Partial<Grade>) => void;
  readOnly: boolean;
}) {
  // The row under a pupil that is open: one at a time per pupil, the marks or the comments.
  const [open, setOpen] = useState<Record<string, Panel>>({});
  const { lesson, content } = entry.document;
  if (!lesson.students.length)
    return (
      <p className="empty-state">
        В составе нет учеников. Добавьте их в справочник и группу.
      </p>
    );
  // Before the lesson only the plan is edited: marks and grades wait for the day, comments may be prepared.
  const early = !!entry.futureLesson;
  const locked = readOnly || early;
  const rows = pupilRows(entry);
  const classes = showClasses(lesson.students);
  const details = needsDetails(rows);
  const marksColumn = !!lessonMarks.info;
  const works = content.assessments;
  const span = 3 + (details ? 1 : 0) + works.length + (marksColumn ? 1 : 0) + 1;
  const toggle = (id: string, panel: Panel) =>
    setOpen((o) => {
      const next = { ...o };
      if (next[id] === panel) delete next[id];
      else next[id] = panel;
      return next;
    });
  return (
    <div className="lesson-pupil-table">
      <div className="lesson-pupil-scroll">
        <table>
          <thead>
            <tr>
              <th className="lesson-pupil-num" scope="col">
                №
              </th>
              <th className="lesson-pupil-name" scope="col">
                Ученик
              </th>
              <th scope="col">Посещаемость</th>
              {details && <th scope="col">Подробно</th>}
              {works.map((a) => (
                <th
                  key={a.id}
                  scope="col"
                  className="lesson-pupil-work"
                  title={a.name}
                >
                  <span className="lesson-pupil-work-head">
                    <CategoryBadge category={categoryOf(a, categories)} />
                    <span className="lesson-pupil-work-name">{a.name}</span>
                    {a.kind === "points" && Number.isFinite(a.maxScore) && (
                      <small>/ {a.maxScore}</small>
                    )}
                  </span>
                </th>
              ))}
              {marksColumn && <th scope="col">Отметки</th>}
              <th scope="col">Комментарий</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const { student: s, record } = row;
              const panel = open[s.id];
              const moreId = `lesson-pupil-more-${s.id}`;
              const canMark = !!lessonMarks.info?.canMark && s.enrolled;
              const share = lateShare(
                record.lateMinutes,
                lesson.start,
                lesson.end,
              );
              return (
                <Fragment key={s.id}>
                  <tr
                    className="lesson-pupil-row"
                    data-status={record.attendance}
                  >
                    <td className="lesson-pupil-num">{row.number}</td>
                    <td className="lesson-pupil-name">
                      <span className="lesson-pupil-fullname">{s.name}</span>
                      {(classes || !s.enrolled) && (
                        <small>
                          {classes ? s.className : ""}
                          {classes && !s.enrolled ? " · " : ""}
                          {!s.enrolled ? "прежний состав" : ""}
                        </small>
                      )}
                    </td>
                    <td className="lesson-pupil-attendance">
                      <div
                        className="lesson-att"
                        role="group"
                        aria-label={`Посещаемость: ${s.name}`}
                      >
                        {ATTENDANCE_CHOICES.map(({ status, label }) => {
                          const pressed = record.attendance === status;
                          return (
                            <button
                              type="button"
                              key={status}
                              data-status={status}
                              aria-pressed={pressed}
                              aria-label={attendanceButtonName(label, status)}
                              title={attendanceNames[status]}
                              disabled={locked}
                              onClick={() =>
                                onPupil(s.id, (p) =>
                                  changeAttendance(p, status),
                                )
                              }
                            >
                              {pressed ? "✓ " : ""}
                              {label}
                            </button>
                          );
                        })}
                      </div>
                    </td>
                    {details && (
                      <td
                        className="lesson-pupil-details"
                        data-label="Подробно"
                      >
                        {row.legacy && (
                          <span
                            className="lesson-pupil-chip"
                            title={
                              row.legacy === "excused"
                                ? "Прежняя отметка: выбор кнопки заменит её."
                                : undefined
                            }
                          >
                            {attendanceNames[row.legacy]}
                            {row.legacy === "excused" ? " · прежняя" : ""}
                          </span>
                        )}
                        {row.approved && (
                          <span
                            className="lesson-pupil-chip lesson-pupil-approved"
                            title="На этот урок принята объяснительная. Отметка «Отсутствует» учитывается как уважительная причина; исходная отметка сохраняется."
                          >
                            Объяснительная
                          </span>
                        )}
                        {row.extra === "absent" && (
                          <select
                            aria-label={`Тип отсутствия: ${s.name}`}
                            title="Уважительной отметка становится по объяснительной, принятой школой; тип — только пометка."
                            disabled={locked}
                            value={record.absenceCode ?? ""}
                            onChange={(e) =>
                              onPupil(s.id, (p) => ({
                                ...p,
                                absenceCode: e.target.value,
                              }))
                            }
                          >
                            <option value="">Без типа</option>
                            {row.codes.map((c) => (
                              <option key={c.id} value={c.id}>
                                {c.name}
                                {c.active ? "" : " (отключён)"}
                              </option>
                            ))}
                          </select>
                        )}
                        {row.extra === "late" && (
                          <>
                            <input
                              type="number"
                              min="0"
                              max={lessonLength(lesson)}
                              aria-label={`Минуты опоздания: ${s.name}`}
                              disabled={locked}
                              value={record.lateMinutes}
                              onChange={(e) =>
                                onPupil(s.id, (p) => ({
                                  ...p,
                                  lateMinutes: Number(e.target.value),
                                }))
                              }
                            />
                            <small
                              title={
                                share > 0
                                  ? undefined
                                  : `Меньше ${LATE_SHARE_FROM} мин: в долях урока не считается.`
                              }
                            >
                              {share > 0
                                ? `мин · ${Math.round(share * 100)}% урока`
                                : `мин · не в долях`}
                            </small>
                          </>
                        )}
                        {row.extra === "left" && (
                          <>
                            <small>Ушёл в</small>
                            <input
                              type="time"
                              min={lesson.start}
                              max={lesson.end}
                              aria-label={`Ушёл в: ${s.name}`}
                              title={`Время внутри урока, ${lesson.start}–${lesson.end}.`}
                              disabled={locked}
                              value={record.leftAt ?? ""}
                              onChange={(e) =>
                                onPupil(s.id, (p) => ({
                                  ...p,
                                  leftAt: e.target.value || null,
                                }))
                              }
                            />
                          </>
                        )}
                      </td>
                    )}
                    {works.map((a) => {
                      const g = record.grades.find(
                        (g) => g.assessmentId === a.id,
                      );
                      return (
                        <td
                          key={a.id}
                          className="lesson-pupil-grade"
                          data-label={
                            a.kind === "points" && Number.isFinite(a.maxScore)
                              ? `${a.name} (из ${a.maxScore})`
                              : a.name
                          }
                        >
                          {a.kind === "points" ? (
                            <input
                              type="number"
                              min="0"
                              max={
                                Number.isFinite(a.maxScore)
                                  ? a.maxScore
                                  : undefined
                              }
                              step="0.01"
                              aria-label={`${a.name}: ${s.name}`}
                              disabled={locked}
                              value={g?.score ?? ""}
                              onChange={(e) =>
                                onGrade(s.id, a, {
                                  score:
                                    e.target.value === ""
                                      ? null
                                      : Number(e.target.value),
                                })
                              }
                            />
                          ) : (
                            <input
                              maxLength={30}
                              aria-label={`${a.name}: ${s.name}`}
                              disabled={locked}
                              value={g?.label || ""}
                              onChange={(e) =>
                                onGrade(s.id, a, { label: e.target.value })
                              }
                            />
                          )}
                        </td>
                      );
                    })}
                    {marksColumn && (
                      <td className="lesson-pupil-marks" data-label="Отметки">
                        {s.enrolled && (
                          <>
                            <LessonMarkChips
                              marks={lessonMarks}
                              studentId={s.id}
                            />
                            {canMark && (
                              <button
                                type="button"
                                className="lesson-pupil-toggle"
                                aria-expanded={panel === "marks"}
                                aria-controls={
                                  panel === "marks" ? moreId : undefined
                                }
                                disabled={readOnly}
                                onClick={() => toggle(s.id, "marks")}
                              >
                                ＋ Отметка
                              </button>
                            )}
                            {panel !== "marks" && (
                              <LessonMarkError
                                marks={lessonMarks}
                                studentId={s.id}
                              />
                            )}
                          </>
                        )}
                      </td>
                    )}
                    <td className="lesson-pupil-comment">
                      <button
                        type="button"
                        className="lesson-pupil-toggle"
                        data-filled={row.comments > 0}
                        aria-expanded={panel === "comments"}
                        aria-controls={
                          panel === "comments" ? moreId : undefined
                        }
                        onClick={() => toggle(s.id, "comments")}
                      >
                        Комментарий{row.comments ? ` · ${row.comments}` : ""}
                      </button>
                    </td>
                  </tr>
                  {panel && (
                    <tr className="lesson-pupil-more" id={moreId}>
                      <td colSpan={span}>
                        {panel === "marks" ? (
                          <div className="lesson-pupil-marks-row">
                            <LessonMarkChoices
                              marks={lessonMarks}
                              studentId={s.id}
                              disabled={readOnly || !!lessonMarks.busy}
                            />
                          </div>
                        ) : (
                          <div className="lesson-pupil-comments-row">
                            <label className="field">
                              <span>Комментарий ученику</span>
                              <input
                                maxLength={1000}
                                disabled={readOnly}
                                value={record.comment}
                                onChange={(e) =>
                                  onPupil(s.id, (p) => ({
                                    ...p,
                                    comment: e.target.value,
                                  }))
                                }
                              />
                            </label>
                            {works.map((a) => (
                              <label className="field" key={a.id}>
                                <span>Комментарий к оценке: {a.name}</span>
                                <input
                                  maxLength={1000}
                                  disabled={readOnly}
                                  value={
                                    record.grades.find(
                                      (g) => g.assessmentId === a.id,
                                    )?.comment || ""
                                  }
                                  onChange={(e) =>
                                    onGrade(s.id, a, {
                                      comment: e.target.value,
                                    })
                                  }
                                />
                              </label>
                            ))}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import CategoryBadge from "./CategoryBadge";
import { categoryOf, type CategoryConfig } from "./assessmentCategories";
import { LessonMarkBar, type LessonMarks } from "./LessonMarks";
import {
  LATE_SHARE_FROM,
  attendanceNames,
  changeAttendance,
  initials,
  lateShare,
  lessonLength,
  type Assessment,
  type Grade,
  type JournalEntry,
  type PupilRecord,
} from "./journal";
import "./LessonPupilCards.css";

// Choosable marks. «Не отмечено» and the old «Уважительная причина» only show as a chip while a record holds them:
// an excused absence comes from a note accepted by the school, never from a teacher's choice.
const STATUSES = ["present", "absent", "late", "left"] as const;

// One card per pupil of the lesson: the mark, its details, a quick praise or remark, the grade of every work and comments.
// It renders inside the editor's fieldset, which disables everything when the lesson is read-only.
export default function LessonPupilCards({
  entry,
  categories,
  lessonMarks,
  onPupil,
  onGrade,
}: {
  entry: JournalEntry;
  categories?: CategoryConfig | null;
  lessonMarks: LessonMarks;
  onPupil: (id: string, update: (p: PupilRecord) => PupilRecord) => void;
  onGrade: (id: string, a: Assessment, update: Partial<Grade>) => void;
}) {
  const { lesson, content } = entry.document;
  // Before the lesson only the plan is edited: marks and grades wait for the day, comments may be prepared.
  const early = !!entry.futureLesson;
  if (!lesson.students.length)
    return (
      <p className="empty-state">
        В составе нет учеников. Добавьте их в справочник и группу.
      </p>
    );
  return (
    <div className="journal-pupils lesson-pupils">
      {lesson.students.map((s) => {
        const record = content.records.find((p) => p.studentId === s.id)!;
        const share = lateShare(record.lateMinutes, lesson.start, lesson.end);
        const codes = [...(entry.absenceCodes ?? [])];
        // A retired type stays selectable on the marks that already hold it, so a correction does not drop it.
        if (
          record.absenceCode &&
          !codes.some((c) => c.id === record.absenceCode)
        )
          codes.push({
            id: record.absenceCode,
            name: record.absenceCode,
            active: false,
          });
        const comments = [
          record.comment,
          ...record.grades.map((g) => g.comment),
        ].filter(Boolean).length;
        return (
          <article className="journal-pupil lesson-pupil" key={s.id}>
            <div className="lesson-pupil-head">
              <span className="lesson-pupil-avatar" aria-hidden="true">
                {initials(s.name)}
              </span>
              <h4>
                {s.name}{" "}
                <small>
                  {s.className}
                  {!s.enrolled ? " · прежний состав" : ""}
                </small>
              </h4>
            </div>
            {entry.attendanceApprovals?.[s.id] && (
              <p className="info-line">
                На этот урок принята объяснительная. Отметка «Отсутствует»
                учитывается как уважительная причина; исходная отметка
                сохраняется.
              </p>
            )}
            {s.enrolled && (
              <LessonMarkBar marks={lessonMarks} studentId={s.id} />
            )}
            <div
              className="lesson-pupil-status"
              role="group"
              aria-label={`Посещаемость: ${s.name}`}
            >
              {STATUSES.map((status) => (
                <button
                  type="button"
                  key={status}
                  aria-pressed={record.attendance === status}
                  disabled={early}
                  onClick={() =>
                    onPupil(s.id, (p) => changeAttendance(p, status))
                  }
                >
                  {attendanceNames[status]}
                </button>
              ))}
            </div>
            {(record.attendance === "unmarked" ||
              record.attendance === "excused") && (
              <p className="lesson-pupil-chip">
                {attendanceNames[record.attendance]}
                {record.attendance === "excused"
                  ? " · прежняя отметка, выбор кнопки заменит её"
                  : ""}
              </p>
            )}
            {record.attendance === "absent" && (
              <div className="lesson-pupil-extra">
                <label className="field">
                  <span>Тип отсутствия</span>
                  <select
                    disabled={early}
                    value={record.absenceCode ?? ""}
                    onChange={(e) =>
                      onPupil(s.id, (p) => ({
                        ...p,
                        absenceCode: e.target.value,
                      }))
                    }
                  >
                    <option value="">Без типа</option>
                    {codes
                      .filter((c) => c.active || c.id === record.absenceCode)
                      .map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                          {c.active ? "" : " (отключён)"}
                        </option>
                      ))}
                  </select>
                </label>
                <small>
                  Уважительной отметка становится по объяснительной, принятой
                  школой; тип — только пометка.
                </small>
              </div>
            )}
            {record.attendance === "late" && (
              <div className="lesson-pupil-extra">
                <label className="field">
                  <span>Минуты опоздания</span>
                  <input
                    type="number"
                    min="0"
                    max={lessonLength(lesson)}
                    disabled={early}
                    value={record.lateMinutes}
                    onChange={(e) =>
                      onPupil(s.id, (p) => ({
                        ...p,
                        lateMinutes: Number(e.target.value),
                      }))
                    }
                  />
                </label>
                <small>
                  {share > 0
                    ? `${Math.round(share * 100)}% урока`
                    : `Меньше ${LATE_SHARE_FROM} мин: в долях урока не считается.`}
                </small>
              </div>
            )}
            {record.attendance === "left" && (
              <div className="lesson-pupil-extra">
                <label className="field">
                  <span>Ушёл в</span>
                  <input
                    type="time"
                    min={lesson.start}
                    max={lesson.end}
                    disabled={early}
                    value={record.leftAt ?? ""}
                    onChange={(e) =>
                      onPupil(s.id, (p) => ({
                        ...p,
                        leftAt: e.target.value || null,
                      }))
                    }
                  />
                </label>
                <small>
                  Время внутри урока, {lesson.start}–{lesson.end}.
                </small>
              </div>
            )}
            {!!content.assessments.length && (
              <div className="lesson-pupil-grades">
                {content.assessments.map((a) => {
                  const g = record.grades.find((g) => g.assessmentId === a.id);
                  return (
                    <label className="field" key={a.id}>
                      <span>
                        <CategoryBadge category={categoryOf(a, categories)} />
                        {a.name}
                        {a.kind === "points" && Number.isFinite(a.maxScore)
                          ? ` (из ${a.maxScore})`
                          : ""}
                      </span>
                      {a.kind === "points" ? (
                        <input
                          type="number"
                          min="0"
                          max={
                            Number.isFinite(a.maxScore) ? a.maxScore : undefined
                          }
                          step="0.01"
                          disabled={early}
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
                          disabled={early}
                          value={g?.label || ""}
                          onChange={(e) =>
                            onGrade(s.id, a, { label: e.target.value })
                          }
                        />
                      )}
                    </label>
                  );
                })}
              </div>
            )}
            <details>
              <summary>Комментарии{comments ? ` · ${comments}` : ""}</summary>
              <label className="field">
                <span>Комментарий ученику</span>
                <input
                  maxLength={1000}
                  value={record.comment}
                  onChange={(e) =>
                    onPupil(s.id, (p) => ({ ...p, comment: e.target.value }))
                  }
                />
              </label>
              {content.assessments.map((a) => (
                <label className="field" key={a.id}>
                  <span>Комментарий к оценке: {a.name}</span>
                  <input
                    maxLength={1000}
                    value={
                      record.grades.find((g) => g.assessmentId === a.id)
                        ?.comment || ""
                    }
                    onChange={(e) =>
                      onGrade(s.id, a, { comment: e.target.value })
                    }
                  />
                </label>
              ))}
            </details>
          </article>
        );
      })}
    </div>
  );
}

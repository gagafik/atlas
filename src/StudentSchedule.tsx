import { AlertTriangle, ArrowUpRight, Clock3, DoorOpen } from "lucide-react";
import { teacherNames, roomNames } from "./utils";
import type { Group, Placement, Report, State } from "./types";
import {
  chronologicalPlacements,
  issuesForStudent,
  lessonTime,
  named,
  rosterComplete,
} from "./utils";

export default function StudentSchedule({
  state,
  studentId,
  placements,
  report,
  onLesson,
  onCheck,
  onGroup,
}: {
  state: State;
  studentId: string;
  placements: Placement[];
  report: Report;
  onLesson: (p: Placement) => void;
  onCheck: () => void;
  onGroup: (g: Group) => void;
}) {
  const student = state.students.find((st) => st.id === studentId);
  if (!student)
    return (
      <div className="empty">
        <h3>Выберите ученика</h3>
        <p>
          Измените поиск или фильтр класса. Если список пуст, внесите учеников в
          разделе «Классы и группы».
        </p>
      </div>
    );
  const groups = state.groups.filter(
    (g) => g.kind !== "whole" && g.studentIds.includes(studentId),
  );
  const incomplete = state.groups.filter(
    (g) =>
      g.classIds.includes(student.classId) &&
      !rosterComplete(g) &&
      state.workloads.some((w) => w.groupId === g.id),
  );
  const issues = issuesForStudent(report.issues, placements, studentId);
  const ordered = chronologicalPlacements(state, placements);
  const pendingHours = state.workloads
    .filter((w) => {
      const g = state.groups.find((g) => g.id === w.groupId)!;
      return g.kind === "whole"
        ? g.classIds.includes(student.classId)
        : g.studentIds.includes(studentId);
    })
    .reduce(
      (n, w) =>
        n +
        Math.max(
          0,
          w.hours -
            state.placements.filter((p) => p.workloadId === w.id).length,
        ),
      0,
    );
  return (
    <div className="student-schedule">
      <div className="student-heading">
        <div>
          <span className="eyebrow">
            Личное расписание · {named(state.classes, student.classId)}
          </span>
          <h2>{student.name}</h2>
          <p>
            Занятий в неделю: {placements.length} · Смешанных групп:{" "}
            {groups.length}
          </p>
        </div>
        <button
          className={
            issues.length ? "student-check has-issues" : "student-check"
          }
          onClick={onCheck}
        >
          {issues.length ? (
            <AlertTriangle size={17} />
          ) : (
            <ArrowUpRight size={17} />
          )}
          {issues.length
            ? `Нарушения: ${issues.length}`
            : "Проверить расписание"}
        </button>
      </div>
      {student.active === false && (
        <p className="student-notice">
          Ученик отмечен как выбывший. Показаны общие уроки его класса и
          сохранённые назначения в группы.
        </p>
      )}
      {!!incomplete.length && (
        <p className="student-notice">
          Составы групп неполные: {incomplete.map((g) => g.name).join(", ")}. В
          личной неделе показаны только подтверждённые назначения и общие уроки
          класса.
        </p>
      )}
      {!!pendingHours && (
        <p className="student-notice">
          Ещё не размещено занятий: {pendingHours}.
        </p>
      )}
      <div className="student-week">
        {state.settings.days.map((day, index) => {
          const lessons = ordered.filter((p) => p.day === index);
          return (
            <section className="student-day" key={day}>
              <header>
                <h3>{day}</h3>
                <span>{lessons.length}</span>
              </header>
              {lessons.length ? (
                lessons.map((p) => {
                  const w = state.workloads.find((w) => w.id === p.workloadId)!;
                  const group = state.groups.find((g) => g.id === w.groupId)!;
                  const subject = state.subjects.find(
                    (sub) => sub.id === w.subjectId,
                  )!;
                  const slot = lessonTime(state, p);
                  const flagged = issues.some(
                    (i) => i.ids.includes(p.id) || i.ids.includes(w.id),
                  );
                  return (
                    <button
                      key={p.id}
                      className={`student-lesson ${flagged ? "has-issues" : ""}`}
                      style={{ borderLeftColor: subject.color }}
                      onClick={() => onLesson(p)}
                      aria-label={`Открыть урок: ${subject.name}, ${day}, ${slot.start}, ${group.name}`}
                    >
                      <span className="student-lesson-time">
                        <Clock3 size={12} /> {slot.start}–{slot.end}
                        <span>#{slot.label}</span>
                      </span>
                      <strong>{subject.name}</strong>
                      <span>
                        {group.kind === "whole"
                          ? `Весь класс · ${group.name}`
                          : group.name}
                      </span>
                      <small>{teacherNames(state, w)}</small>
                      <small>
                        <DoorOpen size={12} /> {roomNames(state, w, p)}
                      </small>
                      {flagged && (
                        <span className="lesson-warning">
                          <AlertTriangle size={12} /> Есть нарушение
                        </span>
                      )}
                    </button>
                  );
                })
              ) : (
                <p className="student-day-empty">Нет занятий</p>
              )}
            </section>
          );
        })}
      </div>
      {!!groups.length && (
        <div className="student-group-list">
          <strong>Группы ученика</strong>
          <div>
            {groups.map((g) => (
              <button key={g.id} onClick={() => onGroup(g)}>
                {g.name}
                <ArrowUpRight size={13} />
              </button>
            ))}
          </div>
        </div>
      )}
      <p className="student-footnote">
        Время указано по звонкам каждого занятия. Изменение урока затрагивает
        всю его группу.
      </p>
    </div>
  );
}

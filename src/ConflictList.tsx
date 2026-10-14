import { AlertTriangle, ArrowUpRight } from "lucide-react";
import { teacherNames, roomNames } from "./utils";
import type { Group, Placement, ScheduleIssue, State } from "./types";
import { chronologicalPlacements, lessonTime, named } from "./utils";

export default function ConflictList({
  state,
  issues,
  onLesson,
  onGroup,
  onStudent,
}: {
  state: State;
  issues: ScheduleIssue[];
  onLesson: (p: Placement) => void;
  onGroup: (g: Group) => void;
  onStudent: (id: string) => void;
}) {
  return (
    <div className="conflict-list">
      {issues.map((issue, index) => {
        const collision = issue.collision;
        const lessons = chronologicalPlacements(
          state,
          state.placements.filter(
            (p) => issue.ids.includes(p.id) || issue.ids.includes(p.workloadId),
          ),
        );
        const students = state.students
          .filter(
            (st) =>
              collision?.studentIds.includes(st.id) ||
              issue.affectedStudentIds?.includes(st.id),
          )
          .sort(
            (a, b) =>
              named(state.classes, a.classId).localeCompare(
                named(state.classes, b.classId),
                "ru",
                { numeric: true },
              ) || a.name.localeCompare(b.name, "ru"),
          );
        return (
          <article
            className="conflict-card"
            key={`${issue.type}-${issue.ids.join("-")}-${index}`}
          >
            <header>
              <AlertTriangle size={18} />
              <div>
                <strong>
                  {collision
                    ? `${state.settings.days[collision.day]} · ${collision.start}–${collision.end}`
                    : issue.penalty !== undefined
                      ? "Пожелание"
                      : "Нарушение условий"}
                </strong>
                <p>{issue.message}</p>
              </div>
            </header>
            {collision && (
              <div className="conflict-resources">
                {!!collision.teacherIds.length && (
                  <span>
                    Общий учитель:{" "}
                    {collision.teacherIds
                      .map((id) => named(state.teachers, id))
                      .join(", ")}
                  </span>
                )}
                {!!collision.roomIds.length && (
                  <span>
                    Общий кабинет:{" "}
                    {collision.roomIds
                      .map((id) => named(state.rooms, id))
                      .join(", ")}
                  </span>
                )}
                {!!collision.classIds.length && (
                  <span>
                    Общие классы:{" "}
                    {collision.classIds
                      .map((id) => named(state.classes, id))
                      .join(", ")}
                  </span>
                )}
              </div>
            )}
            <div className="conflict-lessons">
              {lessons.map((p) => {
                const w = state.workloads.find((w) => w.id === p.workloadId)!;
                const g = state.groups.find((g) => g.id === w.groupId)!;
                const slot = lessonTime(state, p);
                return (
                  <div className="conflict-lesson" key={p.id}>
                    <strong>{named(state.subjects, w.subjectId)}</strong>
                    <button
                      className="text-button"
                      onClick={() => onGroup(g)}
                      aria-label={`Открыть группу: ${g.name}`}
                    >
                      {g.name}
                      <ArrowUpRight size={13} />
                    </button>
                    <span>
                      {state.settings.days[p.day]} · урок {slot.label} ·{" "}
                      {slot.start}–{slot.end}
                    </span>
                    <span>
                      {teacherNames(state, w)} · кабинет{" "}
                      {roomNames(state, w, p)}
                    </span>
                    <button
                      onClick={() => onLesson(p)}
                      aria-label={`Открыть конфликтующий урок: ${named(state.subjects, w.subjectId)}, ${g.name}`}
                    >
                      Открыть урок <ArrowUpRight size={14} />
                    </button>
                  </div>
                );
              })}
            </div>
            {!!students.length && (
              <details
                className="conflict-students"
                open={students.length <= 6}
              >
                <summary>
                  {issue.ruleId
                    ? "Ученики, к которым относится правило"
                    : "Общие ученики по внесённым спискам"}
                  : {students.length}
                </summary>
                <div>
                  {students.map((st) => (
                    <button key={st.id} onClick={() => onStudent(st.id)}>
                      {st.name} · {named(state.classes, st.classId)}
                      {st.active === false ? " · выбыл" : ""}
                      <ArrowUpRight size={13} />
                    </button>
                  ))}
                </div>
              </details>
            )}
            {collision && !!collision.classIds.length && !students.length && (
              <p className="muted small">
                Пересечение подтверждено по классу. Поимённый список учеников
                для него не внесён.
              </p>
            )}
          </article>
        );
      })}
    </div>
  );
}

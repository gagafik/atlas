import HomeworkPanel from "./HomeworkPanel";
import LessonMaterials from "./LessonMaterials";
import { SavedPlanTopic } from "./LessonTopic";
import CategoryBadge from "./CategoryBadge";
import { categoryOf, type CategoryConfig } from "./assessmentCategories";
import { attendanceText, gradeLabel, type JournalRow } from "./journal";

// Published lessons of one pupil: the topic, homework, the pupil's marks and attendance of each.
export default function PublishedRows({
  rows,
  workspace,
  categories,
  codeNames,
  bare = false,
}: {
  rows: JournalRow[];
  workspace?: string;
  categories?: CategoryConfig | null;
  codeNames?: Record<string, string>;
  // A row that sits under its own lesson heading: no date, time, subject and group line of its own.
  bare?: boolean;
}) {
  return (
    <div className="journal-published-list">
      {rows.map((row) => (
        <article key={row.lessonKey + row.record.studentId}>
          {!bare && (
            <>
              <h3>
                {row.lesson.date} · {row.lesson.start}–{row.lesson.end} ·{" "}
                {row.lesson.subject}
              </h3>
              <p>
                {row.student.name} · {row.lesson.group} · {row.lesson.teachers}
              </p>
            </>
          )}
          <p>
            <strong>{attendanceText(row.record, row.lesson, codeNames)}</strong>
            {row.record.comment && ` · ${row.record.comment}`}
            {row.absenceApproval && " · объяснительная принята школой"}
          </p>
          {row.content.topic && (
            <p className="journal-text">
              <strong>Тема: </strong>
              {row.content.topic}
            </p>
          )}
          {row.content.homework && (
            <p className="journal-text">
              <strong>
                Домашнее задание
                {row.content.dueDate ? ` к ${row.content.dueDate}` : ""}:{" "}
              </strong>
              {row.content.homework}
            </p>
          )}
          {row.content.assessments.map((a) => {
            const g = row.record.grades.find((g) => g.assessmentId === a.id);
            return (
              <p key={a.id} className="journal-text">
                <CategoryBadge category={categoryOf(a, categories)} />
                <strong>
                  {a.name}: {gradeLabel(g, a)}
                </strong>
                {a.kind === "points" && ` · вес ${a.weight}x`}
                {g?.comment && ` · ${g.comment}`}
              </p>
            );
          })}
          <SavedPlanTopic value={row.content.curriculum} />
          {workspace && (
            <LessonMaterials
              items={row.content.materials || []}
              workspace={workspace}
              lessonKey={row.lessonKey}
            />
          )}
          {workspace && row.content.homework && (
            <HomeworkPanel
              key={workspace + row.lessonKey + row.student.id}
              workspace={workspace}
              lessonKey={row.lessonKey}
              studentId={row.student.id}
            />
          )}
        </article>
      ))}
    </div>
  );
}

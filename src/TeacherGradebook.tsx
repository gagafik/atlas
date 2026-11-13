import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import CourseTablePanel from "./CourseTablePanel";
import type { CourseItem, CourseKind, CourseList } from "./courseTable";
import "./CourseTable.css";

const KIND: Record<CourseKind, string> = {
  course: "Мой курс",
  class: "Классный руководитель — только чтение",
  admin: "Администратор",
};
// A school has hundreds of courses; the page shows a screenful and the search narrows it.
const SHOWN = 120;
const keyOf = (c: Pick<CourseItem, "groupId" | "subjectId">) =>
  `${c.groupId}|${c.subjectId}`;

// «Оценки» of a teacher: the courses (a class or group and a subject) as cards; a card opens the grid of the course at full width.
export default function TeacherGradebook({
  workspace,
  onOpen,
}: {
  workspace: string;
  onOpen: (lessonKey: string, day: string) => void;
}) {
  const [list, setList] = useState<CourseList | null>(null),
    [error, setError] = useState(""),
    [filter, setFilter] = useState(""),
    [className, setClassName] = useState(""),
    [open, setOpen] = useState<string | null>(null);
  const first = useRef(true);
  useEffect(() => {
    let live = true;
    setList(null);
    setError("");
    first.current = true;
    api("journal/course-table/courses", workspace)
      .then((value: CourseList) => {
        if (!live) return;
        setList(value);
        // A single course needs no choosing.
        if (first.current && value.courses.length === 1)
          setOpen(keyOf(value.courses[0]));
        first.current = false;
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [workspace]);
  if (open)
    return (
      <CourseTablePanel
        key={`${workspace}|${open}`}
        workspace={workspace}
        course={open}
        embedded
        onOpen={onOpen}
        onBack={() => setOpen(null)}
      />
    );
  const classes = [
    ...new Set((list?.courses ?? []).flatMap((c) => c.classes)),
  ].sort((a, b) => a.localeCompare(b, "ru", { numeric: true }));
  const found = (list?.courses ?? []).filter(
    (c) =>
      (!className || c.classes.includes(className)) &&
      `${c.group} ${c.subject} ${c.classes.join(" ")} ${c.teachers.join(" ")}`
        .toLocaleLowerCase()
        .includes(filter.trim().toLocaleLowerCase()),
  );
  return (
    <section className="gradebook" aria-label="Оценки">
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {!list && !error && <p role="status">Загружаю курсы…</p>}
      {list && !list.courses.length && (
        <p className="empty-state">
          Курсов, для которых доступна таблица, нет.
        </p>
      )}
      {!!list?.courses.length && (
        <div className="dated-toolbar ct-toolbar">
          <label className="field">
            <span>Найти курс</span>
            <input
              type="search"
              value={filter}
              placeholder="Группа, предмет, учитель"
              onChange={(e) => setFilter(e.target.value)}
            />
          </label>
          {classes.length > 1 && (
            <label className="field">
              <span>Класс</span>
              <select
                value={className}
                onChange={(e) => setClassName(e.target.value)}
              >
                <option value="">Все классы</option>
                {classes.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}
      {!!list?.courses.length && !found.length && (
        <p className="empty-state">По этому запросу курсов нет.</p>
      )}
      <div className="gradebook-cards">
        {found.slice(0, SHOWN).map((c) => (
          <button
            key={keyOf(c)}
            type="button"
            className="gradebook-card"
            onClick={() => setOpen(keyOf(c))}
          >
            <strong>
              {c.group} · {c.subject}
            </strong>
            {!!c.classes.length && <span>{c.classes.join(", ")}</span>}
            {!!c.teachers.length && (
              <small className="pupil-muted">{c.teachers.join(", ")}</small>
            )}
            <span className={`badge gradebook-kind gradebook-kind-${c.kind}`}>
              {c.kind === "admin" && !c.canEdit
                ? "Администратор — только чтение"
                : KIND[c.kind]}
            </span>
          </button>
        ))}
      </div>
      {found.length > SHOWN && (
        <p className="pupil-muted">
          Показаны первые {SHOWN} курсов из {found.length}: уточните поиск.
        </p>
      )}
    </section>
  );
}

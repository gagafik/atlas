import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import CourseTablePanel from "./CourseTablePanel";
import type { CourseList } from "./courseTable";
import {
  ACCESS_FILTERS,
  DEFAULT_SORT,
  NO_FILTERS,
  buildRows,
  clampPage,
  commonAccess,
  courseKey,
  courseWord,
  filterRows,
  filtersActive,
  nextSort,
  offeredFilters,
  pageCount,
  pageRows,
  sortRows,
  summaryText,
  type AccessFilter,
  type CourseFilters,
  type SortKey,
  type SortSpec,
} from "./gradebookCourses";
import "./CourseTable.css";
import "./GradebookCourses.css";

const COLUMNS: { key: SortKey; label: string; className: string }[] = [
  { key: "group", label: "Класс / группа", className: "gc-group" },
  { key: "subject", label: "Предмет", className: "gc-subject" },
  { key: "teacher", label: "Учитель", className: "gc-teacher" },
  { key: "access", label: "Доступ", className: "gc-access" },
];

// «Оценки»: the courses (a class or group and a subject) as a filtered, sortable table; «Открыть» shows the grid of the
// course at full width. Filters, search, sort and page live here, so they are still there when the grid is closed.
export default function TeacherGradebook({
  workspace,
  onOpen,
}: {
  workspace: string;
  onOpen: (lessonKey: string, day: string) => void;
}) {
  const [list, setList] = useState<CourseList | null>(null),
    [error, setError] = useState(""),
    [search, setSearch] = useState(""),
    [filters, setFilters] = useState<CourseFilters>(NO_FILTERS),
    [sort, setSort] = useState<SortSpec>(DEFAULT_SORT),
    [page, setPage] = useState(0),
    [open, setOpen] = useState<string | null>(null);
  const first = useRef(true);
  const searchBox = useRef<HTMLInputElement>(null);
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
          setOpen(courseKey(value.courses[0]));
        first.current = false;
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [workspace]);

  const courses = list?.courses;
  const rows = useMemo(() => buildRows(courses ?? []), [courses]);
  const offered = useMemo(() => offeredFilters(courses ?? []), [courses]);
  const common = useMemo(() => commonAccess(courses ?? []), [courses]);
  const shown = useMemo(
    () => sortRows(filterRows(rows, filters, search), sort),
    [rows, filters, search, sort],
  );
  const safePage = clampPage(page, shown.length);
  const visible = pageRows(shown, safePage);
  const pages = pageCount(shown.length);
  // The access column is only worth a column when the texts differ.
  const columns = COLUMNS.filter((c) => c.key !== "access" || !common);
  const filtered = filtersActive(filters, search);

  function setFilter(patch: Partial<CourseFilters>) {
    setFilters((f) => ({ ...f, ...patch }));
    setPage(0);
  }
  function reset() {
    setFilters(NO_FILTERS);
    setSearch("");
    setPage(0);
    searchBox.current?.focus();
  }

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

  return (
    <section className="gc" aria-label="Оценки">
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
        <>
          <div className="gc-filters">
            <label className="gc-field gc-search">
              <span>Найти курс</span>
              <input
                ref={searchBox}
                type="search"
                value={search}
                placeholder="Группа, предмет, учитель"
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(0);
                }}
              />
            </label>
            {offered.showClass && (
              <label className="gc-field">
                <span>Класс</span>
                <select
                  value={filters.className}
                  onChange={(e) => setFilter({ className: e.target.value })}
                >
                  <option value="">Все классы</option>
                  {offered.classes.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {offered.showSubject && (
              <label className="gc-field">
                <span>Предмет</span>
                <select
                  value={filters.subjectId}
                  onChange={(e) => setFilter({ subjectId: e.target.value })}
                >
                  <option value="">Все предметы</option>
                  {offered.subjects.map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {offered.showTeacher && (
              <label className="gc-field">
                <span>Учитель</span>
                <select
                  value={filters.teacher}
                  onChange={(e) => setFilter({ teacher: e.target.value })}
                >
                  <option value="">Все учителя</option>
                  {offered.teachers.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {offered.showAccess && (
              <label className="gc-field">
                <span>Доступ</span>
                <select
                  value={filters.access}
                  onChange={(e) =>
                    setFilter({ access: e.target.value as AccessFilter })
                  }
                >
                  {ACCESS_FILTERS.map((x) => (
                    <option key={x.value} value={x.value}>
                      {x.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="gc-field gc-sortfield">
              <span>Сортировка</span>
              <select
                value={`${sort.key}:${sort.dir}`}
                onChange={(e) => {
                  const [key, dir] = e.target.value.split(":");
                  setSort({ key: key as SortKey, dir: dir as SortSpec["dir"] });
                  setPage(0);
                }}
              >
                {columns.flatMap((c) =>
                  (["asc", "desc"] as const).map((dir) => (
                    <option key={`${c.key}:${dir}`} value={`${c.key}:${dir}`}>
                      {c.label},{" "}
                      {dir === "asc" ? "по возрастанию" : "по убыванию"}
                    </option>
                  )),
                )}
              </select>
            </label>
            {filtered && (
              <button type="button" className="gc-reset" onClick={reset}>
                Сбросить
              </button>
            )}
          </div>
          <p className="gc-summary" aria-live="polite">
            {summaryText(shown.length, rows.length, filtered, common)}
          </p>
          <div className="table-card">
            {shown.length ? (
              <table className="data-table gc-table">
                <thead>
                  <tr>
                    {columns.map((c) => (
                      <th
                        key={c.key}
                        className={c.className}
                        aria-sort={
                          sort.key === c.key
                            ? sort.dir === "asc"
                              ? "ascending"
                              : "descending"
                            : undefined
                        }
                      >
                        <button
                          type="button"
                          className="gc-sort"
                          onClick={() => {
                            setSort((s) => nextSort(s, c.key));
                            setPage(0);
                          }}
                        >
                          {c.label}
                          <span aria-hidden="true">
                            {sort.key === c.key
                              ? sort.dir === "asc"
                                ? "▲"
                                : "▼"
                              : ""}
                          </span>
                        </button>
                      </th>
                    ))}
                    <th className="gc-act">
                      <span className="sr-only">Действия</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((r) => {
                    const classes = r.classes.join(", ");
                    const label = `${r.course.group} · ${r.course.subject}`;
                    return (
                      <tr key={r.id}>
                        <td
                          className="gc-group"
                          title={
                            r.showClasses
                              ? `${r.course.group} · ${classes}`
                              : r.course.group
                          }
                        >
                          <div className="gc-cell">
                            <button
                              type="button"
                              className="gc-name"
                              onClick={() => setOpen(r.id)}
                            >
                              {r.course.group}
                            </button>
                            {r.showClasses && (
                              <span className="gc-classes">{classes}</span>
                            )}
                          </div>
                        </td>
                        <td className="gc-subject" title={r.course.subject}>
                          {r.course.subject}
                        </td>
                        <td className="gc-teacher" title={r.teacherText}>
                          {r.teacherText}
                        </td>
                        {!common && (
                          <td className="gc-access">
                            <span
                              className={`badge ${r.course.kind === "course" ? "green" : "neutral"}`}
                            >
                              {r.accessText}
                            </span>
                          </td>
                        )}
                        <td className="gc-act">
                          <button
                            type="button"
                            className="gc-open"
                            aria-label={`Открыть ${label}`}
                            onClick={() => setOpen(r.id)}
                          >
                            Открыть
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div className="gc-none">
                <p className="empty-state">По этому запросу курсов нет.</p>
                {filtered && (
                  <button type="button" className="gc-reset" onClick={reset}>
                    Сбросить
                  </button>
                )}
              </div>
            )}
            <div className="table-footer">
              <span>
                {shown.length} {courseWord(shown.length)}
              </span>
              <div>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Предыдущая страница"
                  disabled={!safePage}
                  onClick={() => setPage(safePage - 1)}
                >
                  <ChevronLeft size={16} />
                </button>
                <span>
                  стр. {safePage + 1} из {pages}
                </span>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Следующая страница"
                  disabled={safePage + 1 >= pages}
                  onClick={() => setPage(safePage + 1)}
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

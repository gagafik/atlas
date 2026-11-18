import { ChevronLeft, ChevronRight, Pencil } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  DEFAULT_SORT,
  NO_FILTERS,
  buildRows,
  clampPage,
  filterOptions,
  filterRows,
  filtersActive,
  nextSort,
  pageCount,
  pageRows,
  sortRows,
  totals,
  type LoadFilters,
  type LoadSchool,
  type LoadStateFilter,
  type SortKey,
  type SortSpec,
} from "./teachingLoad";
import type { Workload } from "./types";
import "./TeachingLoad.css";

const STATES: { value: LoadStateFilter; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "unplaced", label: "Не расставлено" },
  { value: "noTeacher", label: "Учитель не назначен" },
  { value: "inactive", label: "Не ведётся в этом цикле" },
];

const COLUMNS: { key: SortKey; label: string; className: string }[] = [
  { key: "group", label: "Класс / группа", className: "tl-group" },
  { key: "subject", label: "Предмет", className: "tl-subject" },
  { key: "teacher", label: "Учитель", className: "tl-teacher" },
  { key: "hours", label: "Часов / нед.", className: "tl-hours" },
  { key: "placed", label: "В сетке", className: "tl-placed" },
  { key: "block", label: "Блок", className: "tl-block" },
];

// The list of teaching assignments of the school. Filters, sort and page live here, so they stay while the editor is open.
export function TeachingLoadList({
  school,
  counts,
  activeCycle,
  search,
  onEdit,
  empty,
}: {
  school: LoadSchool;
  counts: Record<string, number>;
  activeCycle: string;
  search: string;
  onEdit: (row: Workload) => void;
  /** Shown when the school has no assignment at all. */
  empty: ReactNode;
}) {
  const [filters, setFilters] = useState<LoadFilters>(NO_FILTERS);
  const [sort, setSort] = useState<SortSpec>(DEFAULT_SORT);
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [search]);

  const rows = useMemo(
    () => buildRows(school, counts, activeCycle),
    [school, counts, activeCycle],
  );
  const options = useMemo(() => filterOptions(school), [school]);
  const shown = useMemo(
    () => sortRows(filterRows(rows, filters, search), sort),
    [rows, filters, search, sort],
  );
  const sum = totals(shown);
  const safePage = clampPage(page, shown.length);
  const visible = pageRows(shown, safePage);
  const pages = pageCount(shown.length);
  // Keep the stored page equal to the shown one, so rows coming back do not move the list.
  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);
  const firstFilter = useRef<HTMLSelectElement>(null);

  function setFilter(patch: Partial<LoadFilters>) {
    setFilters((f) => ({ ...f, ...patch }));
    setPage(0);
  }
  function reset() {
    setFilters(NO_FILTERS);
    setPage(0);
    firstFilter.current?.focus();
  }
  function sortBy(key: SortKey) {
    setSort((s) => nextSort(s, key));
    setPage(0);
  }

  if (!rows.length) return <>{empty}</>;

  return (
    <div className="tl">
      <div className="tl-filters">
        <label className="tl-field">
          <span>Класс</span>
          <select
            ref={firstFilter}
            value={filters.classId}
            onChange={(e) => setFilter({ classId: e.target.value })}
          >
            <option value="">Все классы</option>
            {options.classes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="tl-field">
          <span>Учитель</span>
          <select
            value={filters.teacherId}
            onChange={(e) => setFilter({ teacherId: e.target.value })}
          >
            <option value="">Все учителя</option>
            {options.teachers.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="tl-field">
          <span>Предмет</span>
          <select
            value={filters.subjectId}
            onChange={(e) => setFilter({ subjectId: e.target.value })}
          >
            <option value="">Все предметы</option>
            {options.subjects.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <label className="tl-field">
          <span>Состояние</span>
          <select
            value={filters.state}
            onChange={(e) =>
              setFilter({ state: e.target.value as LoadStateFilter })
            }
          >
            {STATES.map((x) => (
              <option key={x.value} value={x.value}>
                {x.label}
              </option>
            ))}
          </select>
        </label>
        <label className="tl-field tl-sortfield">
          <span>Сортировка</span>
          <select
            value={`${sort.key}:${sort.dir}`}
            onChange={(e) => {
              const [key, dir] = e.target.value.split(":");
              setSort({ key: key as SortKey, dir: dir as SortSpec["dir"] });
              setPage(0);
            }}
          >
            {COLUMNS.flatMap((c) =>
              (["asc", "desc"] as const).map((dir) => (
                <option key={`${c.key}:${dir}`} value={`${c.key}:${dir}`}>
                  {c.label}, {dir === "asc" ? "по возрастанию" : "по убыванию"}
                </option>
              )),
            )}
          </select>
        </label>
        {filtersActive(filters) && (
          <button type="button" className="tl-reset" onClick={reset}>
            Сбросить
          </button>
        )}
      </div>
      <p className="tl-summary" aria-live="polite">
        {sum.count} назначений · {sum.hours} часов в неделю · в сетке{" "}
        {sum.placed} из {sum.hours}
      </p>
      <div className="table-card">
        {shown.length ? (
          <div className="tl-scroll">
            <table className="data-table tl-table">
              <thead>
                <tr>
                  {COLUMNS.map((c) => (
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
                        className="tl-sort"
                        onClick={() => sortBy(c.key)}
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
                  <th className="tl-act">
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => {
                  const classes = r.classNames.join(", ");
                  const groupTitle = r.wholeClass
                    ? r.groupName
                    : `${r.groupName} · ${classes}`;
                  return (
                    <tr key={r.id}>
                      <td className="tl-group" title={groupTitle}>
                        <strong>{r.groupName}</strong>
                        {!r.wholeClass && classes && (
                          <span className="tl-classes">{classes}</span>
                        )}
                      </td>
                      <td className="tl-subject" title={r.subjectName}>
                        <span className="badge neutral">{r.subjectName}</span>
                      </td>
                      <td className="tl-teacher" title={r.teacherText}>
                        {r.teacherMissing ? (
                          <span className="amber-text">{r.teacherText}</span>
                        ) : (
                          r.teacherText
                        )}
                      </td>
                      <td className="tl-hours">
                        <strong>{r.active ? r.hours : "Не ведётся"}</strong>
                        {r.active && <span className="tl-unit"> ч / нед.</span>}
                      </td>
                      <td className="tl-placed">
                        <span
                          className={`badge ${r.placed === r.hours ? "green" : "amber"}`}
                        >
                          {r.placed} / {r.hours}
                        </span>
                      </td>
                      <td className="tl-block">{r.block || "—"}</td>
                      <td className="tl-act">
                        <button
                          type="button"
                          className="icon-button tl-edit"
                          title="Редактировать"
                          aria-label="Редактировать нагрузку"
                          onClick={() => onEdit(r.workload)}
                        >
                          <Pencil size={15} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="tl-none">
            <h3>По этим условиям ничего не найдено</h3>
            {filtersActive(filters) && (
              <button type="button" className="tl-reset" onClick={reset}>
                Сбросить
              </button>
            )}
          </div>
        )}
        <div className="table-footer">
          <span>{shown.length} записей</span>
          <div>
            <button
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
    </div>
  );
}

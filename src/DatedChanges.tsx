import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { ChangeType, DatedLesson, State } from "./types";
import {
  NO_CHANGE_FILTERS,
  changeColumns,
  changeFiltersActive,
  changePage,
  changePageCount,
  changeRows,
  changeTypeOptions,
  clampChangePage,
  dateShort,
  filterChanges,
  wordForm,
  type ChangeNames,
  type ChangeFilters,
} from "./datedLists";
import "./DatedDay.css";
import "./DatedLists.css";

// «Все изменения»: every one-off change of the school year as a table with search, type filter and pages.
export default function DatedChanges({
  state: s,
  busy,
  onOpenDate,
  onEdit,
  onRemove,
}: {
  state: State;
  busy: boolean;
  onOpenDate: (c: DatedLesson) => void;
  onEdit: (c: DatedLesson) => void;
  onRemove: (c: DatedLesson) => void;
}) {
  const [filters, setFilters] = useState<ChangeFilters>(NO_CHANGE_FILTERS);
  const [page, setPage] = useState(0);
  const names = useMemo<ChangeNames>(() => {
    const nameOf = (items: { id: string; name: string }[]) => {
      const byId = new Map(items.map((i) => [i.id, i.name]));
      return (id: string) => byId.get(id) ?? "";
    };
    const workloads = new Map(s.workloads.map((w) => [w.id, w]));
    const placements = new Map(s.placements.map((p) => [p.id, p]));
    return {
      group: nameOf(s.groups),
      subject: nameOf(s.subjects),
      teacher: nameOf(s.teachers),
      placement: (id) => {
        const w = workloads.get(placements.get(id)?.workloadId ?? "");
        return w && { groupId: w.groupId, subjectId: w.subjectId };
      },
    };
  }, [s.groups, s.subjects, s.teachers, s.workloads, s.placements]);
  const all = useMemo(
    () => changeRows(s.datedLessons || [], names),
    [s.datedLessons, names],
  );
  const columns = changeColumns(all.map((r) => r.view));
  const types = changeTypeOptions(all.map((r) => r.view));
  // A type that no record has any more cannot stay selected.
  const effective: ChangeFilters = types.some((t) => t.type === filters.type)
    ? filters
    : { ...filters, type: "" };
  const shown = useMemo(
    () => filterChanges(all, effective),
    [all, effective.search, effective.type],
  );
  const safePage = clampChangePage(page, shown.length);
  const pages = changePageCount(shown.length);
  const visible = changePage(shown, safePage);
  useEffect(() => setPage(0), [filters]);
  const set = (patch: Partial<ChangeFilters>) =>
    setFilters({ ...filters, ...patch });
  if (!all.length)
    return (
      <div className="dl">
        <p>Разовых изменений пока нет.</p>
      </div>
    );
  return (
    <div className="dl">
      <div className="dd-controls">
        <label className="field dl-search">
          <span>Поиск</span>
          <input
            type="search"
            placeholder="Занятие, тип, название"
            value={filters.search}
            onChange={(e) => set({ search: e.target.value })}
          />
        </label>
        <label className="field dl-type">
          <span>Тип</span>
          <select
            value={effective.type}
            onChange={(e) => set({ type: e.target.value as ChangeType | "" })}
          >
            <option value="">Все типы</option>
            {types.map((t) => (
              <option key={t.type} value={t.type}>
                {t.text}
              </option>
            ))}
          </select>
        </label>
      </div>
      {shown.length ? (
        <div className="table-card">
          <table className="data-table dl-table dl-blocks">
            <caption className="sr-only">Разовые изменения расписания</caption>
            <thead>
              <tr>
                <th scope="col" className="dl-cell dl-fit">
                  Дата
                </th>
                <th scope="col" className="dl-cell dl-fit">
                  Тип
                </th>
                <th scope="col" className="dl-cell">
                  Занятие
                </th>
                {columns.source && (
                  <th scope="col" className="dl-cell dl-fit">
                    Исходная дата
                  </th>
                )}
                <th scope="col" className="dl-cell dl-act">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map(({ change: c, view: v }) => (
                <tr key={v.id}>
                  <td className="dl-cell dl-fit">{dateShort(v.date)}</td>
                  <td className="dl-cell dl-fit">
                    <span className="dd-badge quiet">{v.typeText}</span>
                  </td>
                  <td
                    className="dl-cell dl-text dl-main"
                    title={v.name ? `${v.lesson} · ${v.name}` : v.lesson}
                  >
                    <strong>{v.lesson}</strong>
                    {v.name && <span className="dl-name">{v.name}</span>}
                  </td>
                  {columns.source && (
                    <td className="dl-cell dl-fit">
                      {v.sourceDate && dateShort(v.sourceDate)}
                    </td>
                  )}
                  <td className="dl-cell dl-act">
                    <div className="dl-acts">
                      <button
                        type="button"
                        className="dd-btn"
                        aria-label={v.labels.open}
                        onClick={() => onOpenDate(c)}
                      >
                        Открыть дату
                      </button>
                      <button
                        type="button"
                        className="dd-btn"
                        aria-label={v.labels.edit}
                        disabled={busy}
                        onClick={() => onEdit(c)}
                      >
                        Изменить
                      </button>
                      <button
                        type="button"
                        className="dd-btn"
                        aria-label={v.labels.remove}
                        disabled={busy}
                        onClick={() => onRemove(c)}
                      >
                        Убрать
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="dl-none">
          <h3>По этим условиям ничего не найдено</h3>
          {changeFiltersActive(effective) && (
            <button
              type="button"
              className="dd-btn"
              onClick={() => setFilters(NO_CHANGE_FILTERS)}
            >
              Сбросить
            </button>
          )}
        </div>
      )}
      <div className="table-footer dl-footer">
        <span>
          {shown.length} {wordForm(shown.length, "запись", "записи", "записей")}
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
  );
}

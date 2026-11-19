import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  filterEntries,
  nextSort,
  prepare,
  sortEntries,
  type ColumnRule,
  type SortSpec,
} from "./directoryTable";
import { clampPage, pageCount, pageRows, wordForm } from "./teachingLoad";
import "./DirectoryList.css";

export type DirColumn<T> = ColumnRule<T> & {
  label: string;
  render: (row: T) => ReactNode;
  /** A long text column: shares the free width with the first one and is cut with an ellipsis. */
  grow?: boolean;
  /** Numbers are aligned to the right. */
  align?: "right";
};

// A directory list of the administrator: dense rows, sortable headings, search over the shown text, 20 rows a page.
// The first column is the name of the row; it is the default order and the line a phone card starts with.
export function DirectoryTable<T extends { id: string }>({
  rows,
  columns,
  order,
  actions,
  search,
  onSearch,
  label,
  empty,
}: {
  rows: T[];
  columns: DirColumn<T>[];
  /** The default order of the rows (by name, classes in school order). */
  order: (a: T, b: T) => number;
  /** The row action buttons. */
  actions: (row: T) => ReactNode;
  search: string;
  /** Given: the list draws its own search field above the table. Omitted: the page already has one. */
  onSearch?: (text: string) => void;
  /** The accessible name of the table. */
  label: string;
  /** What to show when there is nothing to list; the argument says whether a search is the reason. */
  empty: (searching: boolean) => ReactNode;
}) {
  const [sort, setSort] = useState<SortSpec>({
    key: columns[0].key,
    dir: "asc",
  });
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [search]);

  const entries = useMemo(() => prepare(rows, columns), [rows, columns]);
  const shown = useMemo(
    () => sortEntries(filterEntries(entries, search), columns, sort, order),
    [entries, search, columns, sort, order],
  );
  const safePage = clampPage(page, shown.length);
  const visible = pageRows(shown, safePage);
  const pages = pageCount(shown.length);
  // Keep the stored page equal to the shown one, so rows coming back do not move the list.
  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);

  function sortBy(key: string) {
    setSort((s) => nextSort(s, key));
    setPage(0);
  }
  const count = `${shown.length} ${wordForm(shown.length, "запись", "записи", "записей")}`;

  return (
    <div className="dt">
      <div className={onSearch ? "dt-head" : "dt-head dt-bare"}>
        {onSearch && (
          <span className="dt-count" aria-live="polite">
            {count}
          </span>
        )}
        <label className="dt-sortfield">
          <span className="sr-only">Сортировка</span>
          <select
            value={`${sort.key}:${sort.dir}`}
            onChange={(e) => {
              const [key, dir] = e.target.value.split(":");
              setSort({ key, dir: dir as SortSpec["dir"] });
              setPage(0);
            }}
          >
            {columns.flatMap((c) =>
              (["asc", "desc"] as const).map((dir) => (
                <option key={`${c.key}:${dir}`} value={`${c.key}:${dir}`}>
                  {c.label}, {dir === "asc" ? "по возрастанию" : "по убыванию"}
                </option>
              )),
            )}
          </select>
        </label>
        {onSearch && (
          <label className="search-field dt-search">
            <Search size={17} />
            <input
              placeholder="Поиск…"
              aria-label="Поиск в справочнике"
              value={search}
              onChange={(e) => onSearch(e.target.value)}
            />
          </label>
        )}
      </div>
      <div className="table-card">
        {shown.length ? (
          <table className="data-table dt-table" role="table" aria-label={label}>
            <thead role="rowgroup">
              <tr role="row">
                {columns.map((c, i) => (
                  <th
                    key={c.key}
                    role="columnheader"
                    className={cellClass(c, i)}
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
                      className="dt-sort"
                      title={c.label}
                      onClick={() => sortBy(c.key)}
                    >
                      <span className="dt-sort-label">{c.label}</span>
                      <span className="dt-sort-mark" aria-hidden="true">
                        {sort.key === c.key
                          ? sort.dir === "asc"
                            ? "▲"
                            : "▼"
                          : ""}
                      </span>
                    </button>
                  </th>
                ))}
                <th className="dt-act" role="columnheader">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {visible.map((e) => (
                <tr key={e.row.id} role="row">
                  {columns.map((c, i) => (
                    <td
                      key={c.key}
                      role="cell"
                      className={cellClass(c, i)}
                      data-label={i ? c.label : undefined}
                      title={i === 0 || c.grow ? e.texts[i] : undefined}
                    >
                      {c.render(e.row)}
                    </td>
                  ))}
                  <td className="dt-act" role="cell">
                    {actions(e.row)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          empty(search.trim() !== "")
        )}
        <div className="table-footer dt-footer">
          <span>{count}</span>
          {pages > 1 && (
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
          )}
        </div>
      </div>
    </div>
  );
}

function cellClass<T>(c: DirColumn<T>, i: number) {
  return [
    "dt-col",
    i === 0 ? "dt-main" : c.grow ? "dt-grow" : "dt-cell",
    c.align === "right" ? "dt-num" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

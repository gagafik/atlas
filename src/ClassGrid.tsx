import { forwardRef, useMemo } from "react";
import type { Row } from "./finalGradesTypes";
import {
  cellLabel,
  classGrid,
  gridGpa,
  hasPoints,
  statusMark,
  type GridLayout,
} from "./reportCardsView";

/** The class statement: pupils in rows, the subjects of the report card in columns, the final grade and its status in every cell. */
const ClassGrid = forwardRef<
  HTMLDivElement,
  {
    rows: Row[];
    layout: GridLayout;
    className: string;
    periodName: string;
    // The subject whose table is open, marked in the header.
    subjectId?: string;
    onSubject: (subjectId: string) => void;
    onCell: (row: Row) => void;
    onPupil: (pupilId: string) => void;
  }
>(function ClassGrid(
  {
    rows,
    layout,
    className,
    periodName,
    subjectId,
    onSubject,
    onCell,
    onPupil,
  },
  ref,
) {
  const grid = useMemo(() => classGrid(rows, layout), [rows, layout]);
  const hiddenIds = grid.subjects.filter((s) => s.hidden).map((s) => s.id);
  const average = hasPoints(rows);
  if (!grid.pupils.length) return <p>В ведомости нет итогов этого класса.</p>;
  return (
    <div className="finals-grid-box" ref={ref}>
      <table className="finals-grid">
        <caption className="sr-only">
          Ведомость класса {className}, {periodName}
        </caption>
        <thead>
          <tr>
            <th scope="col" className="finals-grid-pupil">
              Ученик
            </th>
            {grid.subjects.map((s) => (
              <th
                scope="col"
                key={s.id}
                className={s.hidden ? "finals-grid-hidden" : undefined}
              >
                <button
                  type="button"
                  className="finals-head-button"
                  aria-pressed={subjectId === s.id}
                  aria-label={`Таблица предмета ${s.name}${s.hidden ? " (не в табеле)" : ""}`}
                  onClick={() => onSubject(s.id)}
                >
                  {s.name}
                </button>
                {s.hidden && (
                  <span className="finals-hidden-note">не в табеле</span>
                )}
              </th>
            ))}
            {average && <th scope="col">Средний балл</th>}
          </tr>
        </thead>
        <tbody>
          {grid.pupils.map((p) => (
            <tr key={p.id}>
              <th scope="row" className="finals-grid-pupil">
                <button
                  type="button"
                  className="finals-name-button"
                  aria-label={`Табель ученика ${p.name}`}
                  onClick={() => onPupil(p.id)}
                >
                  {p.name}
                </button>
              </th>
              {grid.subjects.map((s) => {
                const row = grid.cell(p.id, s.id);
                if (!row)
                  return (
                    <td
                      key={s.id}
                      className={s.hidden ? "finals-grid-hidden" : undefined}
                    >
                      <span aria-label="Нет строки">·</span>
                    </td>
                  );
                const mark = statusMark(row.status);
                const text = `${p.name}, ${s.name}: ${cellLabel(row)}, ${mark.full}`;
                return (
                  <td
                    key={s.id}
                    className={s.hidden ? "finals-grid-hidden" : undefined}
                  >
                    <button
                      type="button"
                      className="finals-cell-button"
                      title={mark.full}
                      aria-label={text}
                      onClick={() => onCell(row)}
                    >
                      <b>{cellLabel(row)}</b>
                      <span className="finals-mark">{mark.short}</span>
                    </button>
                  </td>
                );
              })}
              {average && (
                <td className="finals-num">
                  {gridGpa(
                    rows.filter((r) => r.studentId === p.id),
                    hiddenIds,
                  ) ?? "—"}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
});
export default ClassGrid;

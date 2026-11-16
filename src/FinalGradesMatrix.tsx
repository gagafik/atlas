import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import FinalGradesTable from "./FinalGradesTable";
import { cellKey, chunked, eligibleFor, visibleSelection } from "./finalGrades";
import type { Book, Cell, Matrix, Named, Row, Scale } from "./finalGradesTypes";
import { courseRows, stateText } from "./reportCardsView";
import "./FinalGrades.css";

type Handle = { id: string; version: number; status: string };

const ACTIONS: Record<string, string> = {
  assign: "Назначить учителя",
  approve: "Утвердить",
  publish: "Опубликовать",
};
// Text first, so the state of a cell never depends on colour.
const progress = (c: Cell) =>
  [
    c.draft && `черновик ${c.draft}`,
    c.submitted && `на утверждении ${c.submitted}`,
    c.approved && `утверждено ${c.approved}`,
    c.published && `опубликовано ${c.published}`,
  ]
    .filter(Boolean)
    .join(", ") + ` из ${c.total}`;

const cellText = (d: Matrix, c: Cell) =>
  `${d.classes.find((x) => x.id === c.classId)?.name || c.classId}, ${d.subjects.find((x) => x.id === c.subjectId)?.name || c.subjectId}`;

// Progress of the whole book by class and subject. A teacher sees a compact table of own courses, the administrator a grid with mass actions.
export default function FinalGradesMatrix({
  workspace,
  book,
  admin,
  scales,
  phrases,
  teachers,
  lastScale,
  refresh = 0,
  leave,
  onDirty,
  onChanged,
  onOpenRow,
  tableEpoch = 0,
}: {
  workspace: string;
  book: Book;
  admin: boolean;
  scales: Scale[];
  phrases: string[];
  teachers: Named[];
  lastScale: { current: string };
  // Raised by the parent after changes made outside this matrix (the row list, the preparation, another matrix).
  refresh?: number;
  leave: () => boolean;
  onDirty: (table: string, dirty: boolean) => void;
  onChanged: () => void;
  // Makes the pupils of the opened table buttons that open the details of the row.
  onOpenRow?: (row: Row) => void;
  // Raised by the parent when a row was changed outside the table: the open table is read again.
  tableEpoch?: number;
}) {
  const [data, setData] = useState<Matrix | null>(null),
    [selected, setSelected] = useState<string[]>([]),
    [opened, setOpened] = useState<Cell | null>(null),
    [action, setAction] = useState("approve"),
    [teacherId, setTeacherId] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const lock = useRef(false),
    generation = useRef(0),
    mounted = useRef(false);

  // Two loads can overlap; only the newest answer is shown.
  async function load() {
    const mine = ++generation.current;
    const r = await api(`final-grades/books/${book.id}/matrix`, workspace);
    if (mine === generation.current) setData(r);
  }
  const shown = (d: Matrix | null) => d?.cells || [];
  useEffect(() => {
    setOpened(null);
    setSelected([]);
    setError("");
    void load().catch((e) => setError(e instanceof Error ? e.message : ""));
  }, [book.id]);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    void load().catch((e) => setError(e instanceof Error ? e.message : ""));
  }, [refresh]);

  async function run(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить действие.",
      );
    } finally {
      setBusy(false);
      lock.current = false;
    }
  }
  async function perform() {
    // A tick that is no longer on screen (another class was chosen) is dropped, never acted on.
    const picked = visibleSelection(selected, shown(data));
    if (!picked.length || !leave()) return;
    // The open table would hold versions that this action changes.
    setOpened(null);
    await run(async () => {
      const rows: { id: string; version: number }[] = [];
      for (const key of picked) {
        const [classId, subjectId] = JSON.parse(key) as string[];
        const r = await api(
          `final-grades/books/${book.id}/handles?${new URLSearchParams({ classId, subjectId })}`,
          workspace,
        );
        // Only rows in the status the action accepts are sent.
        rows.push(
          ...(r.items as Handle[])
            .filter((x) => eligibleFor(action, x))
            .map((x) => ({ id: x.id, version: x.version })),
        );
      }
      if (!rows.length) {
        setNotice(
          "В выбранных ячейках нет строк, к которым применимо это действие.",
        );
        return;
      }
      if (
        action === "publish" &&
        !window.confirm(
          `Опубликовать итоги (${rows.length}) ученикам и родителям? Ячейки: ${listed(picked)}.`,
        )
      )
        return;
      let done = 0;
      try {
        for (const part of chunked(rows, 100)) {
          await api("final-grades/actions", workspace, "POST", {
            action,
            rows: part,
            teacherId: action === "assign" ? teacherId : "",
            reason: "",
          });
          done += part.length;
        }
      } catch (e) {
        await load().catch(() => undefined);
        throw new Error(
          `Выполнено ${done} из ${rows.length}; остальные строки не изменены. ${e instanceof Error ? e.message : ""}`,
        );
      }
      setSelected([]);
      await load();
      onChanged();
      setNotice(
        `${ACTIONS[action]}: выполнено для ${rows.length} строк${rows.length > 100 ? `, несколькими запросами по 100` : ""}.`,
      );
    });
  }
  // The cells of an action by name, so a confirmation shows what it touches and not only how many rows.
  function listed(keys: string[]) {
    const names = shown(data)
      .filter((c) => keys.includes(cellKey(c)))
      .map((c) => cellText(data!, c));
    return names.length > 5
      ? `${names.slice(0, 5).join("; ")} и ещё ${names.length - 5}`
      : names.join("; ");
  }
  function open(cell: Cell) {
    if (leave()) setOpened(cell);
  }

  if (!data)
    return error ? (
      <p role="alert" className="error">
        {error}
      </p>
    ) : (
      <p role="status">Загрузка…</p>
    );
  const cells = shown(data);
  const classes = data.classes;
  const find = (c: string, s: string) =>
    cells.find((x) => x.classId === c && x.subjectId === s);
  const label = (c: Cell) => cellText(data, c);
  const picked = visibleSelection(selected, cells);
  const unassigned = cells.reduce((n, c) => n + c.unassigned, 0);
  const unavailable = cells.reduce((n, c) => n + c.unavailable, 0);
  return (
    <div className="final-matrix">
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {admin ? (
        <>
          <p>
            Строк без ответственного: {unassigned}.
            {unavailable > 0 &&
              ` Черновиков, у которых учётная запись учителя недоступна: ${unavailable}; назначьте другого учителя.`}{" "}
            Ячейка — один класс и один предмет; отметьте нужные и выберите
            действие.
          </p>
          <div className="final-table-wrap">
            <table className="final-table final-grid">
              <thead>
                <tr>
                  <th>Класс</th>
                  {data.subjects.map((s) => (
                    <th key={s.id}>{s.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {classes.map((c) => (
                  <tr key={c.id}>
                    <th scope="row">{c.name}</th>
                    {data.subjects.map((s) => {
                      const cell = find(c.id, s.id);
                      if (!cell) return <td key={s.id} />;
                      return (
                        <td key={s.id}>
                          <label>
                            <input
                              type="checkbox"
                              aria-label={`Выбрать ${label(cell)}`}
                              checked={selected.includes(cellKey(cell))}
                              onChange={(e) =>
                                setSelected(
                                  e.target.checked
                                    ? [...selected, cellKey(cell)]
                                    : selected.filter(
                                        (k) => k !== cellKey(cell),
                                      ),
                                )
                              }
                            />
                          </label>{" "}
                          <button
                            aria-label={`Открыть таблицу: ${label(cell)}`}
                            onClick={() => open(cell)}
                          >
                            {cell.published}/{cell.total}
                          </button>
                          <small>{progress(cell)}</small>
                          {cell.unassigned > 0 && (
                            <small> · без учителя {cell.unassigned}</small>
                          )}
                          {cell.unavailable > 0 && (
                            <small>
                              {" "}
                              · учётная запись недоступна {cell.unavailable}
                            </small>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="dated-toolbar">
            <label className="field">
              Действие над выбранными ячейками
              <select
                value={action}
                onChange={(e) => setAction(e.target.value)}
              >
                {Object.entries(ACTIONS).map(([id, text]) => (
                  <option key={id} value={id}>
                    {text}
                  </option>
                ))}
              </select>
            </label>
            {action === "assign" && (
              <label className="field">
                Ответственный учитель
                <select
                  value={teacherId}
                  onChange={(e) => setTeacherId(e.target.value)}
                >
                  <option value="">Снять назначение</option>
                  {teachers.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button
              disabled={busy || !picked.length || !book.current}
              onClick={() => void perform()}
            >
              Выполнить для выбранных ({picked.length})
            </button>
          </div>
          <p>
            Действие затрагивает только строки в подходящем статусе. Большой
            выбор выполняется несколькими запросами; при сбое остальные строки
            не меняются, а число выполненных показывается.
          </p>
        </>
      ) : (
        !opened && (
          <>
            {!cells.length && (
              <p>Вам пока не назначены строки итоговых оценок.</p>
            )}
            {!!cells.length && (
              <div className="finals-box">
                <div className="finals-scroll">
                  <table className="finals-table" aria-label="Мои предметы">
                    <thead>
                      <tr>
                        <th>Класс</th>
                        <th>Предмет</th>
                        <th>Учеников</th>
                        <th>Выставлено</th>
                        <th>Состояние</th>
                        <th>
                          <span className="sr-only">Действия</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {courseRows(data).map((c) => (
                        <tr key={cellKey(c)}>
                          <td data-label="Класс">
                            <strong>{c.className}</strong>
                          </td>
                          <td data-label="Предмет">{c.subjectName}</td>
                          <td data-label="Учеников">{c.total}</td>
                          <td data-label="Выставлено">
                            {c.state.done} из {c.total}
                          </td>
                          <td data-label="Состояние" className="finals-state">
                            {stateText(c.state)}
                          </td>
                          <td className="finals-actions">
                            <button
                              type="button"
                              className="finals-row-button"
                              aria-label={`Открыть таблицу: ${c.className}, ${c.subjectName}`}
                              onClick={() => open(c)}
                            >
                              Открыть
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )
      )}
      {opened && (
        <section className="final-table-section">
          <h3>{label(opened)}</h3>
          <div className="dated-toolbar">
            <button
              onClick={() => {
                if (leave()) setOpened(null);
              }}
            >
              {admin ? "Закрыть таблицу" : "← Мои предметы"}
            </button>
            {!admin && (
              <a
                href={`/api/final-grades/books/${book.id}/export?${new URLSearchParams({ workspace, classId: opened.classId, subjectId: opened.subjectId, status: "all" })}`}
                download
              >
                CSV
              </a>
            )}
          </div>
          <FinalGradesTable
            key={`${cellKey(opened)}-${tableEpoch}`}
            workspace={workspace}
            bookId={book.id}
            classId={opened.classId}
            subjectId={opened.subjectId}
            scales={scales}
            phrases={phrases}
            current={book.current}
            lastScale={lastScale}
            onDirty={onDirty}
            onOpenRow={onOpenRow}
            onChanged={() => {
              void load();
              onChanged();
            }}
          />
        </section>
      )}
    </div>
  );
}

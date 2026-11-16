import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import FinalGradeRowDetails from "./FinalGradeRowDetails";
import FinalGradeRows from "./FinalGradeRows";
import FinalGradesClassView from "./FinalGradesClassView";
import FinalGradesMatrix from "./FinalGradesMatrix";
import FinalGradesSettings, { type SettingsTab } from "./FinalGradesSettings";
import FinalSelect from "./FinalSelect";
import type {
  FinalsShared,
  Job,
  Matrix,
  Period,
  Row,
} from "./finalGradesTypes";
import PupilReportCard from "./PupilReportCard";
import ReportPrintDialog from "./ReportPrintDialog";
import {
  bookFor,
  classRows,
  defaultPeriod,
  finishedBook,
  searchPupils,
  stateText,
  type ClassRow,
} from "./reportCardsView";
import {
  isActiveJob,
  JOB_PHASE,
  JOB_STATUS,
  usePreparations,
} from "./usePreparations";
import "./FinalGrades.css";

type View =
  | { kind: "home" }
  | { kind: "class"; classId: string }
  | { kind: "pupil"; studentId: string; reveal: boolean };
type Tab = "classes" | "subjects" | "rows";
const TABS: { id: Tab; title: string }[] = [
  { id: "classes", title: "Классы" },
  { id: "subjects", title: "По предметам" },
  { id: "rows", title: "Все строки" },
];

/** «Табели» of an administrator, the classes of the period: one compact row per class, with what it waits for. */
export function ReportClassesTable({
  rows,
  busy,
  onOpen,
  onPrint,
}: {
  rows: ClassRow[];
  // A class is being opened or something is saving: no second click.
  busy: boolean;
  onOpen: (classId: string) => void;
  onPrint: (classId: string) => void;
}) {
  if (!rows.length) return <p>В ведомости нет классов.</p>;
  return (
    <div className="finals-box">
      <div className="finals-scroll">
        <table className="finals-table" aria-label="Классы">
          <thead>
            <tr>
              <th>Класс</th>
              <th>Учеников</th>
              <th>Итоги</th>
              <th>Утверждено</th>
              <th>Опубликовано</th>
              <th>Состояние</th>
              <th>
                <span className="sr-only">Действия</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr
                key={c.id}
                className={
                  c.state.kind === "unassigned"
                    ? "finals-attention"
                    : c.state.kind === "published"
                      ? "finals-done"
                      : undefined
                }
              >
                <td data-label="Класс">
                  <strong>{c.name}</strong>
                </td>
                <td data-label="Учеников" className="finals-num">
                  {c.pupils}
                </td>
                <td data-label="Итоги" className="finals-num">
                  {c.state.done} / {c.total}
                </td>
                <td data-label="Утверждено" className="finals-num">
                  {c.approved + c.published}
                </td>
                <td data-label="Опубликовано" className="finals-num">
                  {c.published}
                </td>
                <td data-label="Состояние" className="finals-state">
                  {stateText(c.state)}
                </td>
                <td className="finals-actions">
                  <button
                    type="button"
                    className="finals-row-button"
                    disabled={busy}
                    aria-label={`Открыть класс ${c.name}: ${stateText(c.state)}`}
                    onClick={() => onOpen(c.id)}
                  >
                    Открыть
                  </button>
                  <button
                    type="button"
                    className="finals-row-button"
                    disabled={busy}
                    aria-label={`Печать табелей класса ${c.name}`}
                    onClick={() => onPrint(c.id)}
                  >
                    Печать
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** What to do when the chosen period has no statement: one sentence and, where it can be done, one button. */
export function NoStatement({
  period,
  archived,
  job,
  busy,
  onPrepare,
}: {
  period?: Period;
  // A past school year: statements are not prepared there.
  archived: boolean;
  // The newest job of this period, if there is one.
  job?: Job;
  busy: boolean;
  onPrepare: () => void;
}) {
  const running = !!job && isActiveJob(job);
  const closed = period?.status === "closed";
  return (
    <section className="finals-empty" aria-label="Ведомость периода">
      {!period && (
        <p>Закрытых периодов пока нет: итоги готовятся по их архиву.</p>
      )}
      {period && !closed && (
        <p>
          Период «{period.name}» ещё не закрыт. Ведомость итогов готовится по
          архиву закрытого периода.
        </p>
      )}
      {period && closed && archived && (
        <p>
          Для периода «{period.name}» нет ведомости. В архивном году её уже
          нельзя подготовить.
        </p>
      )}
      {period && closed && !archived && (
        <p>
          Для периода «{period.name}» ещё нет ведомости: итоги готовятся по
          архиву закрытого периода.
        </p>
      )}
      {job && (
        <p role="status">
          {JOB_STATUS[job.status] || job.status} ·{" "}
          {JOB_PHASE[job.phase] || job.phase}
          {job.message ? ` · ${job.message}` : ""}
        </p>
      )}
      {running && job.total > 0 && (
        <progress
          max={job.total}
          value={job.completed}
          aria-label={`${JOB_PHASE[job.phase] || "Подготовка"}: ${job.periodName}`}
        />
      )}
      {closed && !archived && (
        <button disabled={busy || running} onClick={onPrepare}>
          Подготовить ведомость
        </button>
      )}
    </section>
  );
}

// The first view of the administrator: the period, the classes of its statement and everything around it.
export default function FinalGradesHome({
  workspace,
  year,
  options,
  categories,
  onCategoriesSaved,
  lastScale,
  epoch,
  changed,
  leave,
  dirty,
  onDirty,
  reloadOptions,
  refresh,
  busy,
  run,
  setNotice,
  focus,
}: FinalsShared & {
  // Set by an opened notification: show the report card of this pupil for this period.
  focus?: { studentId: string; finalsPeriodId?: string };
}) {
  const readOnly = !!year;
  const preparations = usePreparations(workspace);
  const [periodId, setPeriodId] = useState(() =>
    defaultPeriod(options.periods, options.books),
  );
  // An older statement of the period, chosen in the settings.
  const [bookOverride, setBookOverride] = useState("");
  const [tab, setTab] = useState<Tab>("classes");
  const [view, setView] = useState<View>({ kind: "home" });
  const [settings, setSettings] = useState<SettingsTab | null>(null);
  const [search, setSearch] = useState("");
  // The class whose cards the «Печать» of the classes table opens in a dialog.
  const [printClass, setPrintClass] = useState("");
  // The row opened from a pupil name in the table of «По предметам», and the raised counter that reads that table again.
  const [detail, setDetail] = useState<Row | null>(null);
  const [tableEpoch, setTableEpoch] = useState(0);
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [matrixError, setMatrixError] = useState("");
  const generation = useRef(0);
  const reloaded = useRef("");

  const period = options.periods.find((p) => p.id === periodId);
  const found = bookFor(periodId, options.books, bookOverride);
  // An archived year is read-only: the tables get a statement that is not current, which is their read-only path.
  const book = found && readOnly ? { ...found, current: false } : found;
  const job = period
    ? preparations.jobs.find(
        (j) => j.periodId === period.id && j.closureVersion === period.version,
      )
    : undefined;

  useEffect(() => {
    if (!focus?.finalsPeriodId) return;
    setPeriodId(focus.finalsPeriodId);
    setBookOverride("");
    setView({ kind: "pupil", studentId: focus.studentId, reveal: true });
  }, [focus]);
  // The classes table follows the statement and every change made on the screen.
  useEffect(() => {
    if (!book) {
      setMatrix(null);
      return;
    }
    const mine = ++generation.current;
    api(`final-grades/books/${book.id}/matrix`, workspace)
      .then((r: Matrix) => {
        if (mine !== generation.current) return;
        setMatrix(r);
        setMatrixError("");
      })
      .catch((e) => {
        if (mine === generation.current)
          setMatrixError(
            e instanceof Error ? e.message : "Не удалось загрузить классы.",
          );
      });
  }, [book?.id, epoch]);
  // A statement that was finished while nobody looked: the lists are read again and the table appears.
  useEffect(() => {
    if (book) return;
    const id = finishedBook(preparations.jobs, periodId, options.books);
    if (!id || reloaded.current === id) return;
    reloaded.current = id;
    void reloadOptions();
  }, [preparations.jobs, periodId, book]);

  const back = () => {
    if (!leave()) return;
    setDetail(null);
    setView({ kind: "home" });
  };
  function pickPeriod(id: string) {
    if (!leave()) return;
    setPeriodId(id);
    setBookOverride("");
    setDetail(null);
    setView({ kind: "home" });
  }
  function pickTab(next: Tab) {
    if (next === tab || !leave()) return;
    setDetail(null);
    setTab(next);
  }
  function openClass(classId: string) {
    if (!leave()) return;
    setView({ kind: "class", classId });
  }
  function openPupil(studentId: string) {
    if (!leave()) return;
    setSearch("");
    setView({ kind: "pupil", studentId, reveal: false });
  }
  // A statement that is ready: the screen switches to it, wherever it was opened from.
  async function openBook(id: string) {
    if (!leave()) return;
    const next = await reloadOptions();
    const opened = next?.books.find((b) => b.id === id);
    if (opened) {
      setPeriodId(opened.period.id);
      setBookOverride(id);
    }
    setSettings(null);
    setDetail(null);
    setView({ kind: "home" });
    setNotice("Ведомость готова.");
  }
  async function prepareNow() {
    if (!period) return;
    const id = await preparations.prepare(period);
    if (id) await openBook(id);
  }
  // The course teachers of the closure get their rows in a few short requests; nothing is assigned twice.
  async function autoAssign() {
    if (!book || !leave()) return;
    setDetail(null);
    await run(async () => {
      let assigned = 0,
        skipped = 0,
        after = "";
      const missing = new Set<string>();
      do {
        const r = await api(
          `final-grades/books/${book.id}/auto-assign`,
          workspace,
          "POST",
          { after },
        );
        assigned += r.assigned;
        skipped += r.skipped;
        for (const n of r.noAccount as string[]) missing.add(n);
        after = r.nextAfter || "";
      } while (after);
      changed();
      setNotice(
        `Назначено учителям уроков: ${assigned}; без ответственного осталось: ${skipped}.${missing.size ? ` Нет единственной учётной записи у учителей: ${[...missing].join(", ")}.` : ""}`,
      );
    });
  }
  const matrixHere =
    matrix && book && matrix.book.id === book.id ? matrix : null;
  const rows = matrixHere ? classRows(matrixHere) : [];
  const matches = searchPupils(options.students, search);
  const pupilName =
    (view.kind === "pupil" &&
      options.students.find((s) => s.id === view.studentId)?.name) ||
    "";
  const classView = view.kind === "class" ? view : null;
  const classRow = classView
    ? rows.find((r) => r.id === classView.classId)
    : undefined;
  const className =
    classRow?.name ||
    options.classes.find((c) => c.id === classView?.classId)?.name ||
    "";

  return (
    <div className="finals-home">
      <div className="finals-toolbar">
        <FinalSelect
          label="Период"
          value={periodId}
          items={options.periods}
          empty="Нет периодов"
          onChange={pickPeriod}
        />
        {!!periodId && (
          <div className="finals-search">
            <label className="field">
              Найти ученика
              <input
                type="search"
                value={search}
                autoComplete="off"
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            {!!matches.length && (
              <ul className="finals-found" aria-label="Найденные ученики">
                {matches.map((s) => (
                  <li key={s.id}>
                    <button type="button" onClick={() => openPupil(s.id)}>
                      {s.name}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="finals-toolbar-end">
          <button onClick={() => setSettings("scales")}>Настройки</button>
          <button disabled={busy} onClick={refresh}>
            Обновить
          </button>
        </div>
      </div>
      {!!search.trim() && !matches.length && (
        <p role="status">Ученик с таким именем не найден.</p>
      )}
      {settings && (
        <FinalGradesSettings
          workspace={workspace}
          year={year}
          options={options}
          categories={categories}
          tab={settings}
          periodId={periodId}
          bookId={book?.id ?? ""}
          preparations={preparations}
          onCategoriesSaved={onCategoriesSaved}
          onScaleSaved={() => void reloadOptions()}
          onReportSaved={() => void reloadOptions()}
          onOpenBook={openBook}
          onPickBook={(id) => {
            const picked = options.books.find((b) => b.id === id);
            if (!picked) return;
            setPeriodId(picked.period.id);
            setBookOverride(id);
            setDetail(null);
            setView({ kind: "home" });
            setSettings(null);
          }}
          leave={leave}
          onClose={() => setSettings(null)}
        />
      )}
      {!book && view.kind === "home" && (
        <NoStatement
          period={period}
          archived={readOnly}
          job={job}
          busy={busy || preparations.busy}
          onPrepare={() => void prepareNow()}
        />
      )}
      {book && !book.current && !readOnly && (
        <p className="error">
          Этот источник устарел: период открыт или закрыт повторно. Для новых
          итогов нужна ведомость нового закрытия. История доступна; ошибочную
          действующую публикацию может отозвать администратор.{" "}
          <button onClick={() => setSettings("preparation")}>
            Подготовка ведомостей
          </button>
        </p>
      )}
      {view.kind === "pupil" && (
        <section aria-label="Табель ученика">
          <div className="finals-class-head">
            <button onClick={back}>← Все классы</button>
            <h3>{pupilName}</h3>
          </div>
          <PupilReportCard
            key={`${view.studentId}/${periodId}`}
            workspace={workspace}
            studentId={view.studentId}
            periodId={periodId}
            name={pupilName}
            reveal={view.reveal}
          />
        </section>
      )}
      {book && classView && (
        <FinalGradesClassView
          key={`${book.id}-${classView.classId}`}
          workspace={workspace}
          year={year}
          options={options}
          categories={categories}
          onCategoriesSaved={onCategoriesSaved}
          lastScale={lastScale}
          epoch={epoch}
          changed={changed}
          leave={leave}
          dirty={dirty}
          onDirty={onDirty}
          reloadOptions={reloadOptions}
          refresh={refresh}
          busy={busy}
          run={run}
          setNotice={setNotice}
          book={book}
          classId={classView.classId}
          className={className}
          classRow={classRow}
          onBack={back}
        />
      )}
      {book && printClass && (
        <ReportPrintDialog
          workspace={workspace}
          periodId={book.period.id}
          classId={printClass}
          className={
            rows.find((r) => r.id === printClass)?.name ||
            options.classes.find((c) => c.id === printClass)?.name ||
            ""
          }
          admin
          bookId={book.id}
          currentBook={book.current}
          initialTile="final"
          onClose={() => setPrintClass("")}
        />
      )}
      {book && view.kind === "home" && (
        <>
          <div className="finals-tabs" role="tablist" aria-label="Разделы">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                id={`finals-tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls="finals-tab-panel"
                onClick={() => pickTab(t.id)}
              >
                {t.title}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id="finals-tab-panel"
            aria-labelledby={`finals-tab-${tab}`}
          >
            {tab === "classes" && (
              <>
                {matrixError && (
                  <p role="alert" className="error">
                    {matrixError}
                  </p>
                )}
                {!matrixHere && !matrixError && <p role="status">Загрузка…</p>}
                {matrixHere && (
                  <ReportClassesTable
                    rows={rows}
                    busy={busy}
                    onOpen={openClass}
                    onPrint={setPrintClass}
                  />
                )}
              </>
            )}
            {tab === "subjects" && (
              <>
                {!readOnly && (
                  <div className="dated-toolbar">
                    <button
                      disabled={busy || !book.current}
                      onClick={() => void autoAssign()}
                    >
                      Назначить по учителям уроков
                    </button>
                  </div>
                )}
                <FinalGradesMatrix
                  key={book.id}
                  workspace={workspace}
                  book={book}
                  admin
                  scales={options.scales}
                  phrases={options.phrases}
                  teachers={options.teachers}
                  lastScale={lastScale}
                  refresh={epoch}
                  tableEpoch={tableEpoch}
                  leave={leave}
                  onDirty={onDirty}
                  onChanged={changed}
                  onOpenRow={(row) => {
                    const unsaved = dirty;
                    if (!leave()) return;
                    // What was typed in the table was given up: it is read again.
                    if (unsaved) setTableEpoch((n) => n + 1);
                    setDetail(row);
                  }}
                />
                {detail && (
                  <FinalGradeRowDetails
                    key={detail.id}
                    workspace={workspace}
                    row={detail}
                    scales={options.scales}
                    teachers={options.teachers}
                    admin
                    current={book.current}
                    readOnly={readOnly}
                    categories={categories}
                    lastScale={lastScale}
                    onDirty={onDirty}
                    onChanged={() => {
                      setTableEpoch((n) => n + 1);
                      changed();
                    }}
                    onClose={() => {
                      if (leave()) setDetail(null);
                    }}
                  />
                )}
              </>
            )}
            {tab === "rows" && (
              <FinalGradeRows
                key={book.id}
                workspace={workspace}
                book={book}
                readOnly={readOnly}
                scales={options.scales}
                teachers={options.teachers}
                categories={categories}
                epoch={epoch}
                lastScale={lastScale}
                leave={leave}
                onDirty={onDirty}
                onChanged={changed}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}

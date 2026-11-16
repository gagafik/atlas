import { useState } from "react";
import FinalGradeRowDetails from "./FinalGradeRowDetails";
import FinalGradesMatrix from "./FinalGradesMatrix";
import FinalSelect from "./FinalSelect";
import type { FinalsShared, Row } from "./finalGradesTypes";
import PupilReportCard from "./PupilReportCard";
import ReportPrintDialog from "./ReportPrintDialog";
import { bookFor, teacherPeriods } from "./reportCardsView";
import "./FinalGrades.css";

// «Табели» of a teacher: the own courses of the period as a compact table; a class teacher also reads the published cards of the own class.
export default function TeacherFinals({
  workspace,
  year,
  options,
  categories,
  lastScale,
  epoch,
  changed,
  leave,
  dirty,
  onDirty,
  refresh,
  busy,
  focus,
}: FinalsShared & {
  focus?: { studentId: string; finalsPeriodId?: string };
}) {
  const readOnly = !!year;
  const periods = teacherPeriods(options.books, options.periods);
  const [periodId, setPeriodId] = useState(
    () =>
      periods.find((p) => options.books.some((b) => b.period.id === p.id))
        ?.id ||
      periods[0]?.id ||
      "",
  );
  const [bookId, setBookId] = useState("");
  const [detail, setDetail] = useState<Row | null>(null);
  const [tableEpoch, setTableEpoch] = useState(0);
  // The class whose published cards are open in the print dialog.
  const [printClass, setPrintClass] = useState("");
  const own = options.books.filter((b) => b.period.id === periodId);
  const found = bookFor(periodId, options.books, bookId);
  const book = found && readOnly ? { ...found, current: false } : found;
  const pickPeriod = (id: string) => {
    if (!leave()) return;
    setPeriodId(id);
    setBookId("");
    setDetail(null);
  };
  return (
    <div className="finals-home">
      <div className="finals-toolbar">
        <FinalSelect
          label="Период"
          value={periodId}
          items={periods}
          empty="Нет периодов"
          onChange={pickPeriod}
        />
        {own.length > 1 && (
          <FinalSelect
            label="Ведомость"
            value={book?.id ?? ""}
            items={own.map((b) => ({
              id: b.id,
              name: `закрытие ${b.closureVersion}${b.current ? "" : " · архив"}`,
            }))}
            onChange={(id) => {
              if (!id || !leave()) return;
              setBookId(id);
              setDetail(null);
            }}
          />
        )}
        <div className="finals-toolbar-end">
          {book && (
            <a
              href={`/api/final-grades/books/${book.id}/export?${new URLSearchParams({ workspace, classId: "", subjectId: "", status: "all" })}`}
              download
            >
              CSV моих итогов
            </a>
          )}
          <button disabled={busy} onClick={refresh}>
            Обновить
          </button>
        </div>
      </div>
      {!book && <p>Вам пока не назначены строки итоговых оценок.</p>}
      {book && !book.current && !readOnly && (
        <p className="error">
          Этот источник устарел: период открыт или закрыт повторно. Для новых
          итогов нужна ведомость нового закрытия. История доступна.
        </p>
      )}
      {book && (
        <>
          <FinalGradesMatrix
            key={book.id}
            workspace={workspace}
            book={book}
            admin={false}
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
              admin={false}
              current={book.current}
              readOnly={readOnly}
              categories={categories}
              lastScale={lastScale}
              leave={leave}
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
      {options.classTeacher && (
        <>
          {options.classes.map((c) => (
            <section key={c.id} className="finals-my-class">
              <h3>Мой класс: {c.name}</h3>
              <button disabled={!periodId} onClick={() => setPrintClass(c.id)}>
                Табели класса
              </button>
            </section>
          ))}
          {focus?.finalsPeriodId && (
            <PupilReportCard
              key={`${focus.studentId}/${focus.finalsPeriodId}`}
              workspace={workspace}
              studentId={focus.studentId}
              periodId={focus.finalsPeriodId}
              name={
                options.students.find((x) => x.id === focus.studentId)?.name ??
                ""
              }
              reveal
            />
          )}
          {printClass && periodId && (
            <ReportPrintDialog
              workspace={workspace}
              periodId={periodId}
              classId={printClass}
              className={
                options.classes.find((c) => c.id === printClass)?.name ?? ""
              }
              admin={false}
              bookId=""
              currentBook={false}
              initialTile="final"
              onClose={() => setPrintClass("")}
            />
          )}
        </>
      )}
    </div>
  );
}

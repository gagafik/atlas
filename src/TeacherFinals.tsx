import { useState } from "react";
import FinalGradeRowDetails from "./FinalGradeRowDetails";
import FinalGradesMatrix from "./FinalGradesMatrix";
import FinalSelect from "./FinalSelect";
import type { FinalsShared, Row } from "./finalGradesTypes";
import ReportCardChooser from "./ReportCardChooser";
import ReportCardSteps from "./ReportCardSteps";
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
  reloadOptions,
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
  const [classId, setClassId] = useState("");
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
              <button
                aria-pressed={classId === c.id}
                disabled={!periodId}
                onClick={() => {
                  if (leave()) setClassId(classId === c.id ? "" : c.id);
                }}
              >
                Табели класса
              </button>
              {classId === c.id && periodId && (
                <ReportCardSteps
                  key={`${periodId}-${c.id}`}
                  workspace={workspace}
                  options={options}
                  lastScale={lastScale}
                  refresh={epoch}
                  leave={leave}
                  onDirty={onDirty}
                  periodId={periodId}
                  classId={c.id}
                  onChanged={() => {
                    void reloadOptions();
                    changed();
                  }}
                />
              )}
            </section>
          ))}
          <ReportCardChooser
            workspace={workspace}
            options={options}
            focus={focus}
            title="Табели учеников вашего класса"
          />
        </>
      )}
    </div>
  );
}

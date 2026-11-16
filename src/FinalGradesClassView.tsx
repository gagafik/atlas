import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import ClassGrid from "./ClassGrid";
import FinalGradeRowDetails from "./FinalGradeRowDetails";
import FinalGradesTable from "./FinalGradesTable";
import { chunked } from "./finalGrades";
import type {
  Book,
  FinalsShared,
  ReportLayout,
  Row,
  Sheet,
} from "./finalGradesTypes";
import ReportLayoutDialog from "./ReportLayoutDialog";
import ReportPrintDialog from "./ReportPrintDialog";
import { stateText, type ClassRow } from "./reportCardsView";
import "./FinalGrades.css";

type Dialog = { kind: "print"; pupilId: string } | { kind: "layout" } | null;

// One class of the statement for the administrator: the status, the grid of pupils and subjects and what can be done with it.
export default function FinalGradesClassView({
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
  busy,
  run,
  setNotice,
  book,
  previewable,
  classId,
  className,
  classRow,
  onBack,
}: FinalsShared & {
  // The statement of the period; not current for an older closure and for an archived year.
  book: Book;
  // The statement is of the last closure of its period, even if the school year is archived: the preliminary cards can be built.
  previewable: boolean;
  classId: string;
  className: string;
  // The counters of the class from the classes table; they follow every change.
  classRow?: ClassRow;
  onBack: () => void;
}) {
  const readOnly = !!year;
  const [rows, setRows] = useState<Row[] | null>(null),
    [layout, setLayout] = useState<ReportLayout | null>(null),
    [error, setError] = useState(""),
    [subjectId, setSubjectId] = useState(""),
    [detail, setDetail] = useState<Row | null>(null),
    // Raised to read the open subject table again after unsaved results were given up.
    [tableKey, setTableKey] = useState(0),
    [dialog, setDialog] = useState<Dialog>(null);
  const grid = useRef<HTMLDivElement>(null),
    generation = useRef(0);

  // The grid pages through the same listing as the subject table; the layout gives the order of the columns.
  async function load() {
    const mine = ++generation.current;
    try {
      const all: Row[] = [];
      let offset = 0;
      for (;;) {
        const page: Sheet = await api(
          `final-grades/books/${book.id}?${new URLSearchParams({ classId, offset: String(offset) })}`,
          workspace,
        );
        all.push(...page.items);
        if (page.nextOffset === null) break;
        offset = page.nextOffset;
      }
      const saved: ReportLayout = await api(
        `final-grades/report-layouts/${book.period.id}/${classId}`,
        workspace,
      );
      if (mine !== generation.current) return;
      setRows(all);
      setLayout(saved);
      setError("");
    } catch (e) {
      if (mine === generation.current)
        setError(
          e instanceof Error
            ? e.message
            : "Не удалось загрузить ведомость класса.",
        );
    }
  }
  useEffect(() => {
    void load();
    return () => {
      generation.current++;
    };
  }, [book.id, classId, epoch]);
  useEffect(() => {
    setRows(null);
    setLayout(null);
    setSubjectId("");
    setDetail(null);
  }, [book.id, classId]);

  const subjectName =
    layout?.subjects.find((s) => s.id === subjectId)?.name ||
    rows?.find((r) => r.subjectId === subjectId)?.source.subject ||
    "";
  const approved = classRow?.approved ?? 0;
  const open = (next: () => void) => {
    if (leave()) next();
  };
  function openSubject(id: string) {
    if (id === subjectId && !detail) return;
    open(() => {
      setDetail(null);
      setSubjectId(id);
    });
  }
  function openCell(row: Row) {
    open(() => {
      setSubjectId("");
      setDetail(row);
    });
  }
  // From a pupil name in the subject table: the table stays, what was typed in it is given up and read again.
  function openRowFromTable(row: Row) {
    const unsaved = dirty;
    if (!leave()) return;
    if (unsaved) setTableKey((n) => n + 1);
    setDetail(row);
  }
  const closeDetail = () => open(() => setDetail(null));
  const closeTable = () =>
    open(() => {
      setSubjectId("");
      setDetail(null);
    });

  // Publishes what the administrator approved in this class, in parts so a long class is never one request.
  function publish() {
    void run(async () => {
      const h = await api(
        `final-grades/books/${book.id}/handles?${new URLSearchParams({ classId, status: "approved" })}`,
        workspace,
      );
      const list = h.items.map((x: { id: string; version: number }) => ({
        id: x.id,
        version: x.version,
      }));
      if (!list.length)
        return setNotice("Нет утверждённых итогов для публикации.");
      if (
        !window.confirm(
          `Опубликовать утверждённые итоги класса ${className} (${list.length}) ученикам и родителям? Семья получит уведомление.`,
        )
      )
        return;
      let done = 0;
      try {
        for (const part of chunked<{ id: string; version: number }>(
          list,
          100,
        )) {
          await api("final-grades/actions", workspace, "POST", {
            action: "publish",
            rows: part,
            teacherId: "",
            reason: "",
          });
          done += part.length;
        }
      } catch (e) {
        changed();
        throw new Error(
          `Опубликовано ${done} из ${list.length}; остальные строки не изменены. ${e instanceof Error ? e.message : ""}`,
        );
      }
      changed();
      setNotice(`Опубликовано итогов: ${list.length}.`);
    });
  }

  return (
    <section className="finals-class" aria-label={`Класс ${className}`}>
      <div className="finals-class-head">
        <button onClick={() => onBack()}>← Все классы</button>
        <h3>
          {className} · {book.period.name}
        </h3>
      </div>
      {classRow && (
        <p className="finals-status-line">
          {book.period.start}–{book.period.end} · учеников:{" "}
          <strong>{classRow.pupils}</strong> · итоги:{" "}
          <strong>
            {classRow.state.done} из {classRow.total}
          </strong>{" "}
          · утверждено:{" "}
          <strong>{classRow.approved + classRow.published}</strong> ·
          опубликовано: <strong>{classRow.published}</strong> ·{" "}
          {stateText(classRow.state)}
        </p>
      )}
      {classRow && classRow.draft + classRow.submitted > 0 && (
        <p className="error">
          Не все итоги класса утверждены: черновиков {classRow.draft}, на
          утверждении {classRow.submitted}.
        </p>
      )}
      <div className="finals-toolbar">
        <button onClick={() => setDialog({ kind: "print", pupilId: "" })}>
          Печать / экспорт
        </button>
        {!readOnly && (
          <button
            disabled={busy || !book.current || !approved}
            onClick={publish}
          >
            Опубликовать семье{approved ? ` (${approved})` : ""}
          </button>
        )}
        <button onClick={() => setDialog({ kind: "layout" })}>
          Предметы в табеле
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!rows && !error && <p role="status">Загрузка…</p>}
      {rows && layout && (
        <ClassGrid
          ref={grid}
          rows={rows}
          layout={layout}
          className={className}
          periodName={book.period.name}
          subjectId={subjectId}
          onSubject={openSubject}
          onCell={openCell}
          onPupil={(id) => setDialog({ kind: "print", pupilId: id })}
        />
      )}
      {subjectId && (
        <section className="finals-detail" aria-label="Таблица предмета">
          <div className="finals-details-head">
            <h3>
              {className} · {subjectName}
            </h3>
            <button onClick={closeTable}>Закрыть таблицу</button>
          </div>
          <FinalGradesTable
            key={`${book.id}-${classId}-${subjectId}-${tableKey}`}
            workspace={workspace}
            bookId={book.id}
            classId={classId}
            subjectId={subjectId}
            scales={options.scales}
            phrases={options.phrases}
            current={book.current}
            lastScale={lastScale}
            onDirty={onDirty}
            onChanged={changed}
            onOpenRow={openRowFromTable}
          />
        </section>
      )}
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
          leave={leave}
          onDirty={onDirty}
          onChanged={() => {
            setTableKey((n) => n + 1);
            changed();
          }}
          onClose={closeDetail}
        />
      )}
      {dialog?.kind === "print" && (
        <ReportPrintDialog
          workspace={workspace}
          periodId={book.period.id}
          classId={classId}
          className={className}
          admin
          bookId={book.id}
          currentBook={previewable}
          // The grid can be printed only once it is on screen.
          gridRef={rows?.length && layout ? grid : undefined}
          initialTile={dialog.pupilId ? "pupil" : undefined}
          initialPupilId={dialog.pupilId}
          initialPupilName={
            rows?.find((r) => r.studentId === dialog.pupilId)?.source.student
              .name
          }
          // A class is checked before it is published, so the pupil opens as the preview, not as an empty published card.
          initialPreliminary={!!dialog.pupilId}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === "layout" && (
        <ReportLayoutDialog
          workspace={workspace}
          periodId={book.period.id}
          classId={classId}
          className={className}
          readOnly={readOnly}
          onSaved={() => void load()}
          onClose={() => setDialog(null)}
        />
      )}
    </section>
  );
}

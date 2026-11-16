import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { api } from "./api";
import { downloadCsv } from "./downloadCsv";
import type { ClassReport } from "./finalGradesTypes";
import FinalSelect from "./FinalSelect";
import { CLASS_PRINT, GRID_PRINT, printNode } from "./printReport";
import ReportCards from "./ReportCards";
import { cardCsvRows } from "./reportCardsView";

export type PrintTile = "final" | "preview" | "pupil" | "grid" | "csv";

type Tile = { id: PrintTile; title: string; about: string };
const TILES: Tile[] = [
  {
    id: "final",
    title: "Табели класса",
    about:
      "Итоговые табели, по одному на странице. Только опубликованные итоги.",
  },
  {
    id: "preview",
    title: "Предварительные табели",
    about:
      "Утверждённые и опубликованные итоги этого закрытия, с пометкой «Предварительно».",
  },
  {
    id: "pupil",
    title: "Табель одного ученика",
    about: "Выберите ученика: табель можно напечатать или сохранить в CSV.",
  },
  {
    id: "grid",
    title: "Ведомость класса",
    about:
      "Таблица класса целиком: ученики и предметы. Печатается на одной странице.",
  },
  {
    id: "csv",
    title: "CSV ведомости",
    about: "Все строки класса одним файлом для таблицы.",
  },
];

/** Which tiles the dialog offers: the published ones always, the rest only where the viewer may use them. */
export function availableTiles(o: {
  admin: boolean;
  currentBook: boolean;
  grid: boolean;
  bookId: string;
}): PrintTile[] {
  const ids: PrintTile[] = ["final"];
  if (o.admin && o.currentBook && o.bookId) ids.push("preview");
  ids.push("pupil");
  if (o.grid) ids.push("grid");
  if (o.admin && o.bookId) ids.push("csv");
  return ids;
}

const printable = (r: ClassReport | null | undefined) =>
  !!r?.pupils.some((p) => p.items.length);

// «Печать / экспорт»: every report of one class as a tile; a tile shows its cards in the dialog and prints them with one click.
export default function ReportPrintDialog({
  workspace,
  periodId,
  classId,
  className,
  admin,
  bookId,
  currentBook,
  gridRef,
  initialTile,
  initialPupilId = "",
  onClose,
}: {
  workspace: string;
  periodId: string;
  classId: string;
  className: string;
  // The administrator also sees the preliminary cards, the CSV and, with a grid, the ledger; everyone else the published cards only.
  admin: boolean;
  bookId: string;
  // False for an older closure and an archived year: no preliminary cards are built from it.
  currentBook: boolean;
  // The class grid on screen, when the dialog is opened from the class view.
  gridRef?: RefObject<HTMLDivElement | null>;
  initialTile?: PrintTile;
  initialPupilId?: string;
  onClose: () => void;
}) {
  const ids = availableTiles({
    admin,
    currentBook,
    grid: !!gridRef,
    bookId,
  });
  const [tile, setTile] = useState<PrintTile | null>(
    initialTile && ids.includes(initialTile) ? initialTile : null,
  );
  const [pupilId, setPupilId] = useState(initialPupilId);
  const [preliminary, setPreliminary] = useState(false);
  const [reports, setReports] = useState<{
    final?: ClassReport;
    preview?: ClassReport;
  }>({});
  const [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null),
    cards = useRef<HTMLDivElement>(null),
    asked = useRef(new Set<string>());
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);

  // The report a tile needs; asked once per kind while the dialog is open.
  const need: "final" | "preview" | null =
    tile === "final"
      ? "final"
      : tile === "preview"
        ? "preview"
        : tile === "pupil"
          ? preliminary && admin && currentBook
            ? "preview"
            : "final"
          : null;
  useEffect(() => {
    if (!need || asked.current.has(need)) return;
    asked.current.add(need);
    setLoading(true);
    setError("");
    const query = new URLSearchParams({ periodId, classId });
    api(
      `final-grades/class-report?${query}${need === "preview" ? "&preview=true" : ""}`,
      workspace,
    )
      .then((r: ClassReport) => setReports((all) => ({ ...all, [need]: r })))
      .catch((e) => {
        asked.current.delete(need);
        setError(e instanceof Error ? e.message : "Не удалось собрать табели.");
      })
      .finally(() => setLoading(false));
  }, [need, periodId, classId, workspace]);

  const report = need ? reports[need] : undefined;
  const shown = useMemo(() => {
    if (!report) return null;
    if (tile !== "pupil") return report;
    return { ...report, pupils: report.pupils.filter((p) => p.id === pupilId) };
  }, [report, tile, pupilId]);
  const pupil = report?.pupils.find((p) => p.id === pupilId);

  function print(title: string) {
    if (!printNode(cards.current, title, CLASS_PRINT))
      setError("Разрешите открытие окна для печати.");
  }
  function printGrid() {
    if (
      !printNode(gridRef?.current ?? null, `Ведомость ${className}`, GRID_PRINT)
    )
      setError("Разрешите открытие окна для печати.");
  }
  const exportLink = `/api/final-grades/books/${bookId}/export?${new URLSearchParams({ workspace, classId, subjectId: "", status: "all" })}`;

  return (
    <dialog
      className="finals-dialog"
      ref={dialog}
      aria-labelledby="finals-print-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2 id="finals-print-title">Печать и экспорт · {className}</h2>
        <button onClick={onClose}>Закрыть</button>
      </header>
      <div className="finals-tiles" role="group" aria-label="Что напечатать">
        {TILES.filter((t) => ids.includes(t.id)).map((t) => (
          <button
            key={t.id}
            type="button"
            className="finals-tile"
            aria-pressed={tile === t.id}
            onClick={() => {
              setTile(t.id);
              setError("");
            }}
          >
            <strong>{t.title}</strong>
            <span>{t.about}</span>
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading && <p role="status">Собираем табели…</p>}
      {(tile === "final" || tile === "preview") && report && (
        <section aria-label="Табели класса">
          {!printable(report) && (
            <p>
              {tile === "preview"
                ? "Утверждённых итогов пока нет."
                : "Опубликованных итогов пока нет."}
            </p>
          )}
          <button
            disabled={!printable(report)}
            onClick={() =>
              print(
                tile === "preview"
                  ? `Предварительный табель ${report.className}`
                  : `Табели ${report.className}`,
              )
            }
          >
            Печать
          </button>
          <ReportCards report={report} ref={cards} />
        </section>
      )}
      {tile === "pupil" && (
        <section aria-label="Табель одного ученика">
          <div className="finals-toolbar">
            <FinalSelect
              label="Ученик"
              value={pupilId}
              items={(report?.pupils ?? []).map((p) => ({
                id: p.id,
                name: p.name,
              }))}
              onChange={setPupilId}
            />
            {admin && currentBook && (
              <label>
                <input
                  type="checkbox"
                  checked={preliminary}
                  onChange={(e) => setPreliminary(e.target.checked)}
                />{" "}
                Предварительно
              </label>
            )}
          </div>
          {shown && pupil && (
            <>
              <div className="finals-toolbar">
                <button
                  disabled={!printable(shown)}
                  onClick={() => print(`Табель ${pupil.name}`)}
                >
                  Печать
                </button>
                <button
                  disabled={!pupil.items.length}
                  onClick={() =>
                    downloadCsv(cardCsvRows(pupil.items), "Табель.csv")
                  }
                >
                  Скачать табель CSV
                </button>
              </div>
              <ReportCards report={shown} ref={cards} />
            </>
          )}
        </section>
      )}
      {tile === "grid" && gridRef && (
        <section aria-label="Ведомость класса">
          <p>
            Печатается таблица так, как она на экране: ученики, предметы, итоги
            и состояние каждой оценки.
          </p>
          <button onClick={printGrid}>Печать ведомости</button>
        </section>
      )}
      {tile === "csv" && admin && bookId && (
        <section aria-label="CSV ведомости">
          <p>Файл содержит строки класса {className}.</p>
          <a href={exportLink} download>
            Скачать CSV ведомости
          </a>
        </section>
      )}
    </dialog>
  );
}

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { downloadCsv } from "./downloadCsv";
import { finalLabel } from "./finalGrades";
import type { Gpa, Measure, Period, Published } from "./finalGradesTypes";
import { PERSONAL_PRINT, printNode } from "./printReport";
import { ReportExtras } from "./ReportCards";
import { cardCsvRows, dayText, periodDates } from "./reportCardsView";

type Card = {
  period: Period;
  items: Published[];
  gpa: Gpa | null;
  measures: Measure[];
};

// The published report card of one pupil for one period: it loads itself, an answer that is not the latest asked is dropped.
export default function PupilReportCard({
  workspace,
  studentId,
  periodId,
  name,
  reveal = false,
  refresh = 0,
}: {
  workspace: string;
  studentId: string;
  periodId: string;
  // The pupil's name for the heading; the card itself does not carry it.
  name: string;
  // Bring the card into view and focus it once it has loaded (a notification opened it).
  reveal?: boolean;
  // Raised to read the card again, for instance after the screen was refreshed.
  refresh?: number;
}) {
  const [card, setCard] = useState<Card | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const sequence = useRef(0),
    node = useRef<HTMLDivElement>(null),
    revealed = useRef(false);
  useEffect(() => {
    const asked = ++sequence.current;
    setCard(null);
    setError("");
    if (!studentId || !periodId) return setLoading(false);
    setLoading(true);
    api(
      `final-grades/report?${new URLSearchParams({ studentId, periodId })}`,
      workspace,
    )
      .then((r: Card) => {
        if (asked === sequence.current) setCard(r);
      })
      .catch((e) => {
        if (asked === sequence.current)
          setError(
            e instanceof Error ? e.message : "Не удалось загрузить табель.",
          );
      })
      .finally(() => {
        if (asked === sequence.current) setLoading(false);
      });
    return () => {
      sequence.current++;
    };
  }, [workspace, studentId, periodId, refresh]);
  // The card sits below the page header, so one opened from a notification would otherwise load out of sight.
  useEffect(() => {
    if (!card || !reveal || revealed.current) return;
    revealed.current = true;
    node.current?.focus({ preventScroll: true });
    node.current?.scrollIntoView({ block: "start" });
  }, [card, reveal]);
  // The comment column appears only when some subject has something to say in it.
  const notes = !!card?.items.some((r) => r.comment || !r.current);
  return (
    <section className="pupil-report">
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading && <p role="status">Загрузка…</p>}
      {card && (
        <>
          <div className="dated-toolbar">
            <button
              disabled={!card.items.length}
              onClick={() => {
                if (!printNode(node.current, "Табель", PERSONAL_PRINT))
                  setError("Разрешите открытие окна для печати.");
              }}
            >
              Печать
            </button>
            <button
              disabled={!card.items.length}
              onClick={() => downloadCsv(cardCsvRows(card.items), "Табель.csv")}
            >
              Скачать табель CSV
            </button>
          </div>
          <div
            className="final-report"
            aria-label="Табель ученика"
            tabIndex={-1}
            ref={node}
          >
            <h3>Табель · {card.period.name}</h3>
            <p>
              {name} · {periodDates(card.period)}
            </p>
            {!card.items.length && <p>Опубликованных итогов пока нет.</p>}
            {!!card.items.length && (
              <table className="final-card-table">
                <thead>
                  <tr>
                    <th scope="col">Предмет</th>
                    <th scope="col">Итог</th>
                    {notes && <th scope="col">Комментарий</th>}
                    <th scope="col" className="final-card-date">
                      Опубликовано
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {card.items.map((r) => (
                    <tr key={r.subjectId}>
                      <th scope="row">{r.subject}</th>
                      <td>
                        <b>{finalLabel(r)}</b>
                      </td>
                      {notes && (
                        <td>
                          {r.comment}
                          {!r.current && (
                            <span className="final-stale">
                              Последний опубликованный итог. Период открыт для
                              исправлений или уже закрыт повторно; нового итога
                              пока нет.
                            </span>
                          )}
                        </td>
                      )}
                      <td
                        className="final-card-date"
                        title={`Шкала: ${r.scale.name}`}
                      >
                        {dayText(
                          new Date(r.publishedAt).toLocaleDateString("sv-SE"),
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <ReportExtras gpa={card.gpa} measures={card.measures} />
          </div>
          <p className="final-card-note">
            Показаны только опубликованные итоги. Пустой табель не означает
            нулевые оценки.
          </p>
        </>
      )}
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { finalLabel } from "./finalGrades";
import type { Gpa, Measure, Period, Published } from "./finalGradesTypes";
import { PERSONAL_PRINT, printNode } from "./printReport";
import { ReportExtras } from "./ReportCards";
import { cardCsvRows } from "./reportCardsView";
import { csvCell } from "./utils";

type Card = {
  period: Period;
  items: Published[];
  gpa: Gpa | null;
  measures: Measure[];
};

function download(rows: unknown[][], filename: string) {
  const blob = new Blob(
    [
      "﻿" +
        rows
          .map((r) => r.map((v) => csvCell(String(v ?? ""))).join(";"))
          .join("\r\n"),
    ],
    { type: "text/csv;charset=utf-8" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// The published report card of one pupil for one period: it loads itself, an answer that is not the latest asked is dropped.
export default function PupilReportCard({
  workspace,
  studentId,
  periodId,
  name,
  reveal = false,
}: {
  workspace: string;
  studentId: string;
  periodId: string;
  // The pupil's name for the heading; the card itself does not carry it.
  name: string;
  // Bring the card into view and focus it once it has loaded (a notification opened it).
  reveal?: boolean;
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
  }, [workspace, studentId, periodId]);
  // The card sits below the page header, so one opened from a notification would otherwise load out of sight.
  useEffect(() => {
    if (!card || !reveal || revealed.current) return;
    revealed.current = true;
    node.current?.focus({ preventScroll: true });
    node.current?.scrollIntoView({ block: "start" });
  }, [card, reveal]);
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
          <div
            className="final-report"
            aria-label="Табель ученика"
            tabIndex={-1}
            ref={node}
          >
            <h3>Табель · {card.period.name}</h3>
            <p>
              {name} · {card.period.start}–{card.period.end}
            </p>
            {!card.items.length && <p>Опубликованных итогов пока нет.</p>}
            {card.items.map((r) => (
              <article key={r.subjectId}>
                <h4>
                  {r.subject} — {finalLabel(r)}
                </h4>
                <p>{r.comment}</p>
                <p>
                  Шкала: {r.scale.name}. Опубликовано:{" "}
                  {new Date(r.publishedAt).toLocaleString("ru-RU")}.
                </p>
                {!r.current && (
                  <p className="error">
                    Последний опубликованный итог. Период открыт для исправлений
                    или уже закрыт повторно; нового итога пока нет.
                  </p>
                )}
              </article>
            ))}
            <ReportExtras gpa={card.gpa} measures={card.measures} />
          </div>
          <p>
            Показаны только опубликованные итоги. Пустой табель не означает
            нулевые оценки.
          </p>
          <div className="dated-toolbar">
            <button
              disabled={!card.items.length}
              onClick={() => download(cardCsvRows(card.items), "Табель.csv")}
            >
              Скачать табель CSV
            </button>
            <button
              disabled={!card.items.length}
              onClick={() => {
                if (!printNode(node.current, "Табель", PERSONAL_PRINT))
                  setError("Разрешите открытие окна для печати.");
              }}
            >
              Печать
            </button>
          </div>
        </>
      )}
    </section>
  );
}

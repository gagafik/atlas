import { forwardRef } from "react";
import { finalLabel } from "./finalGrades";
import type { ClassReport, Gpa, Measure } from "./finalGradesTypes";

export const gpaText = (gpa: Gpa) => gpa.value.replace(".", ",");
const dayText = (day: string) => new Date(day).toLocaleDateString("ru-RU");

// The average and the educational measures marked for the card; no percentages, explanations or authors.
export function ReportExtras({
  gpa,
  measures,
}: {
  gpa: Gpa | null;
  measures: Measure[];
}) {
  return (
    <>
      {gpa && (
        <p className="final-gpa">
          Средний балл: <b>{gpaText(gpa)}</b>
        </p>
      )}
      {!!measures.length && (
        <section className="final-measures">
          <h4>Поведение и меры</h4>
          {measures.map((m, i) => (
            <p key={i}>
              {m.kindTitle}: {m.category} · {dayText(m.day)}
              {m.letterNumber && ` · письмо № ${m.letterNumber}`}
            </p>
          ))}
        </section>
      )}
    </>
  );
}

// One card per pupil of the class, ready for the printer. A preview carries a visible mark on every card.
const ReportCards = forwardRef<HTMLDivElement, { report: ClassReport }>(
  function ReportCards({ report }, ref) {
    return (
      <div className="final-cards" ref={ref}>
        {report.pupils.map((pupil) => (
          <article className="final-card" key={pupil.id}>
            <p>{report.school}</p>
            <h3>
              Табель · {report.period.name}
              {report.preview && (
                <span className="final-preview"> · Предварительно</span>
              )}
            </h3>
            <p>
              {pupil.name} · {report.className} · {report.period.start}–
              {report.period.end}
            </p>
            {!pupil.items.length && <p>Опубликованных итогов пока нет.</p>}
            {pupil.items.map((item) => (
              <div className="final-subject" key={item.subjectId}>
                <h4>{item.subject}</h4>
                <p>
                  <b>{finalLabel(item)}</b>
                  {item.comment && ` · ${item.comment}`}
                  {item.preview && (
                    <span className="final-preview"> · Предварительно</span>
                  )}
                </p>
              </div>
            ))}
            <ReportExtras gpa={pupil.gpa} measures={pupil.measures} />
            <div className="final-signatures">
              <span>Классный руководитель</span>
              <span>Директор</span>
              <span>Родитель</span>
            </div>
          </article>
        ))}
      </div>
    );
  },
);
export default ReportCards;

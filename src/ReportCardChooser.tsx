import { useEffect, useState } from "react";
import FinalSelect from "./FinalSelect";
import type { Options } from "./finalGradesTypes";
import PupilReportCard from "./PupilReportCard";

// The personal published card for families and class teachers: choose the pupil (a family has one pinned) and the period.
// The screen of the family is rebuilt on top of PupilReportCard; until then the choice stays as it was.
export default function ReportCardChooser({
  workspace,
  options,
  studentId: chosen,
  focus,
  title,
}: {
  workspace: string;
  options: Options;
  // The pupil of the page, when it already knows the child.
  studentId?: string;
  // Set by an opened notification: show the report card of this pupil for this period.
  focus?: { studentId: string; finalsPeriodId?: string };
  title: string;
}) {
  const [studentId, setStudentId] = useState(chosen ?? ""),
    [periodId, setPeriodId] = useState(""),
    // The card is shown for the pupil and period that were asked for with the button, not for the selects as they are now.
    [asked, setAsked] = useState<{
      studentId: string;
      periodId: string;
      reveal: boolean;
    } | null>(null);
  useEffect(() => {
    if (!chosen) return;
    setStudentId(chosen);
    setAsked(null);
  }, [chosen]);
  useEffect(() => {
    const period = focus?.finalsPeriodId;
    if (!focus || !period) return;
    setStudentId(focus.studentId);
    setPeriodId(period);
    setAsked({ studentId: focus.studentId, periodId: period, reveal: true });
  }, [focus]);
  const pinned = chosen
    ? options.students.find((s) => s.id === chosen)
    : undefined;
  const shown = asked && options.students.find((s) => s.id === asked.studentId);
  return (
    <section className="report-card-chooser">
      <h3>{title}</h3>
      <div className="dated-toolbar">
        {pinned ? (
          <p className="info-line">
            Ученик: <strong>{pinned.name}</strong>
          </p>
        ) : (
          <FinalSelect
            label="Ученик для табеля"
            value={studentId}
            onChange={(id) => {
              setStudentId(id);
              setAsked(null);
            }}
            items={options.students}
          />
        )}
        <FinalSelect
          label="Период табеля"
          value={periodId}
          onChange={(id) => {
            setPeriodId(id);
            setAsked(null);
          }}
          items={options.periods}
        />
        <button
          disabled={!studentId || !periodId}
          onClick={() => setAsked({ studentId, periodId, reveal: false })}
        >
          Показать табель
        </button>
      </div>
      {asked && (
        <PupilReportCard
          key={`${asked.studentId}/${asked.periodId}`}
          workspace={workspace}
          studentId={asked.studentId}
          periodId={asked.periodId}
          name={shown?.name ?? ""}
          reveal={asked.reveal}
        />
      )}
    </section>
  );
}

import { useEffect, useState } from "react";
import FinalSelect from "./FinalSelect";
import type { Options } from "./finalGradesTypes";
import PupilReportCard from "./PupilReportCard";
import "./FinalGrades.css";

// «Табели» of a pupil or a parent: the published report card of the child opens by itself for the newest period, the period can be switched.
export default function FamilyFinals({
  workspace,
  options,
  studentId,
  focus,
}: {
  workspace: string;
  options: Options;
  // The child chosen on the page; a pupil has only the own card.
  studentId?: string;
  // Set by an opened notification: show the report card of this pupil for this period.
  focus?: { studentId: string; finalsPeriodId?: string };
}) {
  const [periodId, setPeriodId] = useState(
    () => focus?.finalsPeriodId || options.periods[0]?.id || "",
  );
  useEffect(() => {
    if (focus?.finalsPeriodId) setPeriodId(focus.finalsPeriodId);
  }, [focus]);
  const pupilId =
    studentId || focus?.studentId || options.students[0]?.id || "";
  const name = options.students.find((s) => s.id === pupilId)?.name ?? "";
  if (!options.periods.length) return <p>Опубликованных табелей пока нет.</p>;
  return (
    <section className="finals-family" aria-label="Табель ученика">
      <div className="finals-toolbar">
        <FinalSelect
          label="Период"
          value={periodId}
          items={options.periods}
          onChange={(id) => id && setPeriodId(id)}
        />
      </div>
      {pupilId && periodId && (
        <PupilReportCard
          key={`${pupilId}/${periodId}`}
          workspace={workspace}
          studentId={pupilId}
          periodId={periodId}
          name={name}
          reveal={
            !!focus?.finalsPeriodId &&
            focus.studentId === pupilId &&
            focus.finalsPeriodId === periodId
          }
        />
      )}
    </section>
  );
}

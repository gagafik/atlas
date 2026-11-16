import { useEffect, useState } from "react";
import FinalSelect from "./FinalSelect";
import type { Options } from "./finalGradesTypes";
import PupilReportCard from "./PupilReportCard";
import { childPeriods, familyPeriod } from "./reportCardsView";
import "./FinalGrades.css";

// «Табели» of a pupil or a parent: the published report card of the child opens by itself for the newest period of that child, the period can be switched.
export default function FamilyFinals({
  workspace,
  options,
  studentId,
  focus,
  epoch = 0,
  busy = false,
  onRefresh,
}: {
  workspace: string;
  options: Options;
  // The child chosen on the page; a pupil has only the own card.
  studentId?: string;
  // Set by an opened notification: show the report card of this pupil for this period.
  focus?: { studentId: string; finalsPeriodId?: string };
  // Raised after the lists were read again, so the card is read again too.
  epoch?: number;
  busy?: boolean;
  // Reads the lists of periods and the card again.
  onRefresh?: () => void;
}) {
  const pupilId =
    studentId || focus?.studentId || options.students[0]?.id || "";
  const name = options.students.find((s) => s.id === pupilId)?.name ?? "";
  // The period chosen by hand or by a notification, with the child it was chosen for: another child starts at the own newest period.
  const [picked, setPicked] = useState<{
    pupilId: string;
    periodId: string;
  } | null>(() =>
    focus?.finalsPeriodId
      ? { pupilId: focus.studentId, periodId: focus.finalsPeriodId }
      : null,
  );
  // The parent page builds a new `focus` object on every render, so only its content decides whether a notification was opened.
  const focusPupil = focus?.studentId ?? "",
    focusPeriod = focus?.finalsPeriodId ?? "";
  useEffect(() => {
    if (focusPeriod) setPicked({ pupilId: focusPupil, periodId: focusPeriod });
  }, [focusPupil, focusPeriod]);
  const available = childPeriods(
    options.periods,
    options.studentPeriods,
    pupilId,
  );
  const periodId = familyPeriod(available, picked, pupilId);
  return (
    <section className="finals-family" aria-label="Табель ученика">
      <div className="finals-toolbar">
        {!!available.length && (
          <FinalSelect
            label="Период"
            value={periodId}
            items={available}
            onChange={(id) => id && setPicked({ pupilId, periodId: id })}
          />
        )}
        {onRefresh && (
          <div className="finals-toolbar-end">
            <button disabled={busy} onClick={onRefresh}>
              Обновить
            </button>
          </div>
        )}
      </div>
      {!available.length && <p>Опубликованных табелей пока нет.</p>}
      {pupilId && periodId && (
        <PupilReportCard
          key={`${pupilId}/${periodId}`}
          workspace={workspace}
          studentId={pupilId}
          periodId={periodId}
          name={name}
          refresh={epoch}
          reveal={
            !!focusPeriod && focusPupil === pupilId && focusPeriod === periodId
          }
        />
      )}
    </section>
  );
}

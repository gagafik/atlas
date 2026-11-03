import { useEffect, useMemo, useState } from "react";
import FinalGradesPanel from "./FinalGradesPanel";
import { ChildPicker } from "./JournalPickers";
import type { JournalFocus } from "./JournalPanel";
import type { JournalOptions, JournalView } from "./journal";
import PupilAttendance from "./PupilAttendance";
import PupilDay from "./PupilDay";
import PupilGrades from "./PupilGrades";
import {
  defaultChoice,
  periodChoices,
  recallChild,
  recallPeriod,
  rememberChild,
  rememberPeriod,
  type PeriodSelection,
} from "./pupilJournal";
import "./PupilJournal.css";

// The browser's day, kept inside the school year, for a server that does not tell its own day.
function schoolToday(options: JournalOptions) {
  const day = options.today ?? new Date().toLocaleDateString("sv-SE");
  return day < options.yearStart
    ? options.yearStart
    : day > options.yearEnd
      ? options.yearEnd
      : day;
}

// The journal of a pupil or a parent: one child and one period for the four screens of the menu.
export default function FamilyJournal({
  workspace,
  options,
  view,
  focus,
}: {
  workspace: string;
  options: JournalOptions;
  view?: JournalView;
  focus?: JournalFocus;
}) {
  const { students } = options;
  const known = (id?: string) =>
    id && students.some((s) => s.id === id) ? id : undefined;
  const [child, setChild] = useState(
    () =>
      known(focus?.studentId) ??
      known(recallChild(workspace)) ??
      students[0]?.id ??
      "",
  );
  const pick = (id: string) => {
    setChild(id);
    rememberChild(workspace, id);
  };
  // A notification opened while the journal is on screen points at another child.
  useEffect(() => {
    const id = known(focus?.studentId);
    if (id) pick(id);
  }, [focus]);
  const today = schoolToday(options);
  const choices = useMemo(
    () =>
      periodChoices(options.periods ?? [], options.yearStart, options.yearEnd),
    [options],
  );
  const [period, setPeriod] = useState<PeriodSelection>(() => {
    const remembered = recallPeriod(workspace);
    if (remembered) return remembered;
    const { id, start, end } = defaultChoice(choices, today);
    return { id, start, end };
  });
  const choose = (value: PeriodSelection) => {
    setPeriod(value);
    rememberPeriod(workspace, value);
  };
  if (!students.length)
    return <p className="empty-state">Ученики для журнала не назначены.</p>;
  const screen =
    view === "pupilGrades" ? (
      <PupilGrades
        key={child}
        workspace={workspace}
        child={child}
        choices={choices}
        period={period}
        onPeriod={choose}
      />
    ) : view === "pupilAttendance" || view === "absences" ? (
      <PupilAttendance
        key={child}
        workspace={workspace}
        options={options}
        child={child}
        choices={choices}
        period={period}
        onPeriod={choose}
        showNotes={focus?.panel === "absences" || view === "absences"}
      />
    ) : view === "finalGrades" ? (
      <FinalGradesPanel
        workspace={workspace}
        defaultOpen
        studentId={child}
        focus={
          focus?.finalsPeriodId && focus.studentId
            ? {
                studentId: focus.studentId,
                finalsPeriodId: focus.finalsPeriodId,
              }
            : undefined
        }
      />
    ) : (
      <PupilDay
        workspace={workspace}
        options={options}
        child={child}
        today={today}
        focus={focus}
      />
    );
  return (
    <div className="family-journal">
      <ChildPicker students={students} value={child} onChange={pick} />
      {screen}
    </div>
  );
}

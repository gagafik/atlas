import { useEffect, useMemo, useState } from "react";
import FinalGradesPanel from "./FinalGradesPanel";
import { ChildPicker } from "./JournalPickers";
import type { JournalFocus } from "./JournalPanel";
import type { JournalOptions, JournalView } from "./journal";
import PupilAttendance from "./PupilAttendance";
import PupilDay from "./PupilDay";
import PupilGrades from "./PupilGrades";
import {
  dayText,
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
// A family looks at the child the portal has chosen (`chosen`, empty until the portal knows its children); the staff reading
// a pupil's report pick the pupil here.
export default function FamilyJournal({
  workspace,
  options,
  chosen,
  view,
  focus,
  family = true,
  year,
}: {
  workspace: string;
  // The archived school year on show; undefined for the current one.
  year?: string;
  options: JournalOptions;
  chosen?: string;
  view?: JournalView;
  focus?: JournalFocus;
  // False for the staff reading a pupil's report: the same screens over the records the account may read, without what only a family does.
  family?: boolean;
}) {
  const { students } = options;
  const known = (id?: string) =>
    id && students.some((s) => s.id === id) ? id : undefined;
  const [picked, setPicked] = useState(
    () =>
      known(focus?.studentId) ??
      known(recallChild(workspace)) ??
      students[0]?.id ??
      "",
  );
  const pick = (id: string) => {
    setPicked(id);
    rememberChild(workspace, id);
  };
  // A notification opened while the report is on screen points at another pupil.
  useEffect(() => {
    const id = known(focus?.studentId);
    if (id && chosen === undefined) pick(id);
  }, [focus]);
  const child = known(chosen) ?? picked;
  const today = schoolToday(options);
  const choices = useMemo(
    () =>
      periodChoices(options.periods ?? [], options.yearStart, options.yearEnd),
    [options],
  );
  const [period, setPeriod] = useState<PeriodSelection>(() => {
    const remembered = recallPeriod(workspace);
    // A period remembered from another year never asks for days outside the year on show.
    if (
      remembered &&
      remembered.start >= options.yearStart &&
      remembered.end <= options.yearEnd
    )
      return remembered;
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
        family={family}
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
        family={family}
      />
    ) : view === "finalGrades" ? (
      <FinalGradesPanel
        workspace={workspace}
        year={year}
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
        family={family}
      />
    );
  // The pickers do not print, so the sheet names the pupil and, for grades and attendance, the period.
  const pupil = students.find((s) => s.id === child);
  const ranged =
    view === "pupilGrades" || view === "pupilAttendance" || view === "absences";
  return (
    <div className="family-journal">
      <p className="pupil-print-head">
        {pupil?.name}
        {pupil?.className ? `, ${pupil.className}` : ""}
        {ranged &&
          ` · ${choices.find((c) => c.id === period.id)?.name ?? "Период"}: ${dayText(period.start)} – ${dayText(period.end)}`}
      </p>
      {chosen === undefined && (
        <ChildPicker
          students={students}
          value={child}
          onChange={pick}
          label={family ? "Ребёнок" : "Ученик"}
        />
      )}
      {screen}
    </div>
  );
}

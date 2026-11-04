import { useEffect, useState } from "react";
import { api } from "./api";
import { gridWindow } from "./journalCoverage";

const NONE = new Set<string>();

// Whose lessons colour the calendar: the teacher's own, or one class of the school for the administrator.
export type CoverageScope =
  { mode: "teaching" } | { mode: "classes"; className: string };

/**
 * The days on the calendar of one month (its whole weeks) that hold a lesson still «не заполнено», by the rule of the reminders and the class summary.
 * The server answers with the dates alone, outside the coverage report's snapshot and audit trail.
 * Nothing is coloured (and nothing is said) for an account without the coverage report, for «Все классы», or for a month outside the year.
 */
export function useCoverageDays(
  workspace: string,
  scope: CoverageScope | null,
  month: string,
  year: { start: string; end: string },
  // Changes whenever the journal was saved.
  epoch: number,
): Set<string> {
  const name = scope?.mode === "classes" ? scope.className : "";
  const key = `${workspace}|${scope?.mode ?? ""}|${name}|${month}|${year.start}|${year.end}|${epoch}`;
  const [found, setFound] = useState({ key: "", days: NONE });
  useEffect(() => {
    const window = month ? gridWindow(month, year.start, year.end) : null;
    if (!scope || !window || (scope.mode === "classes" && !scope.className)) {
      setFound({ key, days: NONE });
      return;
    }
    let live = true;
    (async () => {
      let classId = "";
      if (scope.mode === "classes") {
        const options = await api("journal/coverage/options", workspace);
        classId =
          (options.classes?.classes ?? []).find(
            (c: { name: string }) => c.name === scope.className,
          )?.id ?? "";
        if (!classId) return [];
      }
      const r = await api("journal/coverage/days", workspace, "POST", {
        ...window,
        mode: scope.mode,
        classId,
      });
      return r.days as string[];
    })()
      .then((days) => live && setFound({ key, days: new Set(days) }))
      .catch(() => live && setFound({ key, days: NONE }));
    return () => {
      live = false;
    };
  }, [key]);
  return found.key === key ? found.days : NONE;
}

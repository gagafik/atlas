// The school years of a workspace: the list for the switcher in the top bar and the year the screens read.
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";

export interface SchoolYear {
  id: string;
  name: string;
  start: string;
  end: string;
  current: boolean;
  archived: boolean;
}
interface YearList {
  current: string;
  years: SchoolYear[];
}

/** The years of the workspace. The choice is not remembered: each load opens the current year, as EduPage does by date. */
export function useSchoolYears(workspace: string) {
  const [list, setList] = useState<YearList | null>(null);
  const [chosen, setChosen] = useState("");
  const reload = useCallback(async () => {
    try {
      const r = await api("school-years", workspace);
      setList({ current: r.current, years: r.years });
    } catch {
      // Without the list the shell keeps the old label.
      setList(null);
    }
  }, [workspace]);
  useEffect(() => {
    let live = true;
    setList(null);
    api("school-years", workspace)
      .then((r) => live && setList({ current: r.current, years: r.years }))
      .catch(() => live && setList(null));
    return () => {
      live = false;
    };
  }, [workspace]);
  const current = list?.current ?? "";
  useEffect(() => setChosen(""), [workspace, current]);
  const years = list?.years ?? [];
  const selected = years.some((y) => y.id === chosen) ? chosen : current;
  const archived = !!selected && selected !== current;
  return useMemo(
    () => ({
      years,
      current,
      selected,
      select: setChosen,
      selectedYear: years.find((y) => y.id === selected),
      archived,
      // What the year-scoped requests carry: nothing for the current year, so they stay what they always were.
      archivedYear: archived ? selected : undefined,
      reload,
      // The prop of the shell; absent while the list is not known.
      shell: years.length
        ? { items: years, selected, onSelect: setChosen }
        : undefined,
    }),
    [list, chosen, reload],
  );
}

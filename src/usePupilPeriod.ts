import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { PupilPeriod } from "./pupilJournal";

// The last answers, so «Оценки» and «Посещаемость» of the same child and period share one request.
const FRESH_MS = 20_000;
const KEPT = 4;
const cache = new Map<string, { at: number; data: PupilPeriod }>();
const keyOf = (
  workspace: string,
  student: string,
  start: string,
  end: string,
) => `${workspace}|${student}|${start}|${end}`;
function remember(key: string, data: PupilPeriod) {
  cache.delete(key);
  cache.set(key, { at: Date.now(), data });
  while (cache.size > KEPT) cache.delete(cache.keys().next().value!);
}

/** Marks and attendance of one pupil for a period (journal/pupil-period). `reload` asks the server again. */
export function usePupilPeriod(
  workspace: string,
  studentId: string,
  start: string,
  end: string,
) {
  const key = keyOf(workspace, studentId, start, end);
  const [state, setState] = useState<{
    key: string;
    data: PupilPeriod | null;
    error: string;
  }>({ key, data: null, error: "" });
  const [epoch, setEpoch] = useState(0);
  const [again, setAgain] = useState(false);
  useEffect(() => {
    if (!studentId || !start || !end) return;
    const kept = cache.get(key);
    if (kept && !again && Date.now() - kept.at < FRESH_MS) {
      setState({ key, data: kept.data, error: "" });
      return;
    }
    let live = true;
    // The previous answer of this selection stays on screen while the new one loads.
    setState((old) => ({
      key,
      data: kept?.data ?? (old.key === key ? old.data : null),
      error: "",
    }));
    api(
      `journal/pupil-period?${new URLSearchParams({ studentId, start, end })}`,
      workspace,
    )
      .then((data: PupilPeriod) => {
        remember(key, data);
        if (live) setState({ key, data, error: "" });
      })
      .catch((e: Error) => {
        if (live) setState({ key, data: null, error: e.message });
      })
      .finally(() => {
        if (live) setAgain(false);
      });
    return () => {
      live = false;
    };
  }, [key, epoch]);
  const reload = useCallback(() => {
    setAgain(true);
    setEpoch((n) => n + 1);
  }, []);
  const current = state.key === key;
  return {
    data: current ? state.data : null,
    error: current ? state.error : "",
    loading: !current || (!state.data && !state.error),
    reload,
  };
}

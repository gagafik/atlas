import { useCallback, useEffect, useRef, useState } from "react";
import { markDirty as mark } from "./finalGrades";

const ROW = "row";

/** What the user is asked before unsaved results are dropped; empty when nothing is unsaved. A table outranks a single row. */
export function unsavedMessage(
  keys: readonly string[],
  // A place whose own typing is being saved right now: it does not count.
  except?: string,
): string {
  const open = except ? keys.filter((k) => k !== except) : keys;
  if (!open.length) return "";
  return open.some((k) => k !== ROW)
    ? "В таблице есть несохранённые итоги. Отменить эти изменения?"
    : "В строке есть несохранённый итог. Отменить эти изменения?";
}

/**
 * The places that hold typed, unsaved results register under a key (every table has its own, the row details use «row»).
 * `leave()` asks once before the screen drops them; the browser asks before the page is left.
 * `leave("row")` leaves the row's own typing out, for the row that is being saved itself.
 */
export function useUnsavedGuard() {
  const [keys, setKeys] = useState<readonly string[]>([]);
  const current = useRef(keys);
  current.current = keys;
  const markDirty = useCallback(
    (key: string, unsaved: boolean) =>
      setKeys((list) => mark(list, key, unsaved)),
    [],
  );
  const leave = useCallback((except?: string) => {
    const message = unsavedMessage(current.current, except);
    return !message || window.confirm(message);
  }, []);
  const dirty = keys.length > 0;
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  return { dirty, markDirty, leave };
}

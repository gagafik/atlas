import { useCallback, useEffect, useRef, useState } from "react";
import { markDirty as mark } from "./finalGrades";

const ROW = "row";

/** What the user is asked before unsaved results are dropped; empty when nothing is unsaved. A table outranks a single row. */
export function unsavedMessage(keys: readonly string[]): string {
  if (!keys.length) return "";
  return keys.some((k) => k !== ROW)
    ? "В таблице есть несохранённые итоги. Отменить эти изменения?"
    : "В строке есть несохранённый итог. Отменить эти изменения?";
}

/**
 * The places that hold typed, unsaved results register under a key (every table has its own, the row details use «row»).
 * `leave()` asks once before the screen drops them; the browser asks before the page is left.
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
  const leave = useCallback(() => {
    const message = unsavedMessage(current.current);
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

import { useRef, useState } from "react";

// One request at a time with a visible busy state, an error and a notice; shared by the report-card panels.
export function useBusy() {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const lock = useRef(false);
  async function run(work: () => Promise<void>) {
    if (lock.current) return false;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
      return true;
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить действие.",
      );
      return false;
    } finally {
      setBusy(false);
      lock.current = false;
    }
  }
  return { busy, error, notice, setError, setNotice, run };
}

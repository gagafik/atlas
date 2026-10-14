/** One request at a time; pause idle tabs and spread retries across clients. */
export function startPolling(
  task: () => Promise<boolean | void>,
  options: {
    interval: number | (() => number);
    background?: () => boolean;
    paused?: () => boolean;
    maxDelay?: number;
  },
) {
  let stopped = false,
    pending = false,
    requested = false,
    failures = 0,
    lastStarted = -Infinity;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const available = () =>
    navigator.onLine !== false &&
    (document.visibilityState !== "hidden" || options.background?.() === true);
  function clear() {
    clearTimeout(timer);
    timer = undefined;
  }
  function schedule() {
    clear();
    if (stopped || !available() || options.paused?.()) return;
    const base =
      typeof options.interval === "function"
        ? options.interval()
        : options.interval;
    const delay = Math.min(
      options.maxDelay ?? 120000,
      Math.max(1000, base) * 2 ** failures,
    );
    timer = setTimeout(
      () => void run(false),
      delay * (0.9 + Math.random() * 0.2),
    );
  }
  async function run(force: boolean) {
    if (stopped) return;
    clear();
    if (!available() || (!force && options.paused?.())) return;
    if (pending) {
      requested ||= force;
      return;
    }
    if (!force && Date.now() - lastStarted < 1000) {
      schedule();
      return;
    }
    pending = true;
    lastStarted = Date.now();
    try {
      failures = (await task()) === false ? Math.min(3, failures + 1) : 0;
    } catch {
      failures = Math.min(3, failures + 1);
    } finally {
      pending = false;
      if (requested && !stopped) {
        requested = false;
        void run(true);
      } else schedule();
    }
  }
  const wake = () => void run(false);
  const visibility = () => {
    if (available()) wake();
    else clear();
  };
  window.addEventListener("focus", wake);
  window.addEventListener("online", wake);
  window.addEventListener("offline", clear);
  document.addEventListener("visibilitychange", visibility);
  void run(true);
  return {
    refresh: () => void run(true),
    stop: () => {
      stopped = true;
      clear();
      window.removeEventListener("focus", wake);
      window.removeEventListener("online", wake);
      window.removeEventListener("offline", clear);
      document.removeEventListener("visibilitychange", visibility);
    },
  };
}

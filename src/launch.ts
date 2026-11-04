// What an action of the launcher asks of the screen it opens beyond showing it: the editor to start with.
// The launcher states the intent after the menu has opened the screen; the screen takes it once it is on show.
import { useEffect, useRef } from "react";

export type LaunchIntent =
  "compose" | "newWork" | "measure" | "todayLesson" | "homework" | "newTest";

const EVENT = "atlas-launch";
// A screen that does not start with the intent leaves it waiting only this long, so a later visit does not find an old request.
const FRESH_MS = 5000;
let waiting: { intent: LaunchIntent; at: number } | null = null;

/** Ask the screen that is opening (or already open) to start with `intent`. */
export function requestLaunch(intent: LaunchIntent) {
  waiting = { intent, at: Date.now() };
  window.dispatchEvent(new Event(EVENT));
}
/** Forget a request nobody took: the next screen is chosen by hand. */
export function clearLaunch() {
  waiting = null;
}

/** A screen that can start with some intents: `handle` runs once when one of them is asked for, at once if it was asked before the screen mounted. */
export function useLaunch(
  accepts: readonly LaunchIntent[],
  handle: (intent: LaunchIntent) => void,
) {
  const latest = useRef(handle);
  latest.current = handle;
  useEffect(() => {
    const take = () => {
      if (!waiting || !accepts.includes(waiting.intent)) return;
      const { intent, at } = waiting;
      waiting = null;
      if (Date.now() - at <= FRESH_MS) latest.current(intent);
    };
    take();
    window.addEventListener(EVENT, take);
    return () => window.removeEventListener(EVENT, take);
  }, []);
}

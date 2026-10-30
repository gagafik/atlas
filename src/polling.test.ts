import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { startPolling } from "./polling";

let browser: EventTarget, page: EventTarget & { visibilityState: string };
let connection: { onLine: boolean };
const running: ReturnType<typeof startPolling>[] = [];
function poll(
  task: () => Promise<boolean | void>,
  options: Parameters<typeof startPolling>[1],
) {
  const value = startPolling(task, options);
  running.push(value);
  return value;
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  browser = new EventTarget();
  page = Object.assign(new EventTarget(), { visibilityState: "visible" });
  connection = { onLine: true };
  vi.stubGlobal("window", browser);
  vi.stubGlobal("document", page);
  vi.stubGlobal("navigator", connection);
});
afterEach(() => {
  running.splice(0).forEach((p) => p.stop());
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("pauses hidden/offline tabs and refreshes when they return", async () => {
  const task = vi.fn(async () => true);
  poll(task, { interval: 25000 });
  await vi.advanceTimersByTimeAsync(1000);
  page.visibilityState = "hidden";
  page.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(120000);
  expect(task).toHaveBeenCalledTimes(1);
  page.visibilityState = "visible";
  page.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(1000);
  expect(task).toHaveBeenCalledTimes(2);
  connection.onLine = false;
  browser.dispatchEvent(new Event("offline"));
  await vi.advanceTimersByTimeAsync(120000);
  expect(task).toHaveBeenCalledTimes(2);
  connection.onLine = true;
  browser.dispatchEvent(new Event("online"));
  await vi.advanceTimersByTimeAsync(0);
  expect(task).toHaveBeenCalledTimes(3);
});

it("preserves opted-in background notifications and does not overwrite an open inbox", async () => {
  page.visibilityState = "hidden";
  let paused = true;
  const task = vi.fn(async () => true);
  poll(task, { interval: 25000, background: () => true, paused: () => paused });
  await vi.advanceTimersByTimeAsync(50000);
  expect(task).toHaveBeenCalledTimes(1);
  paused = false;
  browser.dispatchEvent(new Event("focus"));
  await vi.advanceTimersByTimeAsync(25000);
  expect(task).toHaveBeenCalledTimes(3);
});

it("backs off errors and returns to the normal cadence after success", async () => {
  // A refused answer (false) and a thrown error both count as a failure.
  const outcomes: (boolean | Error)[] = [
    true,
    false,
    new Error("offline"),
    false,
    false,
    true,
  ];
  const times: number[] = [];
  const maxDelay = 50000;
  poll(
    async () => {
      times.push(Date.now());
      const outcome = outcomes.shift() ?? true;
      if (outcome instanceof Error) throw outcome;
      return outcome;
    },
    { interval: 10000, maxDelay },
  );
  await vi.advanceTimersByTimeAsync(10 * maxDelay);
  // gaps[i] is how long the poller waited after the i-th poll.
  const gaps = times.slice(1, 7).map((time, i) => time - times[i]);
  expect(gaps[1]).toBeGreaterThan(gaps[0]);
  expect(gaps[2]).toBeGreaterThan(gaps[1]);
  for (const gap of gaps.slice(1, 5)) expect(gap).toBeLessThanOrEqual(maxDelay);
  expect(gaps[5]).toBe(gaps[0]);
});

it("coalesces forced refreshes, never overlaps requests, and stops after disposal", async () => {
  let resolve: (v: boolean) => void = () => {};
  const task = vi.fn(
    () =>
      new Promise<boolean>((r) => {
        resolve = r;
      }),
  );
  const controller = poll(task, { interval: 1000 });
  await vi.advanceTimersByTimeAsync(60000);
  controller.refresh();
  controller.refresh();
  controller.refresh();
  expect(task).toHaveBeenCalledTimes(1);
  resolve(true);
  await vi.advanceTimersByTimeAsync(0);
  expect(task).toHaveBeenCalledTimes(2);
  controller.stop();
  resolve(true);
  browser.dispatchEvent(new Event("focus"));
  await vi.advanceTimersByTimeAsync(120000);
  expect(task).toHaveBeenCalledTimes(2);
});

// Time between the first two polls: the delay a client picks for its next poll.
async function firstGap(interval: number | (() => number)) {
  const times: number[] = [];
  const controller = poll(
    async () => {
      times.push(Date.now());
      return true;
    },
    { interval },
  );
  await vi.advanceTimersByTimeAsync(2 * 60000);
  controller.stop();
  return times[1] - times[0];
}

it("reads the interval again before every poll", async () => {
  let active = false;
  const times: number[] = [];
  const controller = poll(
    async () => {
      times.push(Date.now());
      return true;
    },
    { interval: () => (active ? 1500 : 30000) },
  );
  await vi.advanceTimersByTimeAsync(0);
  active = true;
  controller.refresh();
  await vi.advanceTimersByTimeAsync(5000);
  expect(times.length).toBeGreaterThanOrEqual(3);
  const gap = times[2] - times[1];
  expect(gap).toBeGreaterThan(1500 / 2);
  expect(gap).toBeLessThan(1500 * 1.5);
});

it("spreads clients so they do not poll at the same moment", async () => {
  vi.mocked(Math.random).mockReturnValue(0);
  const early = await firstGap(30000);
  vi.mocked(Math.random).mockReturnValue(0.999);
  const late = await firstGap(30000);
  expect(early).not.toBe(late);
  for (const gap of [early, late]) {
    expect(gap).toBeGreaterThan(30000 / 2);
    expect(gap).toBeLessThan(30000 * 1.5);
  }
});

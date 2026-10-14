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
  const task = vi
    .fn<() => Promise<boolean>>()
    .mockResolvedValueOnce(false)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue(true);
  poll(task, { interval: 10000 });
  await vi.advanceTimersByTimeAsync(19999);
  expect(task).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(task).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(40000);
  expect(task).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(10000);
  expect(task).toHaveBeenCalledTimes(4);
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

it("uses changing intervals and spreads clients with jitter", async () => {
  let active = false;
  const task = vi.fn(async () => true);
  vi.mocked(Math.random).mockReturnValue(0);
  const controller = poll(task, { interval: () => (active ? 1500 : 30000) });
  await vi.advanceTimersByTimeAsync(26999);
  expect(task).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(task).toHaveBeenCalledTimes(2);
  active = true;
  controller.refresh();
  await vi.advanceTimersByTimeAsync(1350);
  expect(task).toHaveBeenCalledTimes(4);
});

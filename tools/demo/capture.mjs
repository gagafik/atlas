// Capture the demo snapshot from the real backend (tools/demo/frozen_server.py).
//
//   node tools/demo/capture.mjs --workdir DIR --url http://127.0.0.1:8765 [--roles admin,teacher,parent,student]
//
// For every role a browser signs in, walks every menu section and the explicit
// journeys, and records each GET response (and the few POSTs that only read).
// Every other write is refused here, so the walk cannot change the database.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launch, newContext, guardPage, trackActivity, waitQuiet, signIn, VIEWPORTS } from "./browser-kit.mjs";
import { createKit, journeys } from "./journeys.mjs";
import { getKey, postKey } from "./request-key.mjs";
import { READ_POSTS, SILENT, denyStub, isApiPath, isDenied, isSilent, readPostRule } from "./read-requests.mjs";
import { createReplayer, daysBetween, fanOut, isoShift } from "./fanout.mjs";
import { scrubSnapshot, serializeSnapshot } from "./scrub.mjs";
import { walkRole } from "./walk.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEMO_ERROR = "Это демо: изменения не сохраняются.";
const SWEEP_FIRST = "2026-10-05";
const SWEEP_LAST = "2026-11-13";
// A day of the whole school is large; the week before the demo day and the next ones are enough.
const WINDOWS = [{ pattern: /^\/api\/calendar\/day$/, first: "2026-10-26" }];

function args() {
  const result = { roles: "admin,teacher,parent,student", budget: 500, walk: true, sweep: true, out: resolve(HERE, "snapshot") };
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === "--workdir") result.workdir = argv[++i];
    else if (flag === "--url") result.url = argv[++i];
    else if (flag === "--roles") result.roles = argv[++i];
    else if (flag === "--budget") result.budget = Number(argv[++i]);
    else if (flag === "--out") result.out = resolve(argv[++i]);
    else if (flag === "--no-walk") result.walk = false;
    else if (flag === "--no-sweep") result.sweep = false;
    else if (flag === "--rescrub") result.rescrub = true;
    else if (flag === "--merge") result.merge = true;
    else if (flag === "--viewport") result.viewport = argv[++i];
    else throw new Error(`Unknown option ${flag}`);
  }
  if (!result.workdir || !result.url) throw new Error("Usage: capture.mjs --workdir DIR --url URL");
  return result;
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function createRecorder(base) {
  const entries = new Map();
  const state = { recording: false, loginOpen: true, writesRefused: 0, denied: 0 };
  const record = (key, status, body) => entries.set(key, { s: status, b: body });
  async function handle(route, request) {
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;
    if (process.env.CAPTURE_DEBUG) console.log("route", method, path, state.recording, state.loginOpen);
    const json = (status, body) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/login" && method === "POST" && state.loginOpen) return route.continue();
    const workspace = url.searchParams.get("workspace");
    if (isDenied(path) || (workspace && workspace !== "school")) {
      state.denied += 1;
      const stub = method === "GET" && (!workspace || workspace === "school") ? denyStub(path) : undefined;
      if (stub) {
        if (state.recording) record(getKey(url), 200, stub.body);
        return json(200, stub.body);
      }
      return json(method === "GET" || method === "HEAD" ? 404 : 403, { detail: "demo" });
    }
    if (method === "GET" || method === "HEAD") {
      const response = await route.fetch();
      const status = response.status();
      if (state.recording && status !== 401 && status !== 304 && status < 500 && /json/.test(response.headers()["content-type"] || "")) {
        try {
          record(getKey(url), status, await response.json());
        } catch {
          // an empty body is not worth keeping
        }
      }
      return route.fulfill({ response });
    }
    const rule = method === "POST" ? readPostRule(path) : undefined;
    if (rule) {
      const response = await route.fetch();
      const status = response.status();
      if (state.recording && status < 500 && /json/.test(response.headers()["content-type"] || "")) {
        try {
          let body = await response.json();
          if (rule.pattern.includes("presence") && body && typeof body === "object") body = { ...body, peers: [] };
          record(postKey(url, rule.anyBody ? null : request.postData(), rule.anyBody), status, body);
        } catch {
          // not JSON
        }
      }
      return route.fulfill({ response });
    }
    if (isSilent(method, path)) return json(200, {});
    state.writesRefused += 1;
    return json(403, { detail: DEMO_ERROR });
  }
  return { entries, state, handle, record, base };
}

async function sweep(context, recorder, role, log) {
  const origin = new URL(recorder.base).origin;
  const replayer = createReplayer({ context, origin, entries: recorder.entries, log });
  const { get } = replayer;
  // Every GET with one date (a day, a lesson date) is asked again for each school day of the window.
  const keys = [...recorder.entries.keys()].filter((key) => key.startsWith("GET "));
  let dated = 0;
  for (const key of keys) {
    const url = new URL(key.slice(4), origin);
    // Lessons and homework are opened by key in the fan-out; a day swept over a key is not a view of anything.
    if (/^\/api\/journal\/(entry|homework\/)/.test(url.pathname) || url.searchParams.has("lessonKey")) continue;
    const params = [...url.searchParams.entries()];
    const dateParams = params.filter(([name, value]) => ["day", "onDate", "date"].includes(name) && DATE.test(value));
    if (dateParams.length === 1) {
      const first = WINDOWS.find((window) => window.pattern.test(url.pathname))?.first ?? SWEEP_FIRST;
      for (const day of daysBetween(first, SWEEP_LAST)) {
        const next = new URL(url);
        next.searchParams.set(dateParams[0][0], day);
        await get(next.pathname + next.search);
        dated += 1;
      }
      continue;
    }
    const start = params.find(([name]) => name === "start" || name === "from");
    const end = params.find(([name]) => name === "end" || name === "to");
    if (start && end && DATE.test(start[1]) && DATE.test(end[1]) && (Date.parse(end[1]) - Date.parse(start[1])) / 86400000 <= 7) {
      for (const delta of [-14, -7, 7, 14]) {
        const next = new URL(url);
        next.searchParams.set(start[0], isoShift(start[1], delta));
        next.searchParams.set(end[0], isoShift(end[1], delta));
        await get(next.pathname + next.search);
      }
    }
  }
  log(`  date sweep: ${dated} requests`);
  await fanOut(replayer, role);
  // Homework opened from any queue the walk saw.
  let homework = 0;
  for (const [key, entry] of [...recorder.entries]) {
    if (!key.startsWith("GET /api/journal/homework-queue") || !Array.isArray(entry.b?.items)) continue;
    for (const item of entry.b.items.slice(0, 120)) {
      await get(`/api/journal/homework/${item.lessonKey}?studentId=${item.studentId}`);
      homework += 1;
    }
  }
  log(`  homework opened from queues: ${homework}`);
  log(`  replayed ${replayer.stats.requests} requests, kept ${replayer.stats.stored}`);
}

async function captureRole(browser, options, meta, role) {
  const log = (text) => console.log(text);
  const base = options.url.replace(/\/$/, "") + "/";
  const recorder = createRecorder(base);
  const rawPath = resolve(options.workdir, `raw-${role}.json`);
  if (options.merge && existsSync(rawPath)) {
    // Add to an earlier walk of the same database instead of starting over.
    for (const [key, entry] of Object.entries(JSON.parse(readFileSync(rawPath, "utf8")).entries)) recorder.entries.set(key, entry);
    log(`  [${role}] merged ${recorder.entries.size} earlier entries`);
  }
  const context = await newContext(browser, { now: meta.now, viewport: VIEWPORTS[options.viewport || "desktop"] });
  await context.route((url) => url.origin === new URL(base).origin && isApiPath(url.pathname), async (route, request) => {
    try {
      await recorder.handle(route, request);
    } catch (error) {
      log(`  [${role}] route error ${request.method()} ${request.url()}: ${error.message}`);
      await route.abort().catch(() => {});
    }
  });
  const page = await context.newPage();
  guardPage(page, (text) => log(`  [${role}] ${text}`));
  // Count scripts too: panels load as lazy chunks and ask the API only after that.
  const activity = trackActivity(page, () => true);
  console.log(`== ${role}`);
  await signIn(page, base, meta.roles[role]);
  await waitQuiet(page, activity, 500, 10000, 800);
  recorder.state.loginOpen = false;
  recorder.state.recording = true;
  await page.goto(base);
  await page.waitForSelector(".sidebar nav button", { timeout: 20000 });
  await waitQuiet(page, activity, 500, 10000, 800);
  if (options.walk) await walkRole(page, base, activity, { left: options.budget, seen: new Set() }, log);
  const kit = createKit({ page, base, activity, miss: (text) => log(`  [${role}] journey miss: ${text}`), log });
  try {
    await journeys[role]?.(kit);
  } catch (error) {
    log(`  [${role}] journey stopped: ${String(error.message).split(String.fromCharCode(10))[0]}`);
  }
  if (options.sweep) await sweep(context, recorder, role, log);
  await context.close();
  // The raw capture stays in the temporary workdir (never in the repository) so that a refused scrub costs no new walk.
  const rawSnapshot = { capturedAt: meta.now, readPosts: READ_POSTS, silent: SILENT, entries: Object.fromEntries(recorder.entries) };
  writeFileSync(resolve(options.workdir, `raw-${role}.json`), JSON.stringify(rawSnapshot), "utf8");
  return finish(role, rawSnapshot, options, recorder.state);
}

function finish(role, rawSnapshot, options, state) {
  const snapshot = scrubSnapshot(
    rawSnapshot,
    undefined,
  );
  const text = serializeSnapshot(snapshot);
  mkdirSync(options.out, { recursive: true });
  writeFileSync(resolve(options.out, `${role}.json`), text, "utf8");
  const sizes = Object.entries(snapshot.entries)
    .map(([key, entry]) => [key, JSON.stringify(entry).length])
    .sort((a, b) => b[1] - a[1]);
  console.log(`  ${role}: ${sizes.length} entries, ${(text.length / 1024).toFixed(0)} KiB; refused writes ${state.writesRefused}, denied ${state.denied}`);
  for (const [key, size] of sizes.slice(0, 20)) console.log(`    ${String(size).padStart(8)}  ${key.slice(0, 110)}`);
  return text.length;
}

const options = args();
const meta = JSON.parse(readFileSync(resolve(options.workdir, "meta.json"), "utf8"));
let total = 0;
if (options.rescrub) {
  // Scrub and write the raw captures of an earlier walk again (no browser).
  for (const role of options.roles.split(",")) {
    const raw = JSON.parse(readFileSync(resolve(options.workdir, `raw-${role}.json`), "utf8"));
    total += finish(role, raw, options, { writesRefused: 0, denied: 0 });
  }
} else {
  const browser = await launch();
  try {
    for (const role of options.roles.split(",")) total += await captureRole(browser, options, meta, role);
  } finally {
    await browser.close();
  }
}
console.log(`snapshot total ${(total / 1024 / 1024).toFixed(2)} MiB`);

// Look at the built demo the way a viewer would: every role, at a desktop and a
// phone width, along the explicit journeys.
//
//   npm run build:demo
//   node tools/demo/verify.mjs [--roles admin,teacher] [--widths desktop,phone] [--shots DIR] [--url http://127.0.0.1:4173/atlas/] [--walk]
//
// Without --url it serves dist-demo with `vite preview --mode demo`. Exit code 1
// on a page error, a missed journey step, a snapshot miss inside a journey or a
// tour (every lesson card, course, thread, homework work ...) or a request that
// left the browser for /api. --walk also clicks through every menu section the
// way the capture does and lists the sections whose clicks hit a snapshot miss
// (reported, not a failure: they are the holes to look at).
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launch, newContext, guardPage, trackActivity, closeOverlays, VIEWPORTS } from "./browser-kit.mjs";
import { createKit, journeys, tours } from "./journeys.mjs";
import { walkRole } from "./walk.mjs";
import { isApiPath } from "./read-requests.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const NOW = "2026-11-02T11:30:00+05:00";

function args() {
  const result = { roles: "admin,teacher,parent,student", widths: "desktop,phone", shots: "", url: "", walk: false };
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === "--roles") result.roles = argv[++i];
    else if (flag === "--widths") result.widths = argv[++i];
    else if (flag === "--shots") result.shots = resolve(argv[++i]);
    else if (flag === "--url") result.url = argv[++i];
    else if (flag === "--walk") result.walk = true;
    else throw new Error(`Unknown option ${flag}`);
  }
  return result;
}

function startPreview() {
  const child = spawn(process.execPath, [resolve(ROOT, "node_modules/vite/bin/vite.js"), "preview", "--mode", "demo", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  return new Promise((resolveStart, reject) => {
    const timer = setTimeout(() => reject(new Error("vite preview did not start")), 30000);
    child.stdout.on("data", (chunk) => {
      if (/4173/.test(String(chunk))) {
        clearTimeout(timer);
        resolveStart(child);
      }
    });
    child.on("exit", (code) => reject(new Error(`vite preview exited with ${code}`)));
  });
}

async function verifyRole(browser, base, role, widthName, shots, report, walk) {
  const context = await newContext(browser, { now: NOW, viewport: VIEWPORTS[widthName] });
  const page = await context.newPage();
  const problems = [];
  const notes = [];
  let walking = false;
  guardPage(page, (text) => problems.push(text));
  page.on("console", (message) => {
    const text = message.text();
    if (text.startsWith("[demo] no snapshot") && !walking) problems.push(text);
    else if (text.startsWith("[demo] write refused")) notes.push(text);
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin === new URL(base).origin && isApiPath(url.pathname)) problems.push(`request left the browser: ${request.method()} ${url.pathname}`);
  });
  const activity = trackActivity(page, () => true);
  const miss = (text) => problems.push(`journey miss: ${text}`);
  const label = `${role}/${widthName}`;
  const url = `${base}?as=${role}`;
  await page.goto(url, { waitUntil: "domcontentloaded" });
  const kit = createKit({ page, base: url, activity, miss, log: (text) => notes.push(text), shots: shots && resolve(shots, label.replace("/", "-")) });
  try {
    await page.waitForSelector(".sidebar nav button", { state: "attached", timeout: 20000 });
    await journeys[role](kit);
    await tours[role]?.(kit);
  } catch (error) {
    problems.push(`journey stopped: ${String(error.message).split(String.fromCharCode(10))[0]}`);
  }
  // Controls that cannot work on a static page must not be offered (the account menu and the folded groups of the menu included).
  await closeOverlays(page);
  await page.locator(".account-toggle").click({ timeout: 2000 }).catch((error) => problems.push(`account menu does not open: ${String(error.message).split(String.fromCharCode(10))[0]}`));
  // The demo bar hides what cannot work on the next frame.
  await page.waitForTimeout(400);
  await page.evaluate(() => document.querySelectorAll(".sidebar nav .nav-group-items[hidden]").forEach((group) => group.removeAttribute("hidden")));
  const offered = await page.evaluate(() =>
    [...document.querySelectorAll("button")]
      .filter((button) => button.offsetParent !== null && /^(Приложение|Принять приглашение|Резервные коды|Пароль|Публикация|Информационные экраны|Состояние сервера|Попробовать на примере|Вернуться в свою школу)$/.test((button.textContent || "").trim()))
      .map((button) => button.textContent.trim()),
  );
  for (const name of offered) problems.push(`control that cannot work is offered: ${name}`);
  const holes = [];
  if (walk) {
    walking = true;
    // Every menu section with its first-level buttons; misses are listed per section, not counted as failures.
    let current = [];
    const stamp = (text) => {
      const section = text.match(/^\s+(.+): (?:\d+ actions|stopped)/)?.[1];
      if (section && current.length) holes.push(`${section}: ${[...new Set(current)].slice(0, 6).join("; ")}`);
      if (section) current = [];
    };
    page.on("console", (message) => {
      if (message.text().startsWith("[demo] no snapshot")) current.push(message.text().replace("[demo] no snapshot ", "").replace(/^GET /, ""));
    });
    try {
      await walkRole(page, url, activity, { left: 300, seen: new Set() }, stamp);
    } catch (error) {
      holes.push(`walk stopped: ${String(error.message).split(String.fromCharCode(10))[0]}`);
    }
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  const registrations = await page.evaluate(async () => (await navigator.serviceWorker?.getRegistrations?.())?.length ?? 0);
  await context.close();
  const layout = notes.filter((note) => note.startsWith("layout:"));
  for (const note of layout) problems.length || console.log(`     ${note}`);
  console.log(`${problems.length ? "FAIL" : "ok  "} ${label}: ${problems.length} problem(s), ${notes.filter((n) => n.startsWith("[demo]")).length} refused write(s)${overflow ? ", page scrolls sideways" : ""}${registrations ? `, ${registrations} service worker(s)` : ""}`);
  for (const problem of [...new Set(problems)].slice(0, 25)) console.log(`     ${problem}`);
  for (const hole of holes) console.log(`     walk: ${hole}`);
  report.push({ label, problems, overflow });
  return problems.length === 0;
}

const options = args();
let preview;
const base = options.url || "http://127.0.0.1:4173/atlas/";
if (!options.url) {
  // vite preview sometimes dies at start on Windows (a libuv assertion); a second try is enough.
  for (let attempt = 1; !preview; attempt += 1) {
    try {
      preview = await startPreview();
    } catch (error) {
      if (attempt >= 3) throw error;
    }
  }
}
if (options.shots) mkdirSync(options.shots, { recursive: true });
const browser = await launch();
const report = [];
let allGood = true;
try {
  for (const role of options.roles.split(","))
    for (const width of options.widths.split(","))
      allGood = (await verifyRole(browser, base, role, width, options.shots, report, options.walk)) && allGood;
} finally {
  await browser.close();
  if (preview) preview.kill();
}
const sideways = report.filter((item) => item.overflow).map((item) => item.label);
if (sideways.length) console.log(`page scrolls sideways at: ${sideways.join(", ")}`);
process.exit(allGood ? 0 : 1);

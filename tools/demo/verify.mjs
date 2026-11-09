// Look at the built demo the way a viewer would: every role, at a desktop and a
// phone width, along the explicit journeys.
//
//   npm run build:demo
//   node tools/demo/verify.mjs [--roles admin,teacher] [--widths desktop,phone] [--shots DIR] [--url http://127.0.0.1:4173/atlas/] [--walk]
//
// Without --url it serves dist-demo with `vite preview --mode demo`. Exit code 1
// on a page error, a missed journey step, a snapshot miss inside a journey or a
// tour (every lesson card, course, thread, homework work ...) or a request that
// left the browser for /api. It also checks the shell: the menu (flyouts, the folded rail), the child switcher of a parent
// (the child on the journal, the grades and the start page follows the account menu) and the action launcher of the staff
// (it opens, narrows, closes, and every action it lists reaches its screen). --walk also clicks through every menu section the
// way the capture does and lists the sections whose clicks hit a snapshot miss
// (reported, not a failure: they are the holes to look at).
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launch, newContext, guardPage, trackActivity, closeOverlays, openGroup, waitQuiet, VIEWPORTS } from "./browser-kit.mjs";
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

// How the left menu behaves. A desktop opens a group as a flyout of tiles (one at a time; Esc and a click elsewhere close it;
// the arrow keys move between tiles) and folds to a rail of icons that is remembered across a reload; a phone has neither.
async function menuBehaviour(page, activity, desktop, problems) {
  const fail = (text) => problems.push(`menu: ${text}`);
  const heads = page.locator(".sidebar nav .nav-group > button");
  const flyouts = page.locator(".nav-flyout");
  if (!desktop) {
    if (await page.locator(".nav-flyout, .rail-toggle, .app-shell.rail").count()) fail("a phone offers a flyout or the fold button");
    return;
  }
  const groups = await heads.count();
  if (!groups) return;
  const focusedText = () => page.evaluate(() => (document.activeElement?.textContent || "").replace(/\s+/g, " ").trim());
  await openGroup(page, 0);
  if ((await flyouts.count()) !== 1 || !(await flyouts.first().locator("button").count())) fail("a group does not open a flyout of tiles");
  if (groups > 1) {
    await openGroup(page, 1);
    if ((await flyouts.count()) !== 1) fail("two flyouts are open at once");
    await openGroup(page, 0);
  }
  const first = await focusedText();
  if ((await flyouts.first().locator("button").count()) > 1) {
    await page.keyboard.press("ArrowDown");
    if ((await focusedText()) === first) fail("an arrow key does not move between tiles");
  }
  await page.keyboard.press("Escape");
  if (await flyouts.count()) fail("Esc does not close the flyout");
  if (!(await heads.first().evaluate((head) => head === document.activeElement))) fail("Esc does not return the focus to the group");
  await openGroup(page, 0);
  await page.locator(".breadcrumb strong").click({ timeout: 2000 }).catch(() => {});
  if (await flyouts.count()) fail("a click elsewhere does not close the flyout");
  // A tile of the first group is picked, so that the page on show is inside a group.
  await openGroup(page, 0);
  await flyouts.first().locator("button").first().click({ timeout: 2000 }).catch(() => fail("the first tile cannot be picked"));
  await closeOverlays(page);
  await waitQuiet(page, activity);
  for (let group = 0; group < groups; group += 1) {
    await openGroup(page, group);
    await page.waitForTimeout(300);
    // A tile the demo hides is no stop: End reaches the last tile that shows and a step past it wraps round.
    const shown = await flyouts.first().locator("button").evaluateAll((tiles) => tiles.filter((tile) => tile.getClientRects().length).map((tile) => (tile.textContent || "").replace(/\s+/g, " ").trim()));
    if (shown.length > 2)
      for (const [key, want] of [["End", shown.at(-1)], ["ArrowDown", shown[0]], ["ArrowUp", shown.at(-1)], ["Home", shown[0]]]) {
        await page.keyboard.press(key);
        if ((await focusedText()) !== want) fail(`${key} in the flyout of group ${group + 1} does not reach the ${want === shown[0] ? "first" : "last"} tile that shows`);
      }
    const holdsPage = (await flyouts.first().locator('[aria-current="page"]').count()) > 0;
    await page.keyboard.press("Escape");
    // The group of the page on show says so with its flyout closed, as the entry of a plain menu does.
    if (((await heads.nth(group).getAttribute("aria-current")) === "true") !== holdsPage) fail(`group ${group + 1} does not tell whether it holds the page on show`);
  }
  // The rail: icons with a name and a tooltip, groups still open flyouts, the page gets the room, the choice survives a reload.
  const widthOf = () => page.locator(".sidebar").evaluate((sidebar) => sidebar.getBoundingClientRect().width);
  const expanded = await widthOf();
  await page.locator(".rail-toggle").click({ timeout: 2000 }).catch(() => fail("no button folds the menu"));
  if (!(await page.locator(".app-shell.rail").count()) || (await widthOf()) >= expanded) fail("the menu does not fold to icons");
  const unnamed = await page.evaluate(() => [...document.querySelectorAll(".sidebar nav > button, .sidebar nav .nav-group > button")].filter((button) => !button.title || !(button.textContent || "").trim()).length);
  if (unnamed) fail(`${unnamed} icon(s) of the rail lack a tooltip or a name`);
  await openGroup(page, 0);
  if ((await flyouts.count()) !== 1) fail("a group does not open a flyout from the rail");
  await page.keyboard.press("Escape");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector(".sidebar nav button", { state: "attached", timeout: 20000 });
  await waitQuiet(page, activity);
  if (!(await page.locator(".app-shell.rail").count())) fail("the folded menu is not remembered");
  await page.locator(".rail-toggle").click({ timeout: 2000 }).catch(() => {});
  if (await page.locator(".app-shell.rail").count()) fail("the menu does not unfold again");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector(".sidebar nav button", { state: "attached", timeout: 20000 });
  await waitQuiet(page, activity);
  if (await page.locator(".app-shell.rail").count()) fail("the unfolded menu is not remembered");
}

// The child switcher of a parent: the account menu lists the children and marks the one on show, the bar names it, and a
// choice changes the child on the journal and the grades (and on the start page) with no picker of their own on those screens.
async function childSwitcher(page, kit, problems) {
  const fail = (text) => problems.push(`child switcher: ${text}`);
  const chip = page.locator(".topbar-child");
  const named = async () => ((await chip.count()) ? (await chip.first().innerText()).trim() : "");
  const printed = async () => ((await page.locator(".pupil-print-head").count()) ? (await page.locator(".pupil-print-head").first().textContent()) || "" : "");
  await kit.home();
  await page.locator(".account-toggle").click({ timeout: 3000 });
  const listed = await page.locator(".account-children button").count();
  const current = await page.locator('.account-children button[aria-current="true"]').count();
  if (listed < 2 || current !== 1) fail(`the account menu lists ${listed} children with ${current} marked`);
  await page.keyboard.press("Escape");
  const screens = ["Классный журнал", "Оценки", "Посещаемость", "Табели"];
  const names = [];
  for (let turn = 0; turn < 2; turn += 1) {
    const child = await named();
    names.push(child);
    if (!child) fail("the bar does not name the child");
    for (const screen of screens) {
      if (!(await kit.menu(screen, { optional: screen === "Табели" }))) continue;
      if (!(await printed()).trim().startsWith(child)) fail(`${screen} shows another child than ${child}`);
      if (await page.locator(".pupil-picker:has(select)").filter({ hasText: /Ребёнок|Ученик/ }).count()) fail(`${screen} still offers a picker of the child`);
    }
    // The lists of the family hold the child on show only: the queue of homework and the tests assigned.
    const first = child.split(",")[0];
    await kit.menu("Домашние задания");
    await kit.click("Весь учебный год", { optional: true });
    const works = await page.locator(".homework-queue-card > div > p").allInnerTexts();
    if (!works.length) fail(`the homework list of ${child} is empty`);
    else if (works.some((text) => !text.startsWith(first))) fail(`the homework list shows another child than ${child}`);
    if (await kit.menu("Контрольные и тесты", { optional: true })) {
      const tests = await page.locator(".quiz-card > p:first-of-type").allInnerTexts();
      if (tests.some((text) => !text.includes(first))) fail(`the tests list shows another child than ${child}`);
    }
    // A window of the menu starts on the child on show.
    if (await kit.menu("Забрать из школы", { optional: true })) {
      const chosen = await page.locator("dialog label:has-text('Ребёнок') select").first().evaluate((select) => select.selectedOptions[0]?.textContent || "").catch(() => "");
      if (!chosen.startsWith(first)) fail(`the pick-up window does not start on ${child} (${chosen})`);
      await closeOverlays(page);
    }
    await kit.menu("Начало");
    if (!(await page.locator(".today-lessons h2").first().innerText()).includes(first)) fail(`the start page does not show ${child}`);
    // The choice is made from the page that is on show and keeps the page.
    await kit.menu("Оценки");
    const heading = await page.locator("main h1").first().innerText();
    await kit.otherChild();
    if ((await page.locator("main h1").first().innerText()) !== heading) fail("choosing a child leaves the screen");
  }
  if (names[0] === names[1]) fail("the second choice shows the same child");
}

// The action launcher of the staff: the button opens it with the field in focus, typing narrows it, the arrows and Enter pick,
// Esc closes it and returns the focus, and every action it lists reaches its screen (a page of its own or a window).
async function launcherBehaviour(page, activity, kit, problems, role) {
  const fail = (text) => problems.push(`launcher: ${text}`);
  const button = page.locator(".launcher-button");
  const panel = page.locator(".launcher-panel");
  const options = page.locator(".launcher-panel [role=option]");
  const labels = async () => (await options.filter({ visible: true }).allInnerTexts()).map((text) => text.trim());
  const heading = async () => ((await page.locator("main h1").count()) ? (await page.locator("main h1").first().innerText()).trim() : "");
  await kit.home();
  if (!(await button.count())) return fail("no button in the bar");
  await button.click({ timeout: 3000 });
  if (!(await panel.isVisible())) return fail("the button does not open the panel");
  // The demo hides the entries it cannot answer on the frame after they appear.
  await page.waitForTimeout(300);
  const all = await labels();
  if (!all.length) return fail("the panel lists no action");
  if (!(await page.evaluate(() => document.activeElement?.matches(".launcher-panel input")))) fail("the field does not take the focus");
  const word = all[0].split(/\s+/)[0].toUpperCase();
  await page.keyboard.type(word);
  const narrowed = await labels();
  if (!narrowed.length || narrowed.length > all.length) fail("typing does not narrow the list");
  await page.keyboard.press("Control+A");
  await page.keyboard.type("яяяяя");
  if ((await options.count()) !== 0) fail("a word nothing has still lists actions");
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Backspace");
  const before = await page.locator(".launcher-panel input").getAttribute("aria-activedescendant");
  await page.keyboard.press("ArrowDown");
  if ((await page.locator(".launcher-panel input").getAttribute("aria-activedescendant")) === before) fail("an arrow key does not move the choice");
  await page.keyboard.press("Escape");
  if (await panel.count()) fail("Esc does not close the panel");
  if (!(await button.evaluate((node) => node === document.activeElement))) fail("Esc does not return the focus to the button");
  // Deep in the list the choice stays on screen (the list scrolls inside the panel), so Enter never opens an option nobody sees.
  await button.click({ timeout: 3000 });
  const chosenShown = () =>
    page.evaluate(() => {
      const row = document.getElementById(document.querySelector(".launcher-panel input")?.getAttribute("aria-activedescendant") || "");
      const list = document.querySelector(".launcher-list");
      if (!row || !list) return false;
      const at = row.getBoundingClientRect();
      const box = list.getBoundingClientRect();
      return at.top >= box.top - 1 && at.bottom <= box.bottom + 1;
    });
  for (let step = 1; step <= all.length; step += 1) {
    await page.keyboard.press("ArrowDown");
    if (!(await chosenShown())) {
      fail(`the choice is out of sight after ${step} presses of the arrow`);
      break;
    }
  }
  await page.keyboard.press("Escape");
  // The panel fits the screen at every width, a tablet in portrait included (it hangs under its button only where the menu is open beside it).
  const size = page.viewportSize();
  for (const width of [...new Set([size.width, 768, 820])]) {
    await page.setViewportSize({ width, height: size.height });
    await page.waitForTimeout(150);
    await button.click({ timeout: 3000 });
    const box = await panel.boundingBox();
    if (!box || box.x < -1 || box.x + box.width > width + 1) fail(`the panel does not fit a screen ${width} px wide`);
    await page.keyboard.press("Escape");
  }
  await page.setViewportSize(size);
  await page.waitForTimeout(150);
  // Every action, once, from the start page: the page changes or a window opens, and the panel is gone.
  const home = await heading();
  for (const label of [...new Set(all)]) {
    await kit.home();
    await button.click({ timeout: 3000 });
    const option = panel.getByRole("option", { name: label, exact: true }).first();
    await option.click({ timeout: 3000 }).catch(() => fail(`${label}: cannot be picked`));
    await waitQuiet(page, activity, 400, 6000, 700);
    if (await panel.count()) fail(`${label}: the panel stays open`);
    const reached = (await heading()) !== home || (await page.locator(".modal-backdrop, dialog[open]").count()) > 0;
    if (!reached) fail(`${label}: reaches no screen`);
    await closeOverlays(page);
  }
  // The actions that start an editor: the editor is on screen when the action has run.
  // «Новая оценка / работа» opens the grades of the courses; the editor of the work comes with the course chosen there.
  const pickCourse = () => page.getByRole("button", { name: /7А · Математика/ }).first().click({ timeout: 3000 });
  const editors = [
    ["Новое сообщение", ".message-compose"],
    ["Создать тест", "text=Новый тест"],
    ["Тема урока и посещаемость", ".journal-editor"],
    ["Добавить домашнее задание", ".journal-editor"],
    ["Новая оценка / работа", ".journal-editor", pickCourse],
    ["Воспитательная мера", "dialog[open] >> text=Новая запись"],
    ["Записки об отсутствии", ".absence-panel[open]"],
  ];
  for (const [label, editor, next] of editors) {
    // The administrator keeps no lessons of their own, so the day alone opens for the lesson actions.
    if (!all.includes(label) || (role === "admin" && editor === ".journal-editor")) continue;
    await kit.home();
    await button.click({ timeout: 3000 });
    await page.keyboard.type(label);
    await page.keyboard.press("Enter");
    await waitQuiet(page, activity, 400, 6000, 700);
    if (next) {
      await next().catch(() => fail(`${label}: the course cannot be chosen`));
      await waitQuiet(page, activity, 400, 6000, 700);
    }
    await page.waitForSelector(editor, { timeout: 4000 }).catch(() => fail(`${label}: the editor is not opened`));
    await closeOverlays(page);
  }
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
    if (role === "parent") await childSwitcher(page, kit, problems);
    if (role === "teacher" || role === "admin") await launcherBehaviour(page, activity, kit, problems, role);
  } catch (error) {
    problems.push(`journey stopped: ${String(error.message).split(String.fromCharCode(10))[0]}`);
  }
  await closeOverlays(page);
  const desktop = VIEWPORTS[widthName].width > 900;
  try {
    await menuBehaviour(page, activity, desktop, problems);
  } catch (error) {
    problems.push(`menu check stopped: ${String(error.message).split(String.fromCharCode(10))[0]}`);
  }
  // Controls that cannot work on a static page must not be offered (the account menu and the groups of the menu included).
  await closeOverlays(page);
  await page.locator(".account-toggle").click({ timeout: 2000 }).catch((error) => problems.push(`account menu does not open: ${String(error.message).split(String.fromCharCode(10))[0]}`));
  // The demo bar hides what cannot work on the next frame.
  await page.waitForTimeout(400);
  await page.evaluate(() => document.querySelectorAll(".sidebar nav .nav-group-items[hidden]").forEach((group) => group.removeAttribute("hidden")));
  const offered = new Set();
  const lookForOffered = async () => {
    const names = await page.evaluate(() =>
      [...document.querySelectorAll("button")]
        .filter((button) => button.offsetParent !== null && /^(Приложение|Принять приглашение|Резервные коды|Пароль|Публикация|Информационные экраны|Состояние сервера|Противопожарные учения|Инструктажи по ТБ|Журнал входов|Попробовать на примере|Вернуться в свою школу|Новый учебный год)$/.test((button.textContent || "").trim()))
        .map((button) => button.textContent.trim()),
    );
    for (const name of names) offered.add(name);
  };
  await lookForOffered();
  // On a desktop the tiles of a group exist only while its flyout is open.
  if (desktop) {
    const groups = await page.locator(".sidebar nav .nav-group > button").count();
    for (let group = 0; group < groups; group += 1) {
      await openGroup(page, group);
      await page.waitForTimeout(100);
      await lookForOffered();
    }
  }
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

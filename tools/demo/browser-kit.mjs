// Shared helpers of the capture and verification tools (Playwright).
import { chromium } from "playwright";

export const VIEWPORTS = {
  desktop: { width: 1280, height: 900 },
  phone: { width: 375, height: 812 },
};

export async function launch() {
  return chromium.launch();
}

// A browser context that looks like a school computer in Kazakhstan at the demo instant.
export async function newContext(browser, { now, viewport = VIEWPORTS.desktop, blockWorkers = true }) {
  const context = await browser.newContext({
    viewport,
    timezoneId: "Asia/Qyzylorda",
    locale: "ru-RU",
    serviceWorkers: blockWorkers ? "block" : "allow",
    acceptDownloads: true,
  });
  await context.clock.setFixedTime(now);
  return context;
}

export function guardPage(page, log = () => {}) {
  // "Leave this page?" must be accepted or navigation stalls; every other question is answered no.
  page.on("dialog", (dialog) =>
    void (dialog.type() === "beforeunload" ? dialog.accept() : dialog.dismiss()).catch(() => {}),
  );
  page.on("download", (download) => void download.cancel().catch(() => {}));
  page.on("popup", (popup) => void popup.close().catch(() => {}));
  page.on("pageerror", (error) => log(`pageerror: ${error.message}`));
}

// Count API traffic so a step can wait until the page has gone quiet.
export function trackActivity(page, isApi) {
  const state = { inflight: 0, last: Date.now() };
  const touch = () => (state.last = Date.now());
  page.on("request", (request) => {
    if (isApi(new URL(request.url()).pathname)) {
      state.inflight += 1;
      touch();
    }
  });
  const done = (request) => {
    if (isApi(new URL(request.url()).pathname)) {
      state.inflight = Math.max(0, state.inflight - 1);
      touch();
    }
  };
  page.on("requestfinished", done);
  page.on("requestfailed", done);
  return state;
}

export async function waitQuiet(page, state, quietMs = 300, capMs = 5000, minMs = 200) {
  const started = Date.now();
  await page.waitForTimeout(minMs);
  while (Date.now() - started < capMs) {
    if (state.inflight === 0 && Date.now() - state.last >= quietMs) return true;
    await page.waitForTimeout(60);
  }
  return false;
}

export async function signIn(page, base, credentials) {
  await page.goto(base, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("input", { timeout: 20000 });
  const inputs = await page.locator("input").all();
  await inputs[0].fill(credentials.login);
  await inputs[1].fill(credentials.password);
  await page.locator("form button, button[type=submit]").first().click();
  // The form is gone once the session is accepted.
  await page.waitForSelector("input[type=password]", { state: "detached", timeout: 20000 });
}

// Close whatever overlays a click opened.
export async function closeOverlays(page) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const overlay = page.locator(".modal-backdrop, dialog[open]").first();
    if (!(await overlay.count())) return;
    const close = overlay.getByRole("button", { name: /^(Закрыть|Отмена|Назад|×|✕)$/ }).first();
    if (await close.count()) await close.click({ timeout: 1500 }).catch(() => {});
    else await page.keyboard.press("Escape").catch(() => {});
    await page.waitForTimeout(150);
  }
}

// The left menu. On a phone it is a drawer behind the menu button and a group of it unfolds in place. On a desktop
// a group opens a flyout of tiles beside the menu, one at a time, and a closed flyout is not in the page.
const PLAIN = ".sidebar nav > button";
const HEADS = ".sidebar nav .nav-group > button";
const TILES = ".nav-flyout button, .nav-group-items button";
export const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Opens the drawer of a phone; true if it was closed before.
export async function openDrawer(page) {
  const toggle = page.locator(".menu-toggle").filter({ visible: true }).first();
  if (!(await toggle.count()) || (await page.locator(".sidebar.open").count())) return false;
  await toggle.click({ timeout: 2000 }).catch(() => {});
  await page.waitForTimeout(350);
  return true;
}

// Unfold the group at position `group` among the groups of the menu; gives the locator of its entries.
export async function openGroup(page, group) {
  const head = page.locator(HEADS).nth(group);
  if ((await head.getAttribute("aria-expanded")) === "false") {
    await head.click({ timeout: 2000 });
    await page.waitForTimeout(150);
  }
  return page.locator(".sidebar nav .nav-group").nth(group).locator(TILES);
}

// Every entry of the menu as {group, index, text}: group is -1 for an entry outside the groups, else its position.
export async function menuEntries(page) {
  await openDrawer(page);
  const texts = async (locator) => (await locator.allInnerTexts()).map((text) => text.replace(/\s+/g, " ").trim());
  const found = (await texts(page.locator(PLAIN))).map((text, index) => ({ group: -1, index, text }));
  const groups = await page.locator(HEADS).count();
  for (let group = 0; group < groups; group += 1)
    found.push(...(await texts(await openGroup(page, group))).map((text, index) => ({ group, index, text })));
  return found;
}

// The button of an entry given by menuEntries (its group is opened on the way).
export async function menuButton(page, entry) {
  await openDrawer(page);
  return entry.group < 0 ? page.locator(PLAIN).nth(entry.index) : (await openGroup(page, entry.group)).nth(entry.index);
}

// The entry whose text is `label` (a count behind the text is allowed), looked for outside the groups first; null if none.
export async function findMenuEntry(page, label) {
  const name = new RegExp(`^\\s*${escape(label)}(\\s*\\d+\\+?)?\\s*$`);
  const opened = await openDrawer(page);
  const plain = page.locator(PLAIN).filter({ hasText: name });
  if (await plain.count()) return plain.first();
  const groups = await page.locator(HEADS).count();
  for (let group = 0; group < groups; group += 1) {
    const found = (await openGroup(page, group)).filter({ hasText: name });
    if (await found.count()) return found.first();
  }
  // A drawer opened for nothing would stay over the page.
  if (opened) await page.keyboard.press("Escape");
  return null;
}

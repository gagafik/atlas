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

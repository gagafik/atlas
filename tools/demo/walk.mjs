// Walks every menu section of the app and clicks what it finds, so that the
// capture records each GET without a hand-kept endpoint list. Every write is
// refused by the capture's route rules, so clicking cannot change anything.
import { closeOverlays, waitQuiet } from "./browser-kit.mjs";

const SKIP_MENU = /^(Закрыть|Включить уведомления браузера|Обновить устройства|Прочитать все|Обновить|Прочитано|Открыть|Показать более ранние)$|Выйти|Пароль|Резервные|Принять приглашение|Состояние сервера|Попробовать на примере|Приложение|Информационные экраны/;
const SKIP_CLICK = /Выйти|Пароль|Резервные|Принять приглашение|Состояние сервера|Попробовать на примере|Удалить|Печать|Скачать|Экспорт|Выгрузить|Импорт|Загрузить|Отозвать|Заархивировать/;
const MENU_GROUPS = [
  { name: "sidebar", selector: ".sidebar nav button" },
  { name: "portal", selector: ".portal-tabs button" },
  { name: "account", selector: ".account-controls button" },
];
const EXCLUDE = ".sidebar, .account-controls, .portal-tabs, .demo-bar";

export async function menuTexts(page) {
  const found = [];
  for (const group of MENU_GROUPS) {
    const count = await page.locator(group.selector).count();
    for (let index = 0; index < count; index += 1) {
      const text = (await page.locator(group.selector).nth(index).innerText().catch(() => "")).replace(/\s+/g, " ").trim();
      if (text && !SKIP_MENU.test(text)) found.push({ group, index, text });
    }
  }
  return found;
}

function collect(page) {
  return page.evaluate((exclude) => {
    for (const old of document.querySelectorAll("[data-walk]")) old.removeAttribute("data-walk");
    const visible = (el) => {
      if (!el.offsetParent && getComputedStyle(el).position !== "fixed") return false;
      const style = getComputedStyle(el);
      return style.visibility !== "hidden" && !el.disabled && el.getAttribute("aria-disabled") !== "true";
    };
    const label = (el) =>
      (el.innerText || el.getAttribute("aria-label") || el.title || "").replace(/\s+/g, " ").trim().slice(0, 80);
    const clicks = [];
    const selects = [];
    let id = 0;
    for (const el of document.querySelectorAll("button, summary, [role=tab], [aria-pressed]")) {
      if (!visible(el) || el.closest(exclude)) continue;
      const text = label(el);
      if (!text) continue;
      el.setAttribute("data-walk", String(id));
      clicks.push({ id: id++, tag: el.tagName.toLowerCase(), text });
    }
    for (const el of document.querySelectorAll("select")) {
      if (!visible(el) || el.closest(exclude)) continue;
      el.setAttribute("data-walk", String(id));
      const options = [...el.options].map((o) => o.textContent.trim());
      selects.push({ id: id++, name: el.getAttribute("aria-label") || el.name || el.id || "", options, current: el.selectedIndex });
    }
    return { clicks, selects };
  }, EXCLUDE);
}

const SECTION_CAP = 14;
const NEWLINE = String.fromCharCode(10);

export async function explore(page, activity, section, budget, log) {
  const seen = budget.seen;
  let clicked = 0;
  for (let depth = 0; depth < 1 && budget.left > 0 && clicked < SECTION_CAP; depth += 1) {
    const { clicks, selects } = await collect(page);
    const once = new Set();
    const fresh = clicks
      .filter((item) => {
        const key = `${section}|${item.tag}|${item.text}`;
        if (seen.has(key) || once.has(key) || SKIP_CLICK.test(item.text)) return false;
        once.add(key);
        return true;
      })
      .slice(0, 45);
    const freshSelects = selects.filter((item) => !seen.has(`${section}|select|${item.name}|${item.options.join("/")}`));
    if (!fresh.length && !freshSelects.length) break;
    for (const item of freshSelects) {
      seen.add(`${section}|select|${item.name}|${item.options.join("/")}`);
      const select = page.locator(`[data-walk="${item.id}"]`);
      for (let option = 0; option < Math.min(item.options.length, 4) && budget.left > 0 && clicked < SECTION_CAP + 6; option += 1) {
        if (option === item.current) continue;
        if (!(await select.count())) break;
        await select.selectOption({ index: option }, { timeout: 2000 }).catch(() => {});
        budget.left -= 1;
        clicked += 1;
        await waitQuiet(page, activity);
        // A filter inside a form runs when the form is submitted (reports are POSTs that only read).
        const submitted = await select.evaluate((el) => {
          const form = el.closest("form");
          if (!form) return false;
          form.requestSubmit();
          return true;
        }).catch(() => false);
        if (submitted) await waitQuiet(page, activity);
      }
      if (await select.count()) await select.selectOption({ index: item.current }, { timeout: 2000 }).catch(() => {});
      await waitQuiet(page, activity);
    }
    for (const item of fresh) {
      if (budget.left <= 0 || clicked >= SECTION_CAP) break;
      seen.add(`${section}|${item.tag}|${item.text}`);
      const target = page.locator(`[data-walk="${item.id}"]`);
      if (!(await target.count())) continue;
      if (process.env.CAPTURE_TRACE) log(`    click ${section} > ${item.tag}: ${item.text}`);
      await target.first().click({ timeout: 2000, noWaitAfter: true }).catch(() => {});
      budget.left -= 1;
      clicked += 1;
      await waitQuiet(page, activity);
      await closeOverlays(page);
    }
  }
  log(`  ${section}: ${clicked} actions`);
}

export async function walkRole(page, base, activity, budget, log) {
  const reload = async () => {
    for (let attempt = 0; ; attempt += 1) {
      try {
        await page.goto(base, { waitUntil: "domcontentloaded" });
        await page.waitForSelector(".sidebar nav button, .portal-tabs button", { timeout: 15000 });
        break;
      } catch (error) {
        if (attempt >= 2) throw error;
      }
    }
    await waitQuiet(page, activity, 400, 8000, 600);
  };
  await reload();
  const entries = await menuTexts(page);
  for (const entry of entries) {
    if (budget.left <= 0) break;
    try {
      await reload();
      const button = page.locator(entry.group.selector).nth(entry.index);
      if (!(await button.count())) continue;
      await button.click({ timeout: 3000, noWaitAfter: true }).catch(() => {});
      await waitQuiet(page, activity, 400, 8000);
      await explore(page, activity, entry.text, budget, log);
    } catch (error) {
      log(`  ${entry.text}: stopped (${String(error.message).split(NEWLINE)[0]})`);
    }
  }
}

// Explicit journeys for what the presentation shows, per role. The walker may
// miss a drill-down; a journey cannot. capture.mjs runs them against the real
// backend (so their requests land in the snapshot) and verify.mjs runs the same
// lists over the built demo (so a missing answer is reported).
import { closeOverlays, waitQuiet } from "./browser-kit.mjs";

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function createKit({ page, base, activity, miss, log = () => {}, shots }) {
  let shot = 0;
  const settle = (ms = 450) => waitQuiet(page, activity, 350, 6000, ms);
  const kit = {
    page,
    // Back to the first screen of the role.
    async home() {
      await page.goto(base, { waitUntil: "domcontentloaded" });
      await page.waitForSelector(".sidebar nav button, .portal-tabs button", { timeout: 20000 });
      await settle(700);
    },
    // Click a button, tab, summary or link by its text (a string matches the whole text).
    async click(label, { optional = false } = {}) {
      const name = typeof label === "string" ? new RegExp(`^\\s*${escape(label)}\\s*$`) : label;
      const finders = [
        page.getByRole("button", { name }),
        page.getByRole("tab", { name }),
        page.locator("summary").filter({ hasText: name }),
        page.getByRole("link", { name }),
        page.locator("[aria-pressed]").filter({ hasText: name }),
      ];
      // Panels load lazily: give the target a few seconds to appear before calling it a miss.
      for (let attempt = 0; attempt < (optional ? 4 : 16); attempt += 1) {
        for (const finder of finders) {
          const target = finder.filter({ visible: true }).first();
          if (await target.count()) {
            // On a phone the administrator's menu is a drawer behind the menu button.
            const inDrawer = await target.evaluate((el) => !!el.closest(".sidebar")).catch(() => false);
            const toggle = page.locator(".menu-toggle").filter({ visible: true }).first();
            if (inDrawer && (await toggle.count()) && !(await page.locator(".sidebar.open").count())) {
              await toggle.click().catch(() => {});
              await page.waitForTimeout(350);
            }
            await target.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
            await target.click({ timeout: 3000, noWaitAfter: true }).catch((error) => log(`click failed: ${label}: ${error.message.split(String.fromCharCode(10))[0]}`));
            await settle();
            return true;
          }
        }
        await page.waitForTimeout(250);
      }
      if (!optional) miss(`not found: ${label}`);
      return false;
    },
    // A path of clicks from the home screen.
    async path(...labels) {
      await kit.home();
      for (const label of labels) if (!(await kit.click(label))) return false;
      return true;
    },
    // Continue clicking from where the page stands.
    async then(...labels) {
      for (const label of labels) if (!(await kit.click(label))) return false;
      return true;
    },
    // Choose option `which` (an index, a text or a pattern) in the select whose label starts with `selectLabel`.
    async choose(selectLabel, which) {
      const own = new RegExp(`^\\s*${escape(selectLabel)}`);
      let field = page.locator("label").filter({ has: page.locator("span").filter({ hasText: own }) }).locator("select").filter({ visible: true }).first();
      if (!(await field.count())) field = page.locator("label").filter({ hasText: own }).locator("select").filter({ visible: true }).first();
      if (!(await field.count())) return miss(`no select: ${selectLabel}`);
      if (typeof which === "number") await field.selectOption({ index: which }, { timeout: 3000 }).catch(() => miss(`option ${which} of ${selectLabel}`));
      else {
        const options = await field.locator("option").allInnerTexts();
        const match = options.find((text) => (typeof which === "string" ? text.trim() === which : which.test(text)));
        if (match === undefined) return miss(`option ${which} of ${selectLabel}`);
        await field.selectOption({ label: match.trim() }, { timeout: 3000 }).catch(() => miss(`option ${which} of ${selectLabel}`));
      }
      await settle();
    },
    // The text is on the page (a journey step that shows something).
    async see(text) {
      const target = page.getByText(text).filter({ visible: true }).first();
      if (!(await target.count())) miss(`text not shown: ${text}`);
    },
    async close() {
      await closeOverlays(page);
    },
    async snap(name) {
      const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      if (wide) log(`layout: the page scrolls sideways at "${name}"`);
      if (!shots) return;
      shot += 1;
      await page.screenshot({ path: `${shots}-${String(shot).padStart(2, "0")}-${name}.png` });
    },
  };
  return kit;
}

// What the presentation shows, per role. Labels are the visible texts of the app.
const JOURNAL = "Учебный журнал";
const DAY_LESSON = /Математика 7А/;

async function lessonScreens(kit, name) {
  // The lesson of the day: attendance and marks, then back to the list.
  await kit.path(JOURNAL, DAY_LESSON);
  await kit.snap(`${name}-lesson`);
}

export const journeys = {
  async admin(kit) {
    await kit.home();
    await kit.snap("timetable");
    await kit.path(/^Уведомления/);
    await kit.snap("notifications");
    await kit.close();
    await kit.click("Учитель", { optional: true });
    await kit.click("Класс", { optional: true });
    // Journal control: coverage, attendance, the final table, course table, periods.
    await kit.path(JOURNAL, "Контроль заполнения журналов");
    await kit.click("Проверить заполнение");
    await kit.snap("coverage");
    for (const state of ["Все состояния", "Заполнен", "Нет публикации", "Журнал не создан"]) {
      await kit.choose("Состояние журнала", state);
      await kit.click("Проверить заполнение");
    }
    await kit.choose("Состояние журнала", "Все состояния");
    await kit.click("Сводка по классам");
    await kit.snap("coverage-classes");
    await kit.path(JOURNAL, "Сводная посещаемость");
    await kit.click("Показать сводку");
    for (const className of ["7А", "7Б", "8А", "8Б"]) {
      await kit.choose("Класс сводки", className);
      await kit.click("Показать сводку");
    }
    await kit.snap("attendance");
    await kit.path(JOURNAL, "Сетка посещаемости класса");
    await kit.path(JOURNAL, "Итоговые оценки и табели");
    await kit.choose("Период", "Первая четверть");
    for (const className of ["7А", "7Б", "8А", "8Б"]) await kit.choose("Класс", className);
    await kit.choose("Класс", "7А");
    await kit.snap("final-grades");
    await kit.choose("Ученик для табеля", 1);
    await kit.choose("Период табеля", "Первая четверть");
    await kit.click("Показать табель");
    await kit.path(JOURNAL, "Учебные периоды и закрытие журналов");
    await kit.snap("periods");
    await kit.path(JOURNAL, "Таблица курса");
    for (const course of [/Математика/, /Русский язык/]) await kit.choose("Курс", course);
    await kit.snap("course-table");
    await kit.path(JOURNAL, DAY_LESSON);
    await kit.snap("lesson");
    // Workload, substitutions, messages, forms.
    await kit.path("Нагрузка учителей");
    await kit.snap("workload");
    await kit.path("Замены и даты", "Замена на день");
    await kit.snap("substitutions");
    for (const tab of ["Занятия на дату", "Отсутствия учителей", "Все изменения", "Итоги замен"]) await kit.click(tab);
    await kit.path("Сообщения");
    await kit.click(/Родительские собрания/);
    await kit.snap("messages");
    await kit.path("Формы и опросы");
    await kit.click(/Согласие на экскурсию/);
    await kit.snap("forms");
    await kit.path("Табель и отсутствия");
    await kit.path("Встречи и консультации");
    await kit.path("Домашние задания");
    await kit.path("Контрольные и тесты");
  },

  async teacher(kit) {
    await kit.home();
    await kit.snap("home");
    await kit.path(/^Уведомления/);
    await kit.close();
    await lessonScreens(kit, "teacher");
    await kit.path(JOURNAL, /Математика 7Б/);
    await kit.path(JOURNAL, "Таблица курса");
    for (const course of [/7А · Математика/, /7Б · Математика/]) await kit.choose("Курс", course);
    await kit.snap("course-table");
    await kit.path("Домашние задания");
    await kit.click("Проверить работу");
    await kit.snap("homework-review");
    await kit.close();
    for (const status of [1, 2, 3, 4]) {
      await kit.choose("Статус работы", status);
      await kit.click("Применить фильтры");
    }
    await kit.path("Контрольные и тесты");
    await kit.click("Открыть тест");
    await kit.snap("test-results");
    await kit.close();
    await kit.path("Встречи и консультации", "Консультации");
    await kit.snap("consultations");
    await kit.click(/Консультации по математике/);
    await kit.snap("consultation-slots");
    await kit.click("День ведущего", { optional: true });
    await kit.snap("host-day");
    await kit.close();
    await kit.path(/^Сообщения/);
    await kit.snap("messages");
    await kit.path("Похвалы и замечания");
    await kit.path("Календарь контрольных");
    await kit.path("Моя нагрузка");
  },

  async parent(kit) {
    await kit.home();
    await kit.snap("home");
    await kit.path(/^Уведомления/);
    await kit.close();
    await kit.path(JOURNAL);
    await kit.snap("journal");
    await kit.click("Открыть домашнюю работу");
    await kit.close();
    await kit.click("Итоговые оценки и табели", { optional: true });
    await kit.click("Отсутствия и объяснительные", { optional: true });
    await kit.path("Домашние задания");
    await kit.click("Посмотреть работу");
    await kit.snap("homework");
    await kit.close();
    await kit.path("Расписание");
    await kit.choose("Режим просмотра", "Неделя");
    await kit.snap("timetable");
    await kit.path("Встречи и консультации", "Консультации");
    await kit.snap("consultations");
    await kit.click(/Консультации по математике/);
    await kit.snap("consultation-slots");
    await kit.close();
    await kit.path("Встречи и консультации", "Собрания классов");
    await kit.snap("class-meetings");
    await kit.close();
    await kit.path("Сообщения");
    await kit.click(/Вопрос по домашнему заданию/);
    await kit.snap("messages");
    await kit.path("Контрольные и тесты");
    await kit.click("Открыть тест");
    await kit.close();
    await kit.path("Мои дети и доступ");
    await kit.path("Формы и опросы");
    await kit.path("Похвалы и замечания");
  },

  async student(kit) {
    await kit.home();
    await kit.snap("home");
    await kit.path(/^Уведомления/);
    await kit.close();
    await kit.path("Расписание");
    await kit.choose("Режим просмотра", "Неделя");
    await kit.snap("timetable");
    await kit.path("Домашние задания");
    await kit.click("Выполнить задание");
    await kit.snap("homework");
    await kit.close();
    await kit.path(JOURNAL);
    await kit.snap("marks");
    await kit.path("Контрольные и тесты");
    await kit.click("Открыть тест");
    await kit.snap("test");
    await kit.close();
    await kit.path("Сообщения");
  },
};

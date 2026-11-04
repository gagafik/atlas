// Explicit journeys for what the presentation shows, per role. The walker may
// miss a drill-down; a journey cannot. capture.mjs runs them against the real
// backend (so their requests land in the snapshot) and verify.mjs runs the same
// lists over the built demo (so a missing answer is reported).
import { closeOverlays, escape, findMenuEntry, waitQuiet } from "./browser-kit.mjs";

export function createKit({ page, base, activity, miss, log = () => {}, shots }) {
  let shot = 0;
  const settle = (ms = 450) => waitQuiet(page, activity, 350, 6000, ms);
  const kit = {
    page,
    // Back to the first screen of the role.
    async home() {
      await page.goto(base, { waitUntil: "domcontentloaded" });
      await page.waitForSelector(".sidebar nav button", { state: "attached", timeout: 20000 });
      await settle(700);
    },
    // Click a button, tab, summary or link of the page by its text (a string matches the whole text).
    // The menu, the top bar and the account menu have their own steps: menu, bell, account.
    async click(label, { optional = false } = {}) {
      const name = typeof label === "string" ? new RegExp(`^\\s*${escape(label)}\\s*$`) : label;
      const content = page.locator(":not(.sidebar *, .topbar *)");
      const finders = [
        page.getByRole("button", { name }).and(content),
        page.getByRole("tab", { name }).and(content),
        page.locator("summary").filter({ hasText: name }),
        page.getByRole("link", { name }).and(content),
        page.locator("[aria-pressed]").filter({ hasText: name }),
      ];
      // Panels load lazily: give the target a few seconds to appear before calling it a miss.
      for (let attempt = 0; attempt < (optional ? 4 : 16); attempt += 1) {
        for (const finder of finders) {
          const target = finder.filter({ visible: true }).first();
          if (await target.count()) {
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
    // An entry of the left menu by its text (a count behind the text is allowed). On a phone the menu is a drawer
    // behind the menu button and a group unfolds in place; on a desktop a group opens a flyout of tiles.
    async menu(label, { optional = false } = {}) {
      for (let attempt = 0; attempt < (optional ? 4 : 16); attempt += 1) {
        const entry = await findMenuEntry(page, label);
        if (entry) {
          await entry.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
          await entry.click({ timeout: 3000, noWaitAfter: true }).catch((error) => log(`click failed: ${label}: ${error.message.split(String.fromCharCode(10))[0]}`));
          await settle();
          return true;
        }
        await page.waitForTimeout(250);
      }
      if (!optional) miss(`menu entry not found: ${label}`);
      return false;
    },
    // The bell of the top bar (the inbox of notifications).
    async bell() {
      await page.locator(".notifications-button").first().click({ timeout: 3000, noWaitAfter: true }).catch((error) => miss(`bell: ${error.message.split(String.fromCharCode(10))[0]}`));
      await settle();
    },
    // An entry of the account menu in the top bar.
    async account(label) {
      await page.locator(".account-toggle").click({ timeout: 3000 }).catch(() => miss(`no account menu: ${label}`));
      const entry = page.locator(".account-menu button").filter({ hasText: new RegExp(`^\\s*${escape(label)}\\s*$`) }).first();
      if (!(await entry.count())) return miss(`account entry not found: ${label}`);
      await entry.click({ timeout: 3000, noWaitAfter: true }).catch(() => miss(`account entry: ${label}`));
      await settle();
    },
    // A path of clicks from the home screen: an entry of the menu, then buttons of the page.
    async path(first, ...labels) {
      await kit.home();
      if (!(await kit.menu(first))) return false;
      for (const label of labels) if (!(await kit.click(label))) return false;
      return true;
    },
    // Continue clicking from where the page stands.
    async then(...labels) {
      for (const label of labels) if (!(await kit.click(label))) return false;
      return true;
    },
    // The select whose label starts with `selectLabel`.
    selectOf(selectLabel) {
      const own = new RegExp(`^\\s*${escape(selectLabel)}`);
      const exact = page.locator("label").filter({ has: page.locator("span").filter({ hasText: own }) }).locator("select").filter({ visible: true }).first();
      return exact.or(page.locator("label").filter({ hasText: own }).locator("select").filter({ visible: true }).first()).first();
    },
    async optionTexts(selectLabel) {
      const field = kit.selectOf(selectLabel);
      if (!(await field.count())) return [];
      return (await field.locator("option").allInnerTexts()).map((text) => text.trim());
    },
    // Choose option `which` (an index, a text or a pattern) in the select whose label starts with `selectLabel`.
    async choose(selectLabel, which) {
      const field = kit.selectOf(selectLabel);
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
    // Every option of a select in turn (the first `max`), each followed by `then(text, index)`.
    async eachOption(selectLabel, then, { from = 0, max = 12 } = {}) {
      const texts = await kit.optionTexts(selectLabel);
      if (!texts.length) return miss(`no select: ${selectLabel}`);
      for (let index = from; index < Math.min(texts.length, from + max); index += 1) {
        await kit.choose(selectLabel, index);
        await then?.(texts[index], index);
      }
    },
    // A date field by the start of its label (the day view of a family has no label text, only an aria-label).
    async setDate(label, iso) {
      const own = new RegExp(`^\\s*${escape(label)}`);
      const labelled = page.locator("label").filter({ has: page.locator("span").filter({ hasText: own }) }).locator("input").filter({ visible: true }).first();
      const field = labelled.or(page.getByLabel(own).filter({ visible: true }).first()).first();
      if (!(await field.count())) return miss(`no date field: ${label}`);
      await field.fill(iso, { timeout: 3000 }).catch(() => miss(`date ${iso} of ${label}`));
      await settle();
    },
    // Every visible button whose name matches (the first `max`), each followed by `then(index)`.
    async eachButton(pattern, then, { max = 40 } = {}) {
      const items = page.getByRole("button", { name: pattern });
      const total = Math.min(await items.count(), max);
      for (let index = 0; index < total; index += 1) {
        const target = items.nth(index);
        if (!(await target.isVisible().catch(() => false))) continue;
        await target.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
        await target.click({ timeout: 3000, noWaitAfter: true }).catch((error) => log(`click failed: ${pattern}: ${error.message.split(String.fromCharCode(10))[0]}`));
        await settle();
        await then?.(index);
      }
      return total;
    },
    // A tick box by its accessible name.
    async check(label) {
      const box = page.getByRole("checkbox", { name: label }).filter({ visible: true }).first();
      if (!(await box.count())) return miss(`no tick box: ${label}`);
      await box.check({ timeout: 3000 }).catch(() => miss(`tick box: ${label}`));
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
const JOURNAL = "Классный журнал";
// The screens of a pupil's and a parent's journal: «Классный журнал» is the day, «Оценки» the grades with their tabs,
// «Посещаемость» the grid and the notes, «Табели» the report cards (in the menu once the school published them).
const PUPIL_GRADES = "Оценки";
// The tab of the new grades carries the number of unsigned ones behind its text.
const PUPIL_TABS = ["По дате", "Заметки", "Оценки на графике", /^Новые оценки/, "Сводка оценок"];
// The chip of one mark in the summary: «Работа: 8 / 10, 30.10.2026».
const MARK_CHIP = /: [\d,]+ \/ [\d,]+, \d\d\.\d\d\.\d{4}/;
// The «?» behind an average: «Как считается среднее: Математика».
const AVERAGE_RULE = /^Как считается среднее/;
// The notes of the pupil are grouped four ways.
const NOTE_GROUPINGS = ["Учитель", "Дата", "Тип", "Предмет"];
// A mark of the attendance grid is named by the mark and its lesson: «Отсутствовал. Математика, … 09:00–09:40».
const ATTENDANCE_MARK = /, \d\d:\d\d–\d\d:\d\d/;
// The tabs of a pupil's grades for the staff: no notes of the family, and who signed instead of the new grades.
const STAFF_GRADE_TABS = ["По дате", "Оценки на графике", "Подписи семьи", "Сводка оценок"];
const DAYS_BACK = 3;
// The menu entry of the messages; the number of unread threads is a badge behind the text.
const MESSAGES = "Уведомления";
// The button that opens a lesson of the day grid is named by the lesson: «Открыть урок · 09:00–09:40 · Математика 7А».
const DAY_LESSON = /Математика 7А/;
// A course card of «Оценки»: it ends with the kind of the access to the course.
const COURSE_CARD = /(Мой курс|только чтение)$/;
// The head of a work column of the gradebook is named by the day of its lesson: «Открыть урок 30.10.2026» (a lesson of the day grid has a time behind «Открыть урок»).
const WORK_LESSON = /^Открыть урок \d\d\.\d\d\.\d{4}$/;
const CLASSES = ["7А", "7Б", "8А", "8Б"];

// The day grid of the journal: the arrows, a day of the calendar, a lesson opened right under its row, another one on the
// pupils' cards, the months of the calendar from September (the year begins on 5 October) to December, and «Сегодня».
async function dayGrid(kit, { calendarDay }) {
  await kit.path(JOURNAL);
  await kit.click("Предыдущий день");
  await kit.click("Следующий день");
  await kit.click(calendarDay);
  await kit.click(DAY_LESSON);
  await kit.snap("day-grid");
  await kit.click(/^присутствуют /);
  await kit.snap("day-pupils");
  await kit.click("Предыдущий месяц");
  for (let month = 0; month < 3; month += 1) await kit.click("Следующий месяц");
  await kit.click("Сегодня");
}

// The report of a pupil for the staff: the screens of the family, over what the account may read. It opens on the first pupil of
// the list; the pupils behind the picker have no answer in the demo (every one would add a whole report to the public snapshot).
async function pupilReport(kit) {
  await kit.path(JOURNAL, "Отчёт ученика");
  await kit.snap("pupil-grades");
  for (const tab of STAFF_GRADE_TABS) await kit.click(tab);
  await kit.click("Посещаемость");
  await kit.snap("pupil-attendance");
  await kit.click(ATTENDANCE_MARK, { optional: true });
  await kit.close();
  await kit.click("Записи уроков");
  await kit.click("Предыдущий день");
  await kit.snap("pupil-day");
  await kit.click("Оценки");
}

async function lessonScreens(kit, name) {
  // The lesson of the day: attendance and marks, then back to the list.
  await kit.path(JOURNAL, DAY_LESSON);
  await kit.snap(`${name}-lesson`);
}

// The day of the family journal: three days back with a homework opened, a day of the calendar, a date typed in, the arrow
// forward and back to the day of the demo.
async function familyDay(kit) {
  await kit.path(JOURNAL);
  await kit.snap("journal");
  for (let step = 0; step < DAYS_BACK; step += 1) await kit.click("Предыдущий день");
  await kit.snap("journal-day");
  await kit.click("Открыть домашнюю работу");
  await kit.close();
  await kit.click(/^20\.10\.2026$/);
  await kit.setDate("Дата журнала", "2026-11-06");
  await kit.click("Следующий день");
  await kit.click("Сегодня");
}

// «Оценки»: every tab, the notes grouped four ways, the chip of a mark and the rule of an average in their sheets, then
// every period the school has (and the whole year) on the summary.
async function familyGrades(kit) {
  await kit.path(PUPIL_GRADES);
  await kit.snap("grades");
  for (const tab of PUPIL_TABS) await kit.click(tab);
  await kit.click("Заметки");
  for (const by of NOTE_GROUPINGS) await kit.click(by);
  await kit.snap("notes");
  await kit.click("Сводка оценок");
  await kit.click(MARK_CHIP);
  await kit.snap("mark");
  await kit.close();
  await kit.click(AVERAGE_RULE);
  await kit.snap("average");
  await kit.close();
  await kit.eachOption("Период", null);
}

// «Посещаемость»: the grid, the sheet of a mark, every period and, for a parent, the notes of absences with the form of a new one.
async function familyAttendance(kit, { notes }) {
  await kit.path("Посещаемость");
  await kit.snap("attendance");
  await kit.click(ATTENDANCE_MARK);
  await kit.snap("attendance-mark");
  await kit.close();
  await kit.eachOption("Период", null);
  await kit.choose("Период", 0);
  await kit.click("Записки об отсутствии", { optional: !notes });
  await kit.click("Добавить новую записку об отсутствии", { optional: !notes });
  await kit.snap("attendance-notes");
}

// The four screens of the family journal. The notes of absences are the parent's; a pupil has them only when the school filed one.
async function familyScreens(kit, { notes }) {
  await familyDay(kit);
  await familyGrades(kit);
  await familyAttendance(kit, { notes });
  await kit.home();
  if (await kit.menu("Табели", { optional: true })) {
    await kit.choose("Период табеля", 1);
    await kit.click("Показать табель", { optional: true });
    await kit.snap("report-card");
  }
}

// Coverage of the journals and the attendance summary (administrator and teacher).
async function controlScreens(kit, { classes = CLASSES } = {}) {
  await kit.click("Проверить заполнение");
  await kit.snap("coverage");
  for (const state of ["Все состояния", "Заполнен", "Нет публикации", "Журнал не создан"]) {
    await kit.choose("Состояние журнала", state);
    await kit.click("Проверить заполнение");
  }
  await kit.choose("Состояние журнала", "Все состояния");
  await kit.click("Сводка по классам");
  await kit.snap("coverage-classes");
}

async function attendanceSummary(kit) {
  await kit.click("Показать сводку");
  await kit.eachOption("Класс сводки", () => kit.click("Показать сводку"), { max: 6 });
  await kit.snap("attendance");
}

// The family's homework: the queue of the next two weeks, the whole year, every filter.
async function homeworkQueue(kit, { filterLabel }) {
  await kit.path("Домашние задания");
  await kit.click("Весь учебный год", { optional: true });
  await kit.snap("homework-year");
  if (filterLabel) {
    await kit.eachOption(filterLabel, () => kit.click("Применить фильтры"), { max: 6 });
    await kit.choose(filterLabel, 0);
  }
}

export const journeys = {
  async admin(kit) {
    await kit.home();
    await kit.snap("home");
    await kit.path("Расписание");
    await kit.snap("timetable");
    await kit.bell();
    await kit.snap("notifications");
    await kit.close();
    await kit.click("Учитель", { optional: true });
    await kit.click("Класс", { optional: true });
    // Journal control: coverage, attendance, the final table, course table, periods.
    await kit.path(JOURNAL, "Контроль заполнения журналов");
    await controlScreens(kit);
    await kit.path(JOURNAL, "Сводная посещаемость");
    await attendanceSummary(kit);
    await kit.path("Посещаемость (учащиеся)");
    await kit.path("Табели");
    await kit.click("Открыть готовую ведомость");
    await kit.snap("final-grades-sheet");
    await kit.choose("Период", "Первая четверть");
    for (const className of CLASSES) await kit.choose("Класс", className);
    await kit.choose("Класс", "7А");
    await kit.snap("final-grades");
    await kit.choose("Ученик для табеля", 1);
    await kit.choose("Период табеля", "Первая четверть");
    await kit.click("Показать табель");
    await kit.path(JOURNAL, "Учебные периоды и закрытие журналов");
    await kit.snap("periods");
    await kit.path("Оценки", /7А · Математика/);
    await kit.choose("Шкала", /Шкала 2–5/);
    await kit.click("‹ Все курсы");
    await kit.click(/8Б · Физика/);
    await kit.snap("course-table");
    await kit.path(JOURNAL, DAY_LESSON);
    await kit.snap("lesson");
    await dayGrid(kit, { calendarDay: /^30\.10\.2026/ });
    await pupilReport(kit);
    await kit.path(JOURNAL);
    await kit.choose("Класс", "7А");
    await kit.snap("day-class");
    // Workload, substitutions, messages, forms.
    await kit.path("Нагрузка учителей");
    await kit.snap("workload");
    await kit.path("Замены", "Замена на день");
    await kit.snap("substitutions");
    for (const tab of ["Занятия на дату", "Отсутствия учителей", "Все изменения", "Итоги замен"]) await kit.click(tab);
    await kit.path(MESSAGES);
    await kit.click(/Родительские собрания/);
    await kit.snap("messages");
    await kit.path(MESSAGES, /Педагогический совет/);
    await kit.path("Регистрация / Опросы");
    await kit.click(/Согласие на экскурсию/);
    await kit.click("Результаты");
    await kit.snap("forms");
    await kit.path("Посещаемость учителей");
    await kit.path("Родительские собрания", "Консультации");
    await kit.click(/Консультации по математике/);
    await kit.snap("consultations");
    await kit.path("Родительские собрания", "Собрания классов");
    await kit.snap("class-meetings");
    await kit.path("Журналы регистрации");
    await kit.path("Пользователи и доступ");
    await kit.path("Домашние задания");
    await kit.path("Контрольные и тесты");
    await kit.path("Учебная нагрузка");
    await kit.path("Классы и группы");
    await kit.path("Учителя");
    await kit.path("Учебный календарь");
  },

  async teacher(kit) {
    await kit.home();
    await kit.snap("home");
    await kit.bell();
    await kit.close();
    await lessonScreens(kit, "teacher");
    await kit.path(JOURNAL, /Математика 7Б/);
    await kit.path("Оценки");
    await kit.eachButton(COURSE_CARD, () => kit.click("‹ Все курсы"), { max: 12 });
    await kit.click(/7А · Математика/);
    await kit.choose("Шкала", /Шкала 2–5/);
    await kit.snap("course-table");
    // The head of a work column opens the lesson it was given in.
    await kit.click(WORK_LESSON);
    await kit.snap("work-lesson");
    // A new work is added in the latest lesson of the course, which opens in the journal.
    await kit.path("Оценки", /7А · Математика/);
    await kit.click("Новая работа");
    await kit.snap("new-work");
    // The pupils ticked: the form of a note opens with them. It asks before it is closed with pupils in it, so the next path leaves it by reloading.
    await kit.path("Оценки", /7А · Математика/);
    await kit.check("Выбрать всех учеников");
    await kit.click("Новая заметка");
    await kit.snap("behaviour-prefill");
    await kit.path("Оценки", /7А · Математика/);
    await kit.check("Выбрать всех учеников");
    await kit.click("Воспитательная мера");
    await kit.snap("measure-prefill");
    await dayGrid(kit, { calendarDay: /^30\.10\.2026/ });
    await pupilReport(kit);
    // Report cards are in the menu only when the teacher has any.
    await kit.home();
    if (await kit.menu("Табели", { optional: true })) await kit.click("Открыть готовую ведомость", { optional: true });
    await kit.path("Посещаемость учащихся", "Отсутствия и объяснительные");
    await kit.click("Сводная посещаемость");
    await attendanceSummary(kit);
    await kit.click("Контроль заполнения журналов");
    await controlScreens(kit);
    await kit.click("Сетка посещаемости класса");
    await kit.click("Мои полномочия по классам");
    await kit.path("Домашние задания");
    await kit.click("Проверить работу");
    await kit.snap("homework-review");
    await kit.close();
    await kit.eachOption("Статус работы", () => kit.click("Применить фильтры"), { max: 6 });
    await kit.path("Контрольные и тесты");
    await kit.click("Открыть тест");
    await kit.snap("test-results");
    await kit.close();
    await kit.path("Родительские собрания", "Консультации");
    await kit.snap("consultations");
    await kit.click(/Консультации по математике/);
    await kit.snap("consultation-slots");
    await kit.click("День ведущего", { optional: true });
    await kit.snap("host-day");
    await kit.close();
    await kit.path("Родительские собрания", "Собрания классов");
    await kit.snap("class-meetings");
    await kit.path(MESSAGES);
    await kit.click(/Вопрос по домашнему заданию/);
    await kit.snap("messages");
    await kit.path("Официальные письма");
    await kit.path("События");
    await kit.path("Моя нагрузка");
    await kit.path("Расписание");
  },

  async parent(kit) {
    await kit.home();
    await kit.snap("home");
    await kit.bell();
    await kit.close();
    await familyScreens(kit, { notes: true });
    await homeworkQueue(kit, {});
    await kit.click("Посмотреть работу");
    await kit.snap("homework");
    await kit.close();
    await kit.path("Расписание");
    await kit.choose("Режим просмотра", "Неделя");
    await kit.snap("timetable");
    await kit.path("Родительские собрания", "Консультации");
    await kit.snap("consultations");
    await kit.click(/Консультации по математике/);
    await kit.snap("consultation-slots");
    await kit.close();
    await kit.path("Родительские собрания", "Собрания классов");
    await kit.snap("class-meetings");
    await kit.close();
    await kit.path(MESSAGES);
    await kit.click(/Вопрос по домашнему заданию/);
    await kit.snap("messages");
    await kit.path("Контрольные и тесты");
    await kit.click("Открыть тест");
    await kit.close();
    await kit.path("События");
    await kit.snap("assessment-calendar");
    await kit.home();
    await kit.account("Мои дети и доступ");
    await kit.path("Регистрация / Опросы");
    await kit.path("Официальные письма");
    await kit.path("Учебный план");
  },

  async student(kit) {
    await kit.home();
    await kit.snap("home");
    await kit.bell();
    await kit.close();
    await kit.path("Расписание");
    await kit.choose("Режим просмотра", "Неделя");
    await kit.snap("timetable");
    await homeworkQueue(kit, {});
    await kit.click("Выполнить задание");
    await kit.snap("homework");
    await kit.close();
    await familyScreens(kit, { notes: false });
    await kit.path("Контрольные и тесты");
    await kit.click("Открыть тест");
    await kit.snap("test");
    await kit.close();
    await kit.path("События");
    await kit.path(MESSAGES);
    await kit.click(/Вопрос по домашнему заданию/, { optional: true });
    await kit.path("Учебный план");
  },
};

// Tours open every item of the lists the journeys only sample: all lesson cards of
// several days, all courses, all message threads, all homework works. verify.mjs
// runs them over the built demo; a snapshot miss on the way is a problem. The
// capture does not click them (it asks the backend for the same details).
const LESSON_CARD = /^Открыть урок · \d\d:\d\d–\d\d:\d\d · /;

async function lessonCards(kit, days) {
  for (const day of days) {
    await kit.path(JOURNAL);
    await kit.setDate("Дата журнала", day);
    await kit.eachButton(LESSON_CARD, null, { max: 40 });
  }
}

// The day view of a pupil: every school day of the two weeks before the demo day, the homework of each opened.
async function familyDays(kit) {
  await kit.path(JOURNAL);
  for (let step = 0; step < 14; step += 1) {
    await kit.click("Предыдущий день");
    await kit.eachButton("Открыть домашнюю работу", () => kit.close(), { max: 20 });
  }
}

// Every tab of «Оценки» and the marks of «Посещаемость» on every period a pupil can choose, and the report card of every period.
async function familyPeriods(kit) {
  await kit.path(PUPIL_GRADES);
  await kit.eachOption("Период", async () => {
    for (const tab of PUPIL_TABS) await kit.click(tab);
  });
  await kit.path("Посещаемость");
  await kit.eachOption("Период", () => kit.eachButton(ATTENDANCE_MARK, () => kit.close(), { max: 3 }));
  await kit.home();
  if (await kit.menu("Табели", { optional: true })) await kit.eachOption("Период табеля", () => kit.click("Показать табель", { optional: true }));
}

// Every course card of «Оценки»: its table on the opening period, then on the first period of the list, and back.
async function everyCourse(kit, max) {
  await kit.eachButton(
    COURSE_CARD,
    async () => {
      await kit.choose("Период", 0);
      await kit.click("‹ Все курсы");
    },
    { max },
  );
}

async function everyThread(kit, back) {
  await kit.path(MESSAGES);
  const total = await kit.eachButton(/\d\d\.\d\d, \d\d:\d\d/, () => kit.click(back, { optional: true }), { max: 12 });
  if (!total) await kit.eachButton(/Открыть|Ответить/, null, { max: 6 });
}

export const tours = {
  async admin(kit) {
    await lessonCards(kit, ["2026-11-02", "2026-10-30", "2026-10-21"]);
    await kit.path("Оценки");
    await everyCourse(kit, 60);
    await kit.path("Табели");
    await kit.click("Открыть готовую ведомость");
    await kit.eachOption("Ученик для табеля", () => kit.click("Показать табель"), { from: 1, max: 20 });
    await everyThread(kit, "Входящие");
    await kit.path("Регистрация / Опросы");
    await kit.eachButton(/Согласие на экскурсию/, () => kit.click("Результаты", { optional: true }), { max: 3 });
    await kit.path("Родительские собрания", "Консультации");
    await kit.eachButton(/Консультации по математике/, null, { max: 3 });
    await kit.path("Родительские собрания", "Собрания классов");
    await kit.path("Журналы регистрации");
    await kit.path("Пользователи и доступ");
  },

  async teacher(kit) {
    await lessonCards(kit, ["2026-11-02", "2026-10-30", "2026-10-29", "2026-10-21", "2026-10-06"]);
    await kit.path("Оценки");
    await everyCourse(kit, 20);
    await everyThread(kit, "Входящие");
    await kit.path("Домашние задания");
    await kit.eachButton("Проверить работу", () => kit.close(), { max: 12 });
    await kit.path("Посещаемость учащихся", "Отсутствия и объяснительные");
    await kit.click("Сводная посещаемость");
    await kit.path("События");
    await kit.eachButton(/^[←→]$/, null, { max: 8 });
  },

  async parent(kit) {
    await familyDays(kit);
    await familyPeriods(kit);
    await homeworkQueue(kit, {});
    await kit.eachButton("Посмотреть работу", () => kit.close(), { max: 40 });
    await everyThread(kit, "Входящие");
    await kit.path("События");
    await kit.eachButton(/^[←→]$/, null, { max: 8 });
  },

  async student(kit) {
    await familyDays(kit);
    await familyPeriods(kit);
    await homeworkQueue(kit, {});
    await kit.eachButton(/Выполнить задание|Посмотреть работу/, () => kit.close(), { max: 40 });
    await kit.path("События");
    await kit.eachButton(/^[←→]$/, null, { max: 8 });
  },
};

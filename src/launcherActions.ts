// The action launcher «Начните своё действие здесь» of the top bar: what a member of staff can start from anywhere.
// Every action opens an entry of the menu (and may ask the screen to start with an editor), so the list is
// read off the menu of the role: an entry that is hidden or not allowed takes its actions with it. No React here.
import type { Role } from "./auth";
import type { LaunchIntent } from "./launch";
import {
  menuItems,
  type MenuContext,
  type NavItem,
  type NavMenu,
} from "./navigation";

export interface LauncherAction {
  id: string;
  label: string;
  // The group of the list that holds it (a popular action is also in «Популярные» while nothing is typed).
  category: string;
  popular: boolean;
  // The entry of the menu it opens, and the editor the screen starts with, if any.
  item: NavItem;
  intent?: LaunchIntent;
  // More words the search finds it by.
  words?: string;
}
export interface LauncherGroup {
  id: string;
  label: string;
  actions: LauncherAction[];
}

// EduPage's categories of the launcher, in its order.
const CATEGORIES: [string, string][] = [
  ["popular", "Популярные"],
  ["timetable", "Расписание и замены"],
  ["grades", "Оценки и успеваемость"],
  ["attendance", "Посещаемость"],
  ["journal", "Классный журнал"],
  ["plans", "Планы"],
  ["works", "Домашние задания, работы, тесты"],
  ["communication", "Связь"],
  ["site", "Изменить веб-страницу"],
];
const POPULAR = "popular";

interface Spec {
  id: string;
  label: string;
  category: string;
  // Menu entries that can serve the action: the first one the role has opens it.
  via: string[];
  intent?: LaunchIntent;
  popular?: true;
  // More words the search finds it by.
  words?: string;
}
// The popular ones come in EduPage's order; the labels follow its Russian wording where Atlas has the same function.
const ACTIONS: Spec[] = [
  {
    id: "compose",
    label: "Новое сообщение",
    category: "communication",
    via: ["notices"],
    intent: "compose",
    popular: true,
    words: "написать письмо",
  },
  {
    id: "newWork",
    label: "Новая оценка / работа",
    category: "grades",
    via: ["grades"],
    intent: "newWork",
    popular: true,
    words: "балл контрольная",
  },
  {
    id: "note",
    label: "Новая заметка",
    category: "grades",
    via: ["behaviour"],
    popular: true,
    words: "похвала замечание",
  },
  {
    id: "measure",
    label: "Воспитательная мера",
    category: "grades",
    via: ["behaviour"],
    intent: "measure",
    popular: true,
    words: "решение",
  },
  {
    id: "absences",
    label: "Записки об отсутствии",
    category: "attendance",
    via: ["absences", "class-staff"],
    popular: true,
    words: "объяснительная пропуск",
  },
  {
    id: "lessonTopic",
    label: "Тема урока и посещаемость",
    category: "journal",
    via: ["journal"],
    intent: "todayLesson",
    popular: true,
    words: "отметки присутствуют",
  },
  {
    id: "newTest",
    label: "Создать тест",
    category: "works",
    via: ["quizzes"],
    intent: "newTest",
    popular: true,
    words: "контрольная опрос",
  },
  {
    id: "homework",
    label: "Добавить домашнее задание",
    category: "works",
    via: ["journal"],
    intent: "homework",
    popular: true,
    words: "дз задали",
  },
  {
    id: "exam",
    label: "Запланировать контрольную",
    category: "works",
    via: ["assessments"],
    popular: true,
    words: "экзамен событие календарь",
  },
  { id: "dated", label: "Замены", category: "timetable", via: ["dated"] },
  {
    id: "schedule",
    label: "Расписание",
    category: "timetable",
    via: ["schedule"],
  },
  {
    id: "reports",
    label: "Итоговые оценки и табели",
    category: "grades",
    via: ["reports"],
    words: "ведомость",
  },
  {
    id: "attendance",
    label: "Посещаемость учащихся",
    category: "attendance",
    via: ["attendance"],
    words: "сетка",
  },
  {
    id: "coverage",
    label: "Заполнение журнала",
    category: "journal",
    via: ["coverage"],
    words: "контроль незаполненные",
  },
  {
    id: "curriculum",
    label: "Планы и заготовки",
    category: "plans",
    via: ["curriculum"],
    words: "тематический",
  },
  {
    id: "materials",
    label: "Учебные материалы",
    category: "plans",
    via: ["materials"],
    words: "файлы",
  },
  {
    id: "queue",
    label: "Проверить домашние задания",
    category: "works",
    via: ["homework"],
    words: "работы учеников сдали",
  },
  {
    id: "meetings",
    label: "Родительские собрания",
    category: "communication",
    via: ["meetings"],
    words: "консультации",
  },
  {
    id: "site",
    label: "Веб-страница школы",
    category: "site",
    via: ["site"],
    words: "сайт новости",
  },
];

// Only the people who work in the school start things from here; a family or a pupil never gets the button.
const STAFF: readonly Role[] = ["admin", "editor", "teacher", "staff"];

/** The actions the role can start, read off its menu. Empty for a family, a pupil, a viewer and an unknown role. */
export function launcherActions(
  ctx: MenuContext,
  menu: NavMenu,
): LauncherAction[] {
  if (ctx.role ? !STAFF.includes(ctx.role) : !ctx.local) return [];
  const items = menuItems(menu);
  return ACTIONS.flatMap((spec) => {
    const item = spec.via
      .map((id) => items.find((entry) => entry.id === id))
      .find(Boolean);
    return item
      ? [
          {
            id: spec.id,
            label: spec.label,
            category: spec.category,
            popular: !!spec.popular,
            item,
            intent: spec.intent,
            words: spec.words,
          },
        ]
      : [];
  });
}

const haystack = (action: LauncherAction) =>
  `${action.label} ${action.words ?? ""}`.toLocaleLowerCase("ru");

/** The actions that answer what was typed. Every word typed has to be found in some word of the action
 *  (its name or the words it is known by), whatever the case; nothing typed leaves all of them. */
export function filterActions(
  actions: LauncherAction[],
  query: string,
): LauncherAction[] {
  const typed = query.toLocaleLowerCase("ru").split(/\s+/).filter(Boolean);
  if (!typed.length) return actions;
  return actions.filter((action) => {
    const known = haystack(action)
      .split(/[\s/,;.]+/)
      .filter(Boolean);
    return typed.every((word) => known.some((one) => one.includes(word)));
  });
}

/** The list as it is shown, in the order of the categories. Nothing typed: the popular actions first, as EduPage
 *  lists them (they stay in their own category too). Something typed: the matches only, each once, in its category. */
export function launcherGroups(
  actions: LauncherAction[],
  query: string,
): LauncherGroup[] {
  const shown = filterActions(actions, query);
  const typed = query.trim() !== "";
  return CATEGORIES.flatMap(([id, label]) => {
    const found = shown.filter((action) =>
      id === POPULAR ? !typed && action.popular : action.category === id,
    );
    return found.length ? [{ id, label, actions: found }] : [];
  });
}

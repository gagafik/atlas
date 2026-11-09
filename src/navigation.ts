// The menu of the shell: what a role sees and where each entry leads. No React here, so the access
// rules run in node tests. Every condition below is the one the header buttons (AuthShell), the
// administrator's NAV (App) or the portal tabs (Portal, StaffWorkspace) had before the shell.
import type { Role } from "./auth";
import type { JournalPanelName, JournalView } from "./journal";
import { menuRoles } from "./assessmentCalendar";
import { sectionVisible } from "./schoolSections";

export type ModalId =
  | "forms"
  | "live"
  | "library"
  | "lostFound"
  | "behaviour"
  | "assessments"
  | "clubs"
  | "canteen"
  | "handover"
  | "payments"
  | "admissions"
  | "site"
  | "documents"
  | "staffTime"
  | "workload"
  | "registers"
  | "meetings"
  | "consultations"
  | "hall"
  | "sections"
  | "operations";
export type AccountId =
  "family" | "invitation" | "password" | "recovery" | "app" | "signOut";
export type Container = "app" | "portal" | "staff";
export type NavTarget =
  | { kind: "view"; view: string; journal?: JournalView }
  | { kind: "modal"; modal: ModalId }
  | { kind: "account"; account: AccountId };
export interface NavItem {
  id: string;
  label: string;
  icon: string;
  target: NavTarget;
}
export interface NavGroup {
  id: string;
  label: string;
  icon: string;
  items: NavItem[];
}
export type NavEntry = NavItem | NavGroup;
export type NavMenu = NavEntry[][];
export interface AccountItem extends NavItem {
  id: AccountId;
}
export const isGroup = (entry: NavEntry): entry is NavGroup => "items" in entry;

export interface MenuContext {
  role: Role | null;
  // Local mode: sign-in is not switched on and the one user works as the administrator.
  local: boolean;
  // null while the school setting loads: every optional section stays out of sight.
  hiddenSections: string[] | null;
  // Already true only for a pupil with a workspace and a school that lets pupils book.
  pupilConsultations: boolean;
  // The journal panels the server has anything for; null until journal/options arrived.
  journalPanels: JournalPanelName[] | null;
}

/** Which of the three screens renders the content of a role (mirrors AuthShell). */
export const containerOf = (role: Role | null): Container =>
  role === "staff"
    ? "staff"
    : role === null || role === "admin" || role === "editor"
      ? "app"
      : "portal";

export const targetKey = (target: NavTarget) =>
  target.kind === "view"
    ? `view:${target.view}${target.journal ? "#" + target.journal : ""}`
    : target.kind === "modal"
      ? `modal:${target.modal}`
      : `account:${target.account}`;

const view = (name: string, journal?: JournalView): NavTarget =>
  journal
    ? { kind: "view", view: name, journal }
    : { kind: "view", view: name };
const modal = (name: ModalId): NavTarget => ({ kind: "modal", modal: name });
const account = (name: AccountId): NavTarget => ({
  kind: "account",
  account: name,
});

const signed = (ctx: MenuContext, roles?: readonly Role[]) =>
  ctx.role !== null && (!roles || roles.includes(ctx.role));
const inApp = (ctx: MenuContext) => containerOf(ctx.role) === "app";
// App.tsx NAV filter: the editor does not get the journal, homework, quizzes, materials, plans or access.
const appFull = (ctx: MenuContext) =>
  inApp(ctx) && (ctx.local || ctx.role === "admin");
const visible = (ctx: MenuContext, section: string) =>
  sectionVisible(ctx.hiddenSections, section);
const LEARNING: Role[] = ["teacher", "student", "parent"];
const WORKERS: Role[] = ["admin", "editor", "teacher", "staff"];
const REGISTER_READERS: Role[] = [...WORKERS, "parent"];

interface Spec {
  label: string;
  icon: string;
  target: NavTarget;
  allow: (ctx: MenuContext, target: NavTarget) => boolean;
}
const page = (label: string, icon: string, name: string): Spec => ({
  label,
  icon,
  target: view(name),
  allow: inApp,
});
const dialog = (
  label: string,
  icon: string,
  id: ModalId,
  allow: Spec["allow"],
): Spec => ({ label, icon, target: modal(id), allow });
// A part of the journal opens only where the journal does and the server has something for the account.
const journalPart = (
  label: string,
  icon: string,
  panel: JournalPanelName,
): Spec => ({
  label,
  icon,
  target: view("journal", panel),
  allow: (ctx, target) =>
    ITEMS.journal.allow(ctx, target) &&
    target.kind === "view" &&
    !!target.journal &&
    !!ctx.journalPanels?.includes(target.journal as JournalPanelName),
});

const ITEMS: Record<string, Spec> = {
  home: {
    label: "Начало",
    icon: "home",
    target: view("home"),
    allow: () => true,
  },
  // App page messages, portal and staff messages: every container has it.
  notices: {
    label: "Уведомления",
    icon: "notices",
    target: view("messages"),
    allow: () => true,
  },
  // The staff workplace has no timetable (StaffWorkspace tabs).
  schedule: {
    label: "Расписание",
    icon: "schedule",
    target: view("schedule"),
    allow: (ctx) => containerOf(ctx.role) !== "staff",
  },
  planner: page("Рабочее место", "planner", "planner"),
  rules: page("Ограничения", "rules", "rules"),
  workloads: page("Учебная нагрузка", "workloads", "workloads"),
  courses: {
    label: "Выбор курсов",
    icon: "courses",
    target: view("courses"),
    allow: (ctx) => inApp(ctx) || signed(ctx, ["student", "parent"]),
  },
  assistant: page("AI-помощник", "assistant", "assistant"),
  calendar: page("Учебный календарь", "calendar", "calendar"),
  exchange: page("Обмен и печать", "exchange", "exchange"),
  publications: page("Публикация", "publications", "publications"),
  journal: {
    label: "Классный журнал",
    icon: "journal",
    target: view("journal"),
    allow: (ctx) => appFull(ctx) || signed(ctx, LEARNING),
  },
  dated: page("Замены", "dated", "dated"),
  commitments: page("Дежурства и бронирования", "commitments", "commitments"),
  grades: journalPart("Оценки", "grades", "courseTable"),
  // The screens of the family journal read the published records of the account's own children, which every
  // pupil and parent may do, so the server's panel flags do not gate them.
  pupilGrades: {
    label: "Оценки",
    icon: "grades",
    target: view("journal", "pupilGrades"),
    allow: (ctx) => signed(ctx, ["student", "parent"]),
  },
  pupilAttendance: {
    label: "Посещаемость",
    icon: "attendance",
    target: view("journal", "pupilAttendance"),
    allow: (ctx) => signed(ctx, ["student", "parent"]),
  },
  reports: journalPart("Табели", "reports", "finalGrades"),
  attendance: journalPart(
    "Посещаемость (учащиеся)",
    "attendance",
    "attendanceGrid",
  ),
  // The other panels the day grid of the journal used to list under itself: each opens alone, where the server offers it.
  absences: journalPart("Записки об отсутствии", "absences", "absences"),
  coverage: journalPart("Заполнение журнала", "coverage", "coverage"),
  periods: journalPart("Периоды журнала", "periods", "periods"),
  curriculum: {
    label: "Планы и заготовки",
    icon: "curriculum",
    target: view("curriculum"),
    allow: (ctx) => appFull(ctx) || signed(ctx, ["teacher"]),
  },
  materials: {
    label: "Учебные материалы",
    icon: "materials",
    target: view("materials"),
    allow: (ctx) => appFull(ctx) || signed(ctx, ["teacher"]),
  },
  homework: {
    label: "Домашние задания",
    icon: "homework",
    target: view("homework"),
    allow: (ctx) => appFull(ctx) || signed(ctx, LEARNING),
  },
  quizzes: {
    label: "Контрольные и тесты",
    icon: "quizzes",
    target: view("quizzes"),
    allow: (ctx) => appFull(ctx) || signed(ctx, LEARNING),
  },
  groups: page("Классы и группы", "groups", "groups"),
  teachers: page("Учителя", "teachers", "teachers"),
  rooms: page("Кабинеты", "rooms", "rooms"),
  subjects: page("Предметы", "subjects", "subjects"),
  access: {
    label: "Пользователи и доступ",
    icon: "access",
    target: view("access"),
    allow: appFull,
  },
  settings: page("Настройки", "settings", "settings"),
  "school-year": {
    label: "Новый учебный год",
    icon: "newYear",
    target: view("school-year"),
    allow: appFull,
  },
  "class-staff": {
    label: "Посещаемость учащихся",
    icon: "classStaff",
    target: view("class-staff"),
    allow: (ctx) => signed(ctx, ["teacher"]),
  },
  duties: {
    label: "Дежурства",
    icon: "duties",
    target: view("duties"),
    allow: (ctx) => signed(ctx, ["teacher"]),
  },
  // The modals open only for a signed-in user (AuthShell renders them with session.user).
  forms: dialog("Регистрация / Опросы", "forms", "forms", (ctx) => signed(ctx)),
  live: dialog("Интерактивные занятия", "live", "live", (ctx) =>
    signed(ctx, ["admin", "teacher", "student", "parent"]),
  ),
  library: dialog(
    "Библиотека",
    "library",
    "library",
    (ctx) => signed(ctx) && visible(ctx, "library"),
  ),
  lostFound: dialog(
    "Бюро находок",
    "lostFound",
    "lostFound",
    (ctx) => signed(ctx) && visible(ctx, "lost_found"),
  ),
  behaviour: dialog(
    "Официальные письма",
    "behaviour",
    "behaviour",
    (ctx) => signed(ctx) && ctx.role !== "editor",
  ),
  assessments: dialog(
    "События",
    "assessments",
    "assessments",
    (ctx) => ctx.role !== null && menuRoles.includes(ctx.role),
  ),
  clubs: dialog("Кружки", "clubs", "clubs", (ctx) => signed(ctx)),
  canteen: dialog(
    "Столовая",
    "canteen",
    "canteen",
    (ctx) => signed(ctx) && visible(ctx, "canteen"),
  ),
  handover: dialog(
    "Забрать из школы",
    "handover",
    "handover",
    (ctx) => signed(ctx) && ctx.role !== "student",
  ),
  payments: dialog(
    "Платежи",
    "payments",
    "payments",
    (ctx) => signed(ctx) && ctx.role !== "student" && visible(ctx, "payments"),
  ),
  admissions: dialog(
    "Приём в школу",
    "admissions",
    "admissions",
    (ctx) => signed(ctx, WORKERS) && visible(ctx, "admissions"),
  ),
  site: dialog(
    "Веб-страница",
    "site",
    "site",
    (ctx) => signed(ctx, WORKERS) && visible(ctx, "site"),
  ),
  documents: dialog("Документы", "documents", "documents", (ctx) =>
    signed(ctx),
  ),
  staffTime: dialog("Посещаемость учителей", "staffTime", "staffTime", (ctx) =>
    signed(ctx, WORKERS),
  ),
  workload: dialog("Нагрузка учителей", "workload", "workload", (ctx) =>
    signed(ctx, ["admin", "teacher"]),
  ),
  registers: dialog("Журналы регистрации", "registers", "registers", (ctx) =>
    signed(ctx, REGISTER_READERS),
  ),
  meetings: dialog("Родительские собрания", "meetings", "meetings", (ctx) =>
    signed(ctx, REGISTER_READERS),
  ),
  consultations: dialog(
    "Консультации",
    "consultations",
    "consultations",
    (ctx) => ctx.role === "student" && ctx.pupilConsultations,
  ),
  hall: dialog("Информационные экраны", "hall", "hall", (ctx) =>
    signed(ctx, ["admin"]),
  ),
  sections: dialog("Разделы школы", "sections", "sections", (ctx) =>
    signed(ctx, ["admin"]),
  ),
  operations: dialog("Состояние сервера", "operations", "operations", (ctx) =>
    signed(ctx, ["admin"]),
  ),
};

// A slot of a layout is an item id, or an item with another label or target for this role.
interface Placed {
  id: string;
  label?: string;
  target?: NavTarget;
}
type Slot = string | Placed;
interface GroupSlot {
  group: string;
  label: string;
  icon: string;
  items: Slot[];
}
type Layout = (Slot | GroupSlot)[][];
const slot = (id: string, label?: string, target?: NavTarget): Placed => ({
  id,
  label,
  target,
});
const group = (
  id: string,
  label: string,
  icon: string,
  items: Slot[],
): GroupSlot => ({ group: id, label, icon, items });
const OPTIONAL = ["payments", "library", "canteen", "lostFound"];

const LAYOUTS: Record<string, Layout> = {
  // Administrator, editor and local mode.
  app: [
    [
      "home",
      "notices",
      "site",
      group("timetables", "Расписание", "schedule", [
        "schedule",
        "planner",
        "rules",
        "workloads",
        "courses",
        "assistant",
        "calendar",
        "exchange",
        "publications",
      ]),
      "journal",
      "dated",
      "commitments",
      "assessments",
    ],
    [
      "grades",
      "reports",
      "curriculum",
      "materials",
      "homework",
      "quizzes",
      "live",
    ],
    [
      group("learning", "Обучение", "learning", [
        "attendance",
        "absences",
        "coverage",
        "staffTime",
        "workload",
      ]),
      group("documentation", "Документация", "documentation", [
        "behaviour",
        "documents",
      ]),
      group("communication", "Связь", "communication", [
        "meetings",
        "forms",
        "clubs",
        "handover",
        ...OPTIONAL,
      ]),
      group("agenda", "Повестка дня", "agenda", [
        "groups",
        "teachers",
        "rooms",
        "subjects",
        "registers",
      ]),
      group("control", "Центр управления", "control", [
        "access",
        "settings",
        "school-year",
        "periods",
        "sections",
        "hall",
        "operations",
        "admissions",
      ]),
    ],
  ],
  teacher: [
    [
      "home",
      "journal",
      "schedule",
      "assessments",
      "class-staff",
      slot("staffTime", "Моя посещаемость"),
      slot("workload", "Моя нагрузка"),
      "duties",
      "behaviour",
      "notices",
    ],
    [
      "meetings",
      "forms",
      "clubs",
      ...OPTIONAL,
      "admissions",
      "site",
      "handover",
      "documents",
      slot("registers", "Повестка дня"),
    ],
    [
      group("learning", "Обучение", "learning", [
        "grades",
        "reports",
        slot("attendance", "Сетка посещаемости"),
        "coverage",
        "periods",
        "curriculum",
        "materials",
        "homework",
        "quizzes",
        "live",
      ]),
    ],
  ],
  student: [
    [
      "home",
      "notices",
      "journal",
      "pupilGrades",
      "pupilAttendance",
      "reports",
      "schedule",
      slot("courses", "Учебный план"),
      "clubs",
    ],
    [
      group("learning", "Обучение", "learning", [
        "homework",
        "quizzes",
        "live",
      ]),
      "assessments",
      "consultations",
      "forms",
      "documents",
      "behaviour",
    ],
    ["library", "canteen", "lostFound"],
  ],
  parent: [
    [
      "home",
      "notices",
      "journal",
      "pupilGrades",
      "pupilAttendance",
      "reports",
      "schedule",
    ],
    [
      group("learning", "Обучение", "learning", [
        "homework",
        "quizzes",
        "live",
      ]),
      slot("courses", "Учебный план"),
      "clubs",
      "assessments",
      "meetings",
    ],
    [
      "behaviour",
      "handover",
      slot("registers", "Сообщения о происшествиях"),
      "forms",
      "documents",
      "payments",
      "canteen",
      "library",
      "lostFound",
    ],
  ],
  viewer: [
    ["home", "notices", "schedule"],
    ["behaviour", "forms", "clubs", "handover", "documents", ...OPTIONAL],
  ],
  // The workplace of a member of staff: its start page is the class workplace.
  staff: [
    [slot("home", "Начало", view("class-staff")), "notices"],
    [
      "assessments",
      slot("staffTime", "Моя посещаемость"),
      "behaviour",
      "meetings",
      "forms",
      "clubs",
      "handover",
      "documents",
      slot("registers", "Повестка дня"),
      ...OPTIONAL,
      "admissions",
      "site",
    ],
  ],
};

function resolve(entry: Slot, ctx: MenuContext): NavItem | null {
  const { id, label, target } = typeof entry === "string" ? slot(entry) : entry;
  const spec = ITEMS[id];
  const to = target ?? spec.target;
  return spec.allow(ctx, to)
    ? { id, label: label ?? spec.label, icon: spec.icon, target: to }
    : null;
}

/** The menu of a role: blocks of items and groups, empty groups and empty blocks left out. */
export function buildMenu(ctx: MenuContext): NavMenu {
  const layout = LAYOUTS[containerOf(ctx.role) === "app" ? "app" : ctx.role!];
  const items = (slots: Slot[]) =>
    slots.flatMap((entry) => resolve(entry, ctx) ?? []);
  return layout
    .map((block) =>
      block.flatMap((entry): NavEntry[] => {
        if (typeof entry !== "string" && "group" in entry) {
          const found = items(entry.items);
          return found.length
            ? [
                {
                  id: entry.group,
                  label: entry.label,
                  icon: entry.icon,
                  items: found,
                },
              ]
            : [];
        }
        return items([entry]);
      }),
    )
    .filter((block) => block.length);
}

/** The account menu in the top bar; the order is the order on screen. */
export function accountMenu(ctx: MenuContext): AccountItem[] {
  const entries: [AccountId, string, string, NavTarget, boolean][] = [
    [
      "family",
      "Мои дети и доступ",
      "family",
      view("family"),
      signed(ctx, ["parent"]),
    ],
    [
      "invitation",
      "Принять приглашение",
      "invitation",
      account("invitation"),
      signed(ctx, ["parent", "student", "teacher"]),
    ],
    ["password", "Пароль", "password", account("password"), signed(ctx)],
    [
      "recovery",
      "Резервные коды",
      "recovery",
      account("recovery"),
      signed(ctx),
    ],
    ["app", "Приложение", "app", account("app"), true],
    ["signOut", "Выйти", "signOut", account("signOut"), signed(ctx)],
  ];
  return entries
    .filter(([, , , , allowed]) => allowed)
    .map(([id, label, icon, target]) => ({ id, label, icon, target }));
}

export const menuItems = (menu: NavMenu): NavItem[] =>
  menu.flat().flatMap((entry) => (isGroup(entry) ? entry.items : [entry]));

export const groupOf = (menu: NavMenu, itemId: string): NavGroup | undefined =>
  menu
    .flat()
    .find(
      (entry): entry is NavGroup =>
        isGroup(entry) && entry.items.some((item) => item.id === itemId),
    );

/** The tile that takes the focus when an arrow key, Home or End is pressed in a flyout (-1: none has it yet); null for any other key. */
export function stepIndex(
  from: number,
  count: number,
  key: string,
): number | null {
  const forward = key === "ArrowDown" || key === "ArrowRight";
  const back = key === "ArrowUp" || key === "ArrowLeft";
  if (!count || !(forward || back || key === "Home" || key === "End"))
    return null;
  if (key === "Home" || (forward && from < 0)) return 0;
  if (key === "End" || (back && from < 0)) return count - 1;
  return (from + (forward ? 1 : -1) + count) % count;
}

// Whether the menu is folded to icons is remembered per browser; storage that is blocked or empty means expanded.
const RAIL_KEY = "atlas:menu-rail";
export function recallRail(storage?: Pick<Storage, "getItem">): boolean {
  try {
    return (storage ?? localStorage).getItem(RAIL_KEY) === "1";
  } catch {
    return false;
  }
}
export function rememberRail(
  folded: boolean,
  storage?: Pick<Storage, "setItem" | "removeItem">,
) {
  try {
    const store = storage ?? localStorage;
    if (folded) store.setItem(RAIL_KEY, "1");
    else store.removeItem(RAIL_KEY);
  } catch {
    // Not remembered; the menu is expanded again next time.
  }
}

/** The entry to highlight: the exact view and journal part, else the view itself. */
export function activeItem(
  menu: NavMenu,
  name: string,
  journal?: JournalView,
): NavItem | undefined {
  const same = menuItems(menu).filter(
    (item) => item.target.kind === "view" && item.target.view === name,
  );
  const part = (item: NavItem) =>
    item.target.kind === "view" ? item.target.journal : undefined;
  return (
    same.find((item) => part(item) === journal) ??
    same.find((item) => part(item) === undefined)
  );
}

/** The page title: the label of the entry on screen, else of the account entry, else the name of the page, else the product name. */
export function menuTitle(
  menu: NavMenu,
  accounts: AccountItem[],
  name: string,
  journal?: JournalView,
) {
  return (
    activeItem(menu, name, journal)?.label ??
    accounts.find(
      (item) => item.target.kind === "view" && item.target.view === name,
    )?.label ??
    viewLabel(name) ??
    "Атлас"
  );
}

/** The name of an App page as a colleague sees it in «Сейчас работают». */
export function viewLabel(name: string): string | undefined {
  const spec = name === "messages" ? ITEMS.notices : ITEMS[name];
  return spec?.target.kind === "view" && spec.target.view === name
    ? spec.label
    : undefined;
}

export const schoolYear = (start: string, end: string) =>
  `${start.slice(0, 4)} / ${end.slice(0, 4)}`;

/** What a screen does while a past school year is chosen: reads that year, stays as it is, or gives way to a notice. */
export type YearScreen = "shown" | "notice" | "same";
const YEARLESS_VIEWS = ["home", "messages", "access", "school-year"];
const YEARLESS_MODALS: ModalId[] = [
  "forms",
  "meetings",
  "consultations",
  "clubs",
  "handover",
  "documents",
  "registers",
  "live",
  "payments",
  "library",
  "canteen",
  "lostFound",
  "site",
  "admissions",
  "hall",
  "sections",
  "operations",
];
export function yearScreen(target: NavTarget, role: Role | null): YearScreen {
  if (target.kind === "account") return "same";
  if (target.kind === "modal")
    return YEARLESS_MODALS.includes(target.modal) ? "same" : "notice";
  if (YEARLESS_VIEWS.includes(target.view)) return "same";
  if (target.view !== "journal") return "notice";
  if (target.journal === "finalGrades") return "shown";
  const family = role === "student" || role === "parent";
  return family &&
    (target.journal === "pupilGrades" || target.journal === "pupilAttendance")
    ? "shown"
    : "notice";
}

// The tiles of the start page, in the order EduPage shows them; only those the menu has.
const TILES = [
  "schedule",
  "dated",
  "journal",
  "staffTime",
  "grades",
  "reports",
  "curriculum",
  "materials",
  "attendance",
  "forms",
  "homework",
  "quizzes",
  "meetings",
  "documents",
  "handover",
  "payments",
  "canteen",
  "settings",
];
export function startTiles(menu: NavMenu): NavItem[] {
  const items = menuItems(menu);
  return TILES.flatMap((id) => items.find((item) => item.id === id) ?? []);
}

/** The screen of the journal a pupil's or parent's notification opens: the day by default, the report card for a
 *  finals notice, the attendance for an absence note. Everyone else keeps the whole journal. */
export function eventPart(
  role: Role | null,
  type: string,
  detail: { finalsPeriodId?: string | null } = {},
): JournalView | undefined {
  if (role !== "student" && role !== "parent") return undefined;
  if (type === "atlas-open-journal" && detail.finalsPeriodId)
    return "finalGrades";
  return type === "atlas-open-absences" ? "pupilAttendance" : undefined;
}

/** Where an opened notification lands: event name to the view of the container that listens. */
export function eventViews(
  container: Container,
  role: Role | null,
): Record<string, string> {
  if (container === "app")
    return {
      "atlas-open-messages": "messages",
      "atlas-open-absences": "journal",
      "atlas-open-homework": "homework",
    };
  if (container === "staff")
    return {
      "atlas-open-messages": "messages",
      "atlas-open-absences": "class-staff",
    };
  return {
    "atlas-open-journal": "journal",
    "atlas-open-quizzes": "quizzes",
    "atlas-open-schedule": "schedule",
    "atlas-open-duties": "duties",
    "atlas-open-messages": "messages",
    "atlas-open-homework": "homework",
    // A teacher reviews notes in the class workplace; the administrator finds them under the journal.
    "atlas-open-absences": role === "teacher" ? "class-staff" : "journal",
  };
}

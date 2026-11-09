import { describe, it, expect } from "vitest";
import type { Role } from "./auth";
import type { JournalPanelName } from "./journal";
import {
  accountMenu,
  activeItem,
  buildMenu,
  containerOf,
  eventPart,
  eventViews,
  isGroup,
  menuItems,
  recallRail,
  rememberRail,
  startTiles,
  stepIndex,
  targetKey,
  type MenuContext,
} from "./navigation";

const ROLES: Role[] = [
  "admin",
  "editor",
  "teacher",
  "student",
  "parent",
  "viewer",
  "staff",
];
const PANELS: JournalPanelName[] = [
  "courseTable",
  "finalGrades",
  "absences",
  "periods",
  "coverage",
  "attendance",
  "attendanceGrid",
];
const OPTIONAL_SECTIONS = [
  "library",
  "lost_found",
  "canteen",
  "payments",
  "admissions",
  "site",
];

// Every optional section shown, a school that lets pupils book, every journal panel on offer.
function context(role: Role | null, extra: Partial<MenuContext> = {}) {
  return {
    role,
    local: role === null,
    hiddenSections: [],
    pupilConsultations: true,
    journalPanels: PANELS,
    ...extra,
  } satisfies MenuContext;
}
const keys = (ctx: MenuContext) => [
  ...menuItems(buildMenu(ctx)).map((item) => targetKey(item.target)),
  ...accountMenu(ctx).map((item) => targetKey(item.target)),
];
const plain = (ctx: MenuContext) =>
  keys(ctx).filter((key) => key !== "view:home" && !key.includes("#"));
const views = (ctx: MenuContext) =>
  keys(ctx).filter((key) => key.startsWith("view:"));

// What each role could open on main (8d82cad): App's NAV and its editor filter, the portal and
// staff tabs, the buttons of the header in AuthShell.
const APP_PAGES = [
  "schedule",
  "planner",
  "rules",
  "assistant",
  "courses",
  "workloads",
  "groups",
  "teachers",
  "rooms",
  "subjects",
  "calendar",
  "dated",
  "journal",
  "materials",
  "curriculum",
  "homework",
  "quizzes",
  "messages",
  "commitments",
  "exchange",
  "publications",
  "access",
  "settings",
];
const EDITOR_LACKS = [
  "access",
  "journal",
  "homework",
  "quizzes",
  "materials",
  "curriculum",
];
const PAGES: Record<string, string[]> = {
  admin: APP_PAGES,
  editor: APP_PAGES.filter((page) => !EDITOR_LACKS.includes(page)),
  local: APP_PAGES,
  teacher: [
    "class-staff",
    "schedule",
    "duties",
    "messages",
    "curriculum",
    "materials",
    "quizzes",
    "homework",
    "journal",
  ],
  student: [
    "schedule",
    "messages",
    "quizzes",
    "homework",
    "journal",
    "courses",
  ],
  parent: [
    "family",
    "schedule",
    "messages",
    "quizzes",
    "homework",
    "journal",
    "courses",
  ],
  viewer: ["schedule", "messages"],
  staff: ["class-staff", "messages"],
};
const BUTTONS: Record<string, Role[]> = {
  forms: ROLES,
  live: ["admin", "teacher", "student", "parent"],
  library: ROLES,
  lostFound: ROLES,
  behaviour: ROLES.filter((role) => role !== "editor"),
  assessments: ["admin", "editor", "teacher", "staff", "student", "parent"],
  clubs: ROLES,
  canteen: ROLES,
  handover: ROLES.filter((role) => role !== "student"),
  payments: ROLES.filter((role) => role !== "student"),
  admissions: ["admin", "editor", "teacher", "staff"],
  drill: ["admin"],
  briefings: ["admin"],
  logins: ["admin"],
  site: ["admin", "editor", "teacher", "staff"],
  documents: ROLES,
  staffTime: ["admin", "editor", "teacher", "staff"],
  workload: ["admin", "teacher"],
  registers: ["admin", "editor", "teacher", "staff", "parent"],
  meetings: ["admin", "editor", "teacher", "staff", "parent"],
  consultations: ["student"],
  hall: ["admin"],
  sections: ["admin"],
  operations: ["admin"],
  // The account controls
  invitation: ["parent", "student", "teacher"],
  password: ROLES,
  recovery: ROLES,
  signOut: ROLES,
};
const ACCOUNT = ["invitation", "password", "recovery", "signOut"];
function main(role: Role | "local") {
  const signedIn = role !== "local";
  return [
    ...PAGES[role].map((page) => `view:${page}`),
    ...Object.entries(BUTTONS)
      .filter(([, roles]) => signedIn && roles.includes(role))
      .map(([name]) =>
        ACCOUNT.includes(name) ? `account:${name}` : `modal:${name}`,
      ),
    "account:app",
  ].sort();
}

describe("the menu of each role", () => {
  it.each([...ROLES, "local"] as const)(
    "keeps everything %s could open before the shell",
    (role) => {
      const ctx = context(role === "local" ? null : role);
      expect(plain(ctx).sort()).toEqual(main(role));
    },
  );

  it("never offers a pupil, a parent or a viewer a page of the administration or the staff", () => {
    for (const role of ["student", "parent", "viewer"] as Role[]) {
      const ctx = context(role);
      expect(containerOf(role)).toBe("portal");
      const opened = keys(ctx);
      for (const page of [
        "rules",
        "planner",
        "access",
        "settings",
        "class-staff",
        "duties",
        "curriculum",
        "materials",
      ])
        expect(opened).not.toContain(`view:${page}`);
      for (const dialog of [
        "hall",
        "sections",
        "operations",
        "drill",
        "briefings",
        "logins",
        "staffTime",
        "workload",
        "admissions",
        "site",
      ])
        expect(opened).not.toContain(`modal:${dialog}`);
    }
  });

  it("leaves out the sections the school hides, and every optional one until the setting has loaded", () => {
    const optional = [
      "library",
      "lostFound",
      "canteen",
      "payments",
      "admissions",
      "site",
    ];
    for (const role of ROLES)
      for (const hidden of [OPTIONAL_SECTIONS, null]) {
        const opened = keys(context(role, { hiddenSections: hidden }));
        for (const dialog of optional)
          expect(opened).not.toContain(`modal:${dialog}`);
      }
    // One hidden section takes only its own entry with it.
    const some = keys(context("teacher", { hiddenSections: ["canteen"] }));
    expect(some).not.toContain("modal:canteen");
    expect(some).toContain("modal:library");
  });

  it("offers a pupil consultations only while the school lets pupils book", () => {
    expect(keys(context("student"))).toContain("modal:consultations");
    expect(
      keys(context("student", { pupilConsultations: false })),
    ).not.toContain("modal:consultations");
    // The flag alone does not open them to another role.
    expect(keys(context("parent"))).not.toContain("modal:consultations");
  });

  it("shows no folder and no block with nothing in it, whatever the school hides", () => {
    for (const role of [...ROLES, null])
      for (const hiddenSections of [[], OPTIONAL_SECTIONS, null])
        for (const journalPanels of [PANELS, [], null])
          for (const block of buildMenu(
            context(role, { hiddenSections, journalPanels }),
          )) {
            expect(block.length).toBeGreaterThan(0);
            for (const entry of block)
              if (isGroup(entry)) expect(entry.items.length).toBeGreaterThan(0);
          }
  });

  it("opens no two entries to the same screen", () => {
    for (const role of [...ROLES, null])
      for (const journalPanels of [PANELS, [], null]) {
        const opened = keys(context(role, { journalPanels }));
        expect(new Set(opened).size).toBe(opened.length);
      }
  });
});

describe("the parts of the journal in the menu", () => {
  const parts = (ctx: MenuContext) =>
    keys(ctx).filter((key) => key.includes("#"));

  it("appear only where the journal does and the server has something for the account", () => {
    // Every panel the day grid used to list under itself is a part of the menu now, in the group of its kind.
    expect(parts(context("admin"))).toEqual([
      "view:journal#courseTable",
      "view:journal#finalGrades",
      "view:journal#attendanceGrid",
      "view:journal#absences",
      "view:journal#coverage",
      "view:journal#periods",
    ]);
    expect(parts(context("admin", { journalPanels: null }))).toEqual([]);
    expect(parts(context("admin", { journalPanels: ["courseTable"] }))).toEqual(
      ["view:journal#courseTable"],
    );
    // A teacher without report cards keeps the grades; the absence notes of the school are the administrator's.
    expect(
      parts(context("teacher", { journalPanels: ["courseTable", "absences"] })),
    ).toEqual(["view:journal#courseTable"]);
    // A teacher reaches every other panel the server offers.
    expect(parts(context("teacher"))).toEqual([
      "view:journal#courseTable",
      "view:journal#finalGrades",
      "view:journal#attendanceGrid",
      "view:journal#coverage",
      "view:journal#periods",
    ]);
    // The editor has no journal, so none of its parts either.
    for (const role of ["editor", "viewer", "staff"] as Role[])
      expect(parts(context(role))).toEqual([]);
  });

  it("give a pupil and a parent the screens of their own journal, and the report cards once the school published them", () => {
    const own = ["view:journal#pupilGrades", "view:journal#pupilAttendance"];
    for (const role of ["student", "parent"] as Role[]) {
      expect(parts(context(role))).toEqual([
        ...own,
        "view:journal#finalGrades",
      ]);
      for (const journalPanels of [[], null])
        expect(parts(context(role, { journalPanels }))).toEqual(own);
      // The management parts of the staff stay out of a family's menu even when the server lists them.
      const family = parts(context(role)).join();
      for (const part of ["courseTable", "attendanceGrid", "absences"])
        expect(family).not.toContain(part);
    }
  });

  it("highlight their own entry, and the journal itself when the part is not in the menu", () => {
    const menu = buildMenu(context("admin"));
    const shown = (journal?: JournalPanelName) => {
      const item = activeItem(menu, "journal", journal);
      return item && targetKey(item.target);
    };
    expect(shown("finalGrades")).toBe("view:journal#finalGrades");
    expect(shown("periods")).toBe("view:journal#periods");
    expect(shown()).toBe("view:journal");
    // The summary of attendance is shown with the grid and has no entry of its own.
    expect(shown("attendance")).toBe("view:journal");
  });
});

describe("the flyout of a group", () => {
  const TILES = 4;
  const last = TILES - 1;

  it("moves the focus from tile to tile and round the ends", () => {
    for (const key of ["ArrowDown", "ArrowRight"]) {
      expect(stepIndex(0, TILES, key)).toBe(1);
      expect(stepIndex(last, TILES, key)).toBe(0);
    }
    for (const key of ["ArrowUp", "ArrowLeft"]) {
      expect(stepIndex(last, TILES, key)).toBe(last - 1);
      expect(stepIndex(0, TILES, key)).toBe(last);
    }
    // Home and End jump from where a single step would not reach the same tile.
    expect(stepIndex(last, TILES, "Home")).toBe(0);
    expect(stepIndex(1, TILES, "End")).toBe(last);
  });

  it("starts at an end when no tile has the focus, and leaves other keys alone", () => {
    expect(stepIndex(-1, TILES, "ArrowDown")).toBe(0);
    expect(stepIndex(-1, TILES, "ArrowUp")).toBe(last);
    for (const key of ["Tab", "Enter", " ", "a", "Escape"])
      expect(stepIndex(0, TILES, key)).toBeNull();
    expect(stepIndex(-1, 0, "ArrowDown")).toBeNull();
  });
});

describe("the menu folded to icons", () => {
  const memory = () => {
    const data = new Map<string, string>();
    return {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => void data.set(key, value),
      removeItem: (key: string) => void data.delete(key),
    };
  };
  // Storage that a private window or a blocked-cookies setting makes throw.
  const blocked = () => {
    const fail = () => {
      throw new DOMException("blocked", "SecurityError");
    };
    return { getItem: fail, setItem: fail, removeItem: fail };
  };

  it("is remembered, and expanded again once the choice is taken back", () => {
    const storage = memory();
    expect(recallRail(storage)).toBe(false);
    rememberRail(true, storage);
    expect(recallRail(storage)).toBe(true);
    rememberRail(false, storage);
    expect(recallRail(storage)).toBe(false);
  });

  it("falls back to the expanded menu when the storage cannot be used", () => {
    expect(recallRail(blocked())).toBe(false);
    expect(() => rememberRail(true, blocked())).not.toThrow();
  });
});

describe("the start page tiles", () => {
  it("are entries of the menu and never an administration screen", () => {
    for (const role of [...ROLES, null]) {
      const menu = buildMenu(context(role));
      const offered = menuItems(menu).map((item) => targetKey(item.target));
      const tiles = startTiles(menu).map((tile) => targetKey(tile.target));
      for (const tile of tiles) expect(offered).toContain(tile);
      for (const screen of [
        "view:publications",
        "modal:hall",
        "modal:operations",
      ])
        expect(tiles).not.toContain(screen);
    }
  });
});

describe("an opened notification", () => {
  it("lands on the screen that shows it", () => {
    expect(eventViews("app", "admin")["atlas-open-homework"]).toBe("homework");
    expect(eventViews("app", "admin")["atlas-open-absences"]).toBe("journal");
    expect(eventViews("portal", "teacher")["atlas-open-absences"]).toBe(
      "class-staff",
    );
    expect(eventViews("portal", "parent")["atlas-open-absences"]).toBe(
      "journal",
    );
    expect(eventViews("portal", "teacher")["atlas-open-duties"]).toBe("duties");
    expect(eventViews("staff", "staff")["atlas-open-messages"]).toBe(
      "messages",
    );
    expect(eventViews("staff", "staff")["atlas-open-absences"]).toBe(
      "class-staff",
    );
  });

  it("sends a pupil or a parent to the screen of the family journal that shows the notice", () => {
    expect(
      eventPart("parent", "atlas-open-journal", { finalsPeriodId: "p" }),
    ).toBe("finalGrades");
    expect(eventPart("student", "atlas-open-absences")).toBe("pupilAttendance");
    expect(eventPart("parent", "atlas-open-journal", {})).toBeUndefined();
    for (const type of ["atlas-open-absences", "atlas-open-journal"])
      expect(
        eventPart("teacher", type, { finalsPeriodId: "p" }),
      ).toBeUndefined();
  });

  it("never leaves a role on a screen its menu does not open", () => {
    for (const role of ROLES) {
      const reachable = new Set(PAGES[role].concat("home"));
      const opened = new Set(
        menuItems(buildMenu(context(role))).flatMap((item) =>
          item.target.kind === "view" ? [item.target.view] : [],
        ),
      );
      for (const view of Object.values(eventViews(containerOf(role), role)))
        // A role that never had the screen is not sent to a new one: the page falls back as before.
        if (reachable.has(view)) expect(opened).toContain(view);
    }
  });
});

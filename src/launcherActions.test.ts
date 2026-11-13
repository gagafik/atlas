import { describe, it, expect } from "vitest";
import type { Role } from "./auth";
import type { JournalPanelName } from "./journal";
import {
  filterActions,
  launcherActions,
  launcherGroups,
  type LauncherAction,
} from "./launcherActions";
import { buildMenu, menuItems, type MenuContext } from "./navigation";

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
const actionsOf = (ctx: MenuContext) => launcherActions(ctx, buildMenu(ctx));
const idsOf = (ctx: MenuContext) => actionsOf(ctx).map((a) => a.id);

describe("who gets the launcher", () => {
  it("is the people who work in the school", () => {
    for (const role of ["admin", "editor", "teacher", "staff"] as Role[])
      expect(actionsOf(context(role)).length, role).toBeGreaterThan(0);
    // Without sign-in the one user works as the administrator.
    expect(actionsOf(context(null)).length).toBeGreaterThan(0);
  });

  it("is never a family, a pupil or a viewer, however much their menu holds", () => {
    for (const role of ["student", "parent", "viewer"] as Role[])
      expect(actionsOf(context(role)), role).toEqual([]);
  });
});

describe("what the launcher lists", () => {
  it("opens only entries the menu of the same account has", () => {
    for (const role of [...ROLES, null])
      for (const journalPanels of [PANELS, [], null])
        for (const hiddenSections of [[], ["site"], null]) {
          const ctx = context(role, { journalPanels, hiddenSections });
          const offered = menuItems(buildMenu(ctx));
          for (const action of actionsOf(ctx))
            expect(offered, `${role}: ${action.id}`).toContainEqual(
              action.item,
            );
        }
  });

  it("leaves out an action whose part of the journal the server has nothing for", () => {
    expect(idsOf(context("teacher"))).toContain("newWork");
    expect(
      idsOf(context("teacher", { journalPanels: ["finalGrades"] })),
    ).not.toContain("newWork");
    // Until the server has answered nothing of the journal is offered.
    expect(idsOf(context("teacher", { journalPanels: null }))).not.toContain(
      "newWork",
    );
    // The lesson of today is in the journal itself, which does not wait for the answer.
    expect(idsOf(context("teacher", { journalPanels: null }))).toContain(
      "lessonTopic",
    );
  });

  it("leaves out an action whose section the school hides", () => {
    expect(idsOf(context("teacher"))).toContain("site");
    expect(
      idsOf(context("teacher", { hiddenSections: ["site"] })),
    ).not.toContain("site");
    expect(idsOf(context("teacher", { hiddenSections: null }))).not.toContain(
      "site",
    );
  });

  it("keeps an action to the rights of the role", () => {
    // The administrator opens the absence notes from the journal, a teacher from the class workplace.
    const absences = (role: Role) =>
      actionsOf(context(role)).find((a) => a.id === "absences")?.item.target;
    expect(absences("admin")).toEqual({
      kind: "view",
      view: "journal",
      journal: "absences",
    });
    expect(absences("teacher")).toEqual({ kind: "view", view: "class-staff" });
    // The editor has no journal, the homework queue or tests, so neither has the launcher an action for them.
    const editor = idsOf(context("editor"));
    for (const id of ["lessonTopic", "homework", "queue", "newTest", "newWork"])
      expect(editor).not.toContain(id);
  });

  it("asks the screen for its editor only where the action is more than the screen", () => {
    const byId = new Map(
      actionsOf(context("teacher")).map((a) => [a.id, a.intent]),
    );
    expect(byId.get("compose")).toBe("compose");
    expect(byId.get("newTest")).toBe("newTest");
    expect(byId.get("dated")).toBeUndefined();
    // The class workplace holds the absence notes among other sections: the action asks for them to be open.
    expect(byId.get("absences")).toBe("absences");
  });

  it("is named as the entry it opens whenever the menu has an entry of that name", () => {
    for (const role of ROLES)
      for (const journalPanels of [PANELS, []]) {
        const ctx = context(role, { journalPanels });
        const offered = menuItems(buildMenu(ctx));
        for (const action of actionsOf(ctx)) {
          const same = offered.filter((item) => item.label === action.label);
          if (same.length)
            expect(same, `${role}: ${action.label}`).toContainEqual(
              action.item,
            );
        }
      }
  });
});

describe("typing in the launcher", () => {
  const actions = actionsOf(context("teacher"));
  const ids = (list: LauncherAction[]) => list.map((a) => a.id);
  const wordsOf = (text: string) => text.match(/\p{L}+/gu) ?? [];

  it("keeps everything while nothing is typed", () => {
    expect(filterActions(actions, "")).toEqual(actions);
    expect(filterActions(actions, "   ")).toEqual(actions);
  });

  it("finds an action by the beginning of any word of it, whatever the case", () => {
    for (const action of actions)
      for (const word of wordsOf(action.label)) {
        expect(ids(filterActions(actions, word.toUpperCase()))).toContain(
          action.id,
        );
        expect(
          ids(filterActions(actions, word.slice(0, -1).toLowerCase())),
        ).toContain(action.id);
      }
  });

  it("finds an action by its whole name, a slash in it included", () => {
    for (const action of actions)
      expect(ids(filterActions(actions, action.label))).toContain(action.id);
  });

  it("does not take the middle of a word for the word", () => {
    // «дом» begins «Домашнее задание», but it only sits inside «ведомость», by which another action is known.
    const own = (id: string, label: string, words?: string): LauncherAction => ({
      id,
      label,
      category: "journal",
      popular: false,
      item: {} as LauncherAction["item"],
      words,
    });
    const pair = [
      own("homework", "Домашнее задание"),
      own("reports", "Итоговые оценки", "ведомость"),
    ];
    const found = ids(filterActions(pair, "дом"));
    expect(found).toContain("homework");
    expect(found).not.toContain("reports");
  });

  it("needs every typed word to be found", () => {
    const [action] = actions.filter((a) => wordsOf(a.label).length > 1);
    const [first, second] = wordsOf(action.label);
    expect(ids(filterActions(actions, `${first} ${second}`))).toContain(
      action.id,
    );
    expect(filterActions(actions, `${first} zzzz`)).toEqual([]);
  });

  it("also finds an action by a word it is known by", () => {
    const known = actions.filter((a) => a.words);
    expect(known.length).toBeGreaterThan(0);
    for (const action of known)
      expect(ids(filterActions(actions, wordsOf(action.words!)[0]!))).toContain(
        action.id,
      );
  });

  it("lists the popular ones first while nothing is typed and every action once when something is", () => {
    const empty = launcherGroups(actions, "");
    expect(empty[0].actions.length).toBeGreaterThan(0);
    expect(empty[0].actions.every((a) => a.popular)).toBe(true);
    // A popular action also stays in its own category.
    const popular = empty[0].actions[0];
    expect(empty.slice(1).flatMap((g) => g.actions.map((a) => a.id))).toContain(
      popular.id,
    );
    const typed = launcherGroups(actions, wordsOf(popular.label)[0]!);
    const listed = typed.flatMap((g) => ids(g.actions));
    expect(new Set(listed).size).toBe(listed.length);
    expect(listed).toContain(popular.id);
  });
});

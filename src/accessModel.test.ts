import { describe, expect, it } from "vitest";
import type { Account, Role } from "./auth";
import {
  USERS_PAGE,
  accessText,
  accountFormDirty,
  accountMatches,
  auditAction,
  auditDetails,
  auditMatches,
  auditStamp,
  blankForm,
  effectiveRole,
  filterAccounts,
  leaveDecision,
  pageOf,
  pendingTargets,
  roleOptions,
  savedRowOutcome,
  savedRowPlace,
  stateCounts,
  type AccessForm,
  type AuditEntry,
} from "./accessModel";

function account(over: Partial<Account> & { login: string }): Account {
  return {
    id: over.login,
    name: "Анна Примерова",
    role: "teacher",
    active: true,
    version: 1,
    workspaces: ["school"],
    grants: [],
    ...over,
  };
}
const people: Account[] = [
  account({ login: "admin1", name: "Дмитрий Образцов", role: "admin" }),
  account({ login: "anna.p", name: "Анна Примерова", role: "teacher" }),
  account({
    login: "maxim.v",
    name: "Максим Вымыслов",
    role: "teacher",
    active: false,
  }),
  account({
    login: "alina.s",
    name: "Алина Сочинова",
    role: "parent",
    grants: [{ workspace: "school", view: "student", entityIds: ["s1"] }],
  }),
];

describe("filters", () => {
  it("lists only the roles that exist, with counts that add up to the list", () => {
    const options = roleOptions(people);
    expect(options.map((o) => o.role).sort()).toEqual(
      ["admin", "parent", "teacher"].sort(),
    );
    expect(options.reduce((n, o) => n + o.count, 0)).toBe(people.length);
  });

  it("agrees between counts and what the filter returns", () => {
    const counts = stateCounts(people);
    expect(counts.all).toBe(people.length);
    expect(counts.active + counts.inactive).toBe(counts.all);
    const base = { role: "all" as const, query: "" };
    expect(filterAccounts(people, { ...base, state: "active" })).toHaveLength(
      counts.active,
    );
    expect(filterAccounts(people, { ...base, state: "inactive" })).toHaveLength(
      counts.inactive,
    );
    for (const o of roleOptions(people))
      expect(
        filterAccounts(people, { role: o.role, state: "all", query: "" }),
      ).toHaveLength(o.count);
  });

  it("counts roles under the state and the search, and states under the role and the search", () => {
    // Only the active accounts: the disabled teacher is not counted any more, the option stays.
    const active = roleOptions(people, { state: "active" });
    expect(active.find((o) => o.role === "teacher")!.count).toBe(1);
    expect(active.map((o) => o.role).sort()).toEqual(
      roleOptions(people)
        .map((o) => o.role)
        .sort(),
    );
    // A role with nothing left under the search still has its option, with no accounts.
    expect(
      roleOptions(people, { query: "алина" }).find((o) => o.role === "admin")!
        .count,
    ).toBe(0);
    // The numbers of the controls are what the table shows after pressing them.
    const teachers = stateCounts(people, { role: "teacher" });
    expect(teachers.all).toBe(
      filterAccounts(people, { role: "teacher", state: "all", query: "" })
        .length,
    );
    expect(teachers.inactive).toBe(
      filterAccounts(people, { role: "teacher", state: "inactive", query: "" })
        .length,
    );
    const searched = stateCounts(people, { query: "анна" });
    expect(searched.all).toBe(
      filterAccounts(people, { role: "all", state: "all", query: "анна" })
        .length,
    );
  });

  it("drops a role filter that nobody has any more", () => {
    expect(effectiveRole("parent", people)).toBe("parent");
    expect(effectiveRole("parent", people.slice(0, 3))).toBe("all");
    expect(effectiveRole("all", [])).toBe("all");
  });

  it("finds an account by a part of the name or of the login, ignoring case", () => {
    expect(accountMatches(people[1], "ПРИМЕР")).toBe(true);
    expect(accountMatches(people[1], "anna.")).toBe(true);
    expect(accountMatches(people[1], "максим")).toBe(false);
  });

  it("keeps the order of the server and combines the conditions", () => {
    const found = filterAccounts(people, {
      role: "teacher",
      state: "all",
      query: "",
    });
    expect(found.map((u) => u.login)).toEqual(["anna.p", "maxim.v"]);
    expect(
      filterAccounts(people, { role: "teacher", state: "active", query: "" }),
    ).toHaveLength(1);
    expect(
      filterAccounts(people, { role: "admin", state: "all", query: "анна" }),
    ).toHaveLength(0);
  });
});

describe("paging", () => {
  const many = Array.from({ length: USERS_PAGE * 2 + 5 }, (_, i) => i);
  it("shows the first rows and reports whether there are more", () => {
    const first = pageOf(many, USERS_PAGE);
    expect(first.rows).toHaveLength(USERS_PAGE);
    expect(first.total).toBe(many.length);
    expect(first.more).toBe(true);
  });
  it("grows and clamps at the end of the list", () => {
    const all = pageOf(many, USERS_PAGE * 10);
    expect(all.rows).toHaveLength(many.length);
    expect(all.shown).toBe(many.length);
    expect(all.more).toBe(false);
  });
  it("gives an empty page for an empty list", () => {
    expect(pageOf([], USERS_PAGE)).toMatchObject({ shown: 0, more: false });
  });
  it("after a save drops a role filter that is gone, so the saved account is visible and marked", () => {
    // The only parent was changed into a teacher and saved: the list no longer has a parent.
    const after = people.map((u) =>
      u.login === "alina.s" ? { ...u, role: "teacher" as Role } : u,
    );
    const out = savedRowOutcome(
      after,
      { role: "parent", state: "all", query: "" },
      "alina.s",
      USERS_PAGE,
    );
    expect(out.role).toBe("all");
    expect(out.hidden).toBe(false);
    expect(out.id).toBe("alina.s");
  });
  it("after a save reports an account that the filters hide, without changing them", () => {
    const out = savedRowOutcome(
      people,
      { role: "admin", state: "all", query: "" },
      "alina.s",
      USERS_PAGE,
    );
    expect(out.role).toBe("admin");
    expect(out.hidden).toBe(true);
    expect(out.id).toBeNull();
  });
  it("after a save widens the page to the saved account", () => {
    const list = Array.from({ length: USERS_PAGE * 3 }, (_, i) =>
      account({ login: `u${i}`, name: `Имя ${i}` }),
    );
    const login = "u" + (USERS_PAGE * 2 + 1);
    const out = savedRowOutcome(
      list,
      { role: "all", state: "all", query: "" },
      login,
      USERS_PAGE,
    );
    expect(out.hidden).toBe(false);
    expect(out.id).toBe(login);
    expect(out.shown).toBeGreaterThan(USERS_PAGE * 2 + 1);
  });
  it("widens the page so that a saved row far down becomes visible", () => {
    const list = Array.from({ length: USERS_PAGE * 3 }, (_, i) =>
      account({ login: `u${i}` }),
    );
    const place = savedRowPlace(list, "u" + (USERS_PAGE + 3), USERS_PAGE);
    expect(place.index).toBe(USERS_PAGE + 3);
    expect(place.shown).toBeGreaterThan(place.index);
    expect(savedRowPlace(list, "u1", USERS_PAGE).shown).toBe(USERS_PAGE);
    expect(savedRowPlace(list, "nobody", USERS_PAGE).index).toBe(-1);
  });
});

describe("accessText", () => {
  it("tells what the account reaches by its role", () => {
    const two = account({
      login: "x",
      role: "editor",
      workspaces: ["school", "demo"],
    });
    const one = account({ ...two, workspaces: ["school"] });
    // An administrator reaches both spaces without being listed; the others differ by what they were given.
    expect(accessText(people[0])).not.toBe(accessText(two));
    expect(accessText(two)).not.toBe(accessText(one));
    expect(accessText(one)).not.toBe("");
    const none = accessText(account({ login: "p", role: "parent" }));
    expect(accessText(people[3])).not.toBe(none);
    expect(accessText(people[3])).toContain("1");
  });
});

describe("account form dirty check", () => {
  const opened = (u: Account): AccessForm => ({ ...u, password: "" });
  const base = opened(people[3]);
  it("is clean for an account just opened", () => {
    expect(accountFormDirty({ ...base }, base, "", [])).toBe(false);
    expect(accountFormDirty(null, base, "", [])).toBe(false);
  });
  it("sees a changed field", () => {
    expect(
      accountFormDirty({ ...base, name: "Другое имя" }, base, "", []),
    ).toBe(true);
    expect(accountFormDirty({ ...base, active: false }, base, "", [])).toBe(
      true,
    );
    expect(
      accountFormDirty({ ...base, role: "viewer" as Role }, base, "", []),
    ).toBe(true);
    expect(accountFormDirty({ ...base, grants: [] }, base, "", [])).toBe(true);
  });
  it("sees a typed password or selected records", () => {
    expect(accountFormDirty({ ...base, password: "x" }, base, "", [])).toBe(
      true,
    );
    expect(accountFormDirty({ ...base }, base, "y", [])).toBe(true);
    expect(accountFormDirty({ ...base }, base, "", ["s2"])).toBe(true);
  });
  it("does not count the records of a grant that was only opened for a look", () => {
    const grant = base.grants[0];
    // «Посмотреть и изменить» puts the records of the grant into the selection.
    const viewed = pendingTargets(
      base,
      grant.workspace,
      grant.view,
      [...grant.entityIds].reverse(),
    );
    expect(viewed).toEqual([]);
    expect(accountFormDirty({ ...base }, base, "", viewed)).toBe(false);
    // Records added to it, or taken from it, are a change that is not in the form yet.
    const more = pendingTargets(base, grant.workspace, grant.view, [
      ...grant.entityIds,
      "s9",
    ]);
    expect(more.length).toBeGreaterThan(0);
    expect(accountFormDirty({ ...base }, base, "", more)).toBe(true);
    expect(pendingTargets(base, grant.workspace, grant.view, []).length).toBe(
      0,
    );
    // Records of a space or a view the account has no grant for are pending.
    expect(
      pendingTargets(base, "demo", grant.view, [...grant.entityIds]),
    ).toEqual(grant.entityIds);
    expect(pendingTargets(null, "school", "student", ["s1"])).toEqual(["s1"]);
  });
  it("does not mind the order of workspaces", () => {
    const two = opened(
      account({ login: "z", role: "editor", workspaces: ["school", "demo"] }),
    );
    expect(
      accountFormDirty({ ...two, workspaces: ["demo", "school"] }, two, "", []),
    ).toBe(false);
  });
  it("starts a new account clean", () => {
    const blank = blankForm("school");
    expect(accountFormDirty({ ...blank }, blank, "", [])).toBe(false);
    expect(accountFormDirty({ ...blank, login: "new" }, blank, "", [])).toBe(
      true,
    );
  });
});

describe("leaving the form", () => {
  it("asks only when the form holds changes", () => {
    expect(leaveDecision(false, false)).toBe("go");
    expect(leaveDecision(true, false)).toBe("ask");
  });
  it("does not let the form go while it is being saved", () => {
    expect(leaveDecision(false, true)).toBe("blocked");
    expect(leaveDecision(true, true)).toBe("blocked");
  });
});

describe("audit log", () => {
  const entry = (over: Partial<AuditEntry>): AuditEntry => ({
    id: 1,
    created: "2026-11-03T10:00:00Z",
    actorName: "Дмитрий Образцов",
    action: "account.signed_in",
    workspace: null,
    revision: null,
    detail: "",
    ...over,
  });
  it("names an action in Russian and keeps an unknown one as its code", () => {
    expect(auditAction(entry({}))).not.toBe("account.signed_in");
    expect(auditAction(entry({ action: "something.new" }))).toBe(
      "something.new",
    );
  });
  it("joins space, version and detail", () => {
    expect(auditDetails(entry({}))).toBe("");
    const text = auditDetails(
      entry({ workspace: "school", revision: 7, detail: "класс 5А" }),
    );
    expect(text).toContain("7");
    expect(text).toContain("класс 5А");
    // Each part is told apart from the others, and a part that is not there leaves no gap.
    const noRevision = auditDetails(
      entry({ workspace: "school", detail: "класс 5А" }),
    );
    expect(noRevision).toContain("класс 5А");
    expect(noRevision).not.toContain("7");
    expect(noRevision.length).toBeLessThan(text.length);
    expect(noRevision.trim()).toBe(noRevision);
  });
  it("writes the time of a local date as day.month.year hours:minutes:seconds", () => {
    const d = new Date(2026, 10, 3, 9, 5, 7);
    expect(auditStamp(d.toISOString())).toBe("03.11.2026 09:05:07");
  });
  it("searches by person, action and details", () => {
    const e = entry({ detail: "класс 5А", actorName: "Алина Сочинова" });
    expect(auditMatches(e, "сочинова")).toBe(true);
    expect(auditMatches(e, "5а")).toBe(true);
    expect(auditMatches(e, auditAction(e).slice(0, 4))).toBe(true);
    expect(auditMatches(e, "account.signed")).toBe(true);
    expect(auditMatches(e, "несуществующее")).toBe(false);
    expect(auditMatches(e, "  ")).toBe(true);
  });
});

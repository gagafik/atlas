import { describe, expect, it } from "vitest";
import {
  LOGIN_TABS,
  accountAfterTabChange,
  accountsForTab,
  exportQuery,
  listQuery,
  nextTabIndex,
  tabColumns,
  type LoginAccount,
  type LoginFilters,
} from "./loginJournalRules";

const people: LoginAccount[] = [
  { id: "a1", name: "Яров Борис", role: "teacher" },
  { id: "a2", name: "Алиева Мира", role: "teacher" },
  { id: "a3", name: "Сидоров Пётр", role: "student" },
  { id: "a4", name: "Орлова Анна", role: "parent" },
  { id: "a5", name: "Админов Иван", role: "admin" },
  { id: "a6", name: "Расписанов Лев", role: "editor" },
  { id: "a7", name: "Наблюдаев Олег", role: "viewer" },
  { id: "a8", name: "Секретарёва Нина", role: "staff" },
];
const base: LoginFilters = { tab: "", outcome: "", account: "", start: "", end: "" };
const get = (q: string, key: string) => new URLSearchParams(q).get(key);

describe("requests", () => {
  it("asks for everything with no parameters on the first tab", () => {
    expect(listQuery(base)).toBe("");
    expect(exportQuery(base)).toBe("");
  });
  it("sends the tab as the kind and keeps every other filter", () => {
    const f = { ...base, tab: "teacher" as const, outcome: "failed", account: "a1", start: "2026-10-01", end: "2026-10-31" };
    for (const q of [listQuery(f), exportQuery(f)]) {
      expect(get(q, "kind")).toBe("teacher");
      expect(get(q, "outcome")).toBe("failed");
      expect(get(q, "account")).toBe("a1");
      expect(get(q, "start")).toBe("2026-10-01");
      expect(get(q, "end")).toBe("2026-10-31");
    }
  });
  it("adds the cursor to the list but never to the export", () => {
    const f = { ...base, tab: "staff" as const };
    expect(get(listQuery(f, 42), "before")).toBe("42");
    expect(get(listQuery(f, null), "before")).toBeNull();
    expect(get(exportQuery(f), "before")).toBeNull();
  });
  it("drops a leftover account on the unknown-login tab", () => {
    expect(get(listQuery({ ...base, tab: "unknown", account: "a1" }), "account")).toBeNull();
  });
});

describe("accounts of a tab", () => {
  it("offers only the accounts of the kind, sorted by name", () => {
    expect(accountsForTab("teacher", people).map((a) => a.id)).toEqual(["a2", "a1"]);
    expect(accountsForTab("student", people).map((a) => a.id)).toEqual(["a3"]);
  });
  it("puts administrators, schedulers, observers and staff together on the staff tab", () => {
    expect(accountsForTab("staff", people).map((a) => a.id).sort()).toEqual(["a5", "a6", "a7", "a8"]);
  });
  it("offers everybody on the first tab and nobody on the unknown tab", () => {
    expect(accountsForTab("", people)).toHaveLength(people.length);
    expect(accountsForTab("unknown", people)).toEqual([]);
  });
  it("does not reorder the list it was given", () => {
    const copy = [...people];
    accountsForTab("", people);
    expect(people).toEqual(copy);
  });
});

describe("account after a tab change", () => {
  it("keeps an account that belongs to the new tab", () => {
    expect(accountAfterTabChange("a1", "teacher", people)).toBe("a1");
    expect(accountAfterTabChange("a1", "", people)).toBe("a1");
    expect(accountAfterTabChange("a6", "staff", people)).toBe("a6");
  });
  it("resets an account that does not", () => {
    expect(accountAfterTabChange("a1", "student", people)).toBe("");
    expect(accountAfterTabChange("a1", "unknown", people)).toBe("");
  });
  it("resets an account the list does not know", () => {
    expect(accountAfterTabChange("gone", "", people)).toBe("");
    expect(accountAfterTabChange("", "teacher", people)).toBe("");
  });
});

describe("columns", () => {
  it("shows the role only where a tab mixes roles", () => {
    expect(tabColumns("").role).toBe(true);
    expect(tabColumns("staff").role).toBe(true);
    for (const t of ["teacher", "student", "parent", "unknown"] as const) expect(tabColumns(t).role).toBe(false);
  });
  it("hides the account only on the unknown-login tab", () => {
    for (const t of LOGIN_TABS) expect(tabColumns(t.id).account).toBe(t.id !== "unknown");
  });
});

describe("tabs by keyboard", () => {
  it("moves with the arrows and wraps around", () => {
    expect(nextTabIndex(0, "ArrowRight")).toBe(1);
    expect(nextTabIndex(LOGIN_TABS.length - 1, "ArrowRight")).toBe(0);
    expect(nextTabIndex(0, "ArrowLeft")).toBe(LOGIN_TABS.length - 1);
    expect(nextTabIndex(2, "Home")).toBe(0);
    expect(nextTabIndex(2, "End")).toBe(LOGIN_TABS.length - 1);
  });
  it("ignores other keys", () => {
    expect(nextTabIndex(2, "a")).toBeNull();
  });
});

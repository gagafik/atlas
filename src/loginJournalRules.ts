// Rules of the login journal that need no browser: tabs, columns, requests, the account list.

export type LoginTab = "" | "teacher" | "student" | "parent" | "staff" | "unknown";

export const LOGIN_TABS: { id: LoginTab; title: string }[] = [
  { id: "", title: "Все" },
  { id: "teacher", title: "Учителя" },
  { id: "student", title: "Ученики" },
  { id: "parent", title: "Родители" },
  { id: "staff", title: "Администрация" },
  { id: "unknown", title: "Неизвестный логин" },
];

export interface LoginFilters {
  tab: LoginTab;
  outcome: string;
  account: string;
  start: string;
  end: string;
}

export interface LoginAccount {
  id: string;
  name: string;
  role: string;
}

const STAFF_ROLES = ["admin", "editor", "viewer", "staff"];

// Which columns a tab shows. The role says something only where a tab mixes roles.
export const tabColumns = (tab: LoginTab) => ({
  account: tab !== "unknown",
  role: tab === "" || tab === "staff",
});

const belongs = (tab: LoginTab, role: string) =>
  tab === ""
    ? true
    : tab === "unknown"
      ? false
      : tab === "staff"
        ? STAFF_ROLES.includes(role)
        : role === tab;

// Accounts the select offers on a tab, sorted by name.
export const accountsForTab = (tab: LoginTab, all: LoginAccount[]) =>
  all
    .filter((a) => belongs(tab, a.role))
    .sort((a, b) => a.name.localeCompare(b.name, "ru"));

// A chosen account stays only when it is on the new tab; otherwise the filter returns to «Все».
export const accountAfterTabChange = (
  account: string,
  tab: LoginTab,
  all: LoginAccount[],
) =>
  account && all.some((a) => a.id === account && belongs(tab, a.role))
    ? account
    : "";

const params = (f: LoginFilters) => {
  const p = new URLSearchParams();
  if (f.tab) p.set("kind", f.tab);
  if (f.outcome) p.set("outcome", f.outcome);
  if (f.account && f.tab !== "unknown") p.set("account", f.account);
  if (f.start) p.set("start", f.start);
  if (f.end) p.set("end", f.end);
  return p;
};

export const listQuery = (f: LoginFilters, before?: number | null) => {
  const p = params(f);
  if (before != null) p.set("before", String(before));
  return p.toString();
};

export const exportQuery = (f: LoginFilters) => params(f).toString();

// Moving between tabs with the arrow keys; Home and End jump to the ends.
export const nextTabIndex = (index: number, key: string, count = LOGIN_TABS.length) =>
  key === "ArrowRight"
    ? (index + 1) % count
    : key === "ArrowLeft"
      ? (index - 1 + count) % count
      : key === "Home"
        ? 0
        : key === "End"
          ? count - 1
          : null;

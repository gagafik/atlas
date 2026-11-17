// Pure rules of the «Семьи» tab of «Пользователи и доступ»: the state filter of the registry, the chips and the term of a link,
// whether the opened link holds changes and where a saved link is after the registry was reloaded.

export const relationships: Record<string, string> = {
  unspecified: "Не уточнено",
  mother: "Мать",
  father: "Отец",
  guardian: "Опекун",
  representative: "Другой представитель",
};
export const activities: Record<string, string> = {
  active: "Доступ действует",
  scheduled: "Доступ ещё не начался",
  expired: "Срок доступа истёк",
  revoked: "Доступ отозван",
};

export type FamilyLink = {
  parentId: string;
  studentId: string;
  studentName: string;
  parentName: string;
  version: number;
  accountVersion: number;
  relationship: string;
  verified: boolean;
  evidence?: string;
  reason?: string;
  startDate: string | null;
  endDate: string | null;
  status: string;
  activity: string;
  legacy: boolean;
  accountActive: boolean;
  workspace?: string;
};

export type LinkFilter = "all" | "unclear" | "inactive";

export const linkKey = (r: Pick<FamilyLink, "parentId" | "studentId">) =>
  `${r.parentId}:${r.studentId}`;

/** The school has not verified the representative, or the link is an old assignment nobody has looked at. */
export const needsClarifying = (l: FamilyLink) => !l.verified || l.legacy;
/** The child's cabinet is not open to this parent now: the access is not in force, or the account is switched off. */
export const accessInactive = (l: FamilyLink) =>
  l.activity !== "active" || !l.accountActive;

export function filterLinks(rows: FamilyLink[], filter: LinkFilter) {
  if (filter === "unclear") return rows.filter(needsClarifying);
  if (filter === "inactive") return rows.filter(accessInactive);
  return rows;
}
/** How many of the loaded rows each filter would show. */
export function linkCounts(rows: FamilyLink[]): Record<LinkFilter, number> {
  return {
    all: rows.length,
    unclear: rows.filter(needsClarifying).length,
    inactive: rows.filter(accessInactive).length,
  };
}

export type ChipTone = "ok" | "warn" | "danger" | "off";
export type Chip = { text: string; title?: string; tone: ChipTone };

const accessTone: Record<string, ChipTone> = {
  active: "ok",
  scheduled: "warn",
  expired: "off",
  revoked: "danger",
};

/** The chips of a row: the access, the check by the school and, when the account is switched off, a third one. */
export function linkChips(l: FamilyLink): {
  access: Chip;
  check: Chip;
  account: Chip | null;
} {
  return {
    access: {
      text: activities[l.activity] ?? l.activity,
      tone: accessTone[l.activity] ?? "off",
    },
    check: needsClarifying(l)
      ? {
          text: "Требует уточнения",
          title: l.legacy
            ? "Старое назначение: полномочия требуют уточнения"
            : "Полномочия требуют уточнения",
          tone: "warn",
        }
      : {
          text: "Проверено",
          title: "Сведения проверены школой",
          tone: "ok",
        },
    account: l.accountActive
      ? null
      : {
          text: "Аккаунт неактивен",
          title: "Аккаунт отключён или сменил роль",
          tone: "off",
        },
  };
}

/** 2026-09-01 as 01.09.2026; anything that is not a date stays as it came. */
export function formatDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : iso;
}

/** The term of the access for the table cell and the words for its title. */
export function termText(l: Pick<FamilyLink, "startDate" | "endDate">): {
  text: string;
  title: string;
} {
  const start = l.startDate ? formatDay(l.startDate) : null,
    end = l.endDate ? formatDay(l.endDate) : null;
  return {
    text: start || end ? `${start ?? "—"} — ${end ?? "—"}` : "—",
    title: `${start ? `С ${start}` : "Без даты начала"} · ${
      end ? `по ${end} включительно` : "Без даты окончания"
    }`,
  };
}

const same = (a: FamilyLink, b: FamilyLink) =>
  a.relationship === b.relationship &&
  a.verified === b.verified &&
  (a.evidence || "") === (b.evidence || "") &&
  (a.reason || "") === (b.reason || "") &&
  (a.startDate || "") === (b.startDate || "") &&
  (a.endDate || "") === (b.endDate || "") &&
  a.status === b.status;

/** True when the fields of the opened link differ from the link as it was opened or last saved. */
export function linkFormDirty(
  form: FamilyLink | null,
  base: FamilyLink | null,
): boolean {
  if (!form || !base) return false;
  return !same(form, base);
}

/**
 * Where a link is after the registry was reloaded: in the rows that are shown, hidden by the state filter, or not among
 * the loaded rows at all (the search or the server's page does not bring it).
 */
export function savedLinkOutcome(
  loaded: FamilyLink[],
  filter: LinkFilter,
  key: string,
): "visible" | "filtered" | "absent" {
  if (!loaded.some((l) => linkKey(l) === key)) return "absent";
  return filterLinks(loaded, filter).some((l) => linkKey(l) === key)
    ? "visible"
    : "filtered";
}

/** The count of a filter button: only the loaded pages are counted, so a count with more pages to come is a lower bound. */
export const countLabel = (count: number, more: boolean) =>
  more ? `${count}+` : String(count);

/**
 * What the table says instead of rows: the registry has not arrived, did not load, is empty, or holds links that the state
 * filter hides (then, when the server has more pages, the answer is not final).
 */
export function emptyState(
  status: "loading" | "ready" | "failed",
  loaded: number,
  shown: number,
  more: boolean,
): "loading" | "failed" | "empty" | "filtered" | "filtered-more" | null {
  if (shown > 0) return null;
  if (status !== "ready") return status;
  if (loaded === 0) return "empty";
  return more ? "filtered-more" : "filtered";
}

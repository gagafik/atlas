import { useEffect, useState } from "react";
import { api } from "./api";
import type { JournalBooking } from "./journal";

export type Category = {
  id: string;
  name: string;
  title: string;
  color: string;
  calendar: "" | "summative_unit" | "summative_term";
  active: boolean;
};
export type Share = { categoryId: string; percent: string | number };
export type Policy = {
  shares: Share[];
  method: "average" | "points";
  missing: "renormalize" | "block";
};
export type Rule = Policy & {
  id: string;
  name: string;
  subjectIds: string[];
  classIds: string[];
};
// Everyone gets the catalog; staff also get the policy, and the admin the lists the rules refer to.
export type CategoryConfig = {
  version: number;
  categories: Category[];
  defaultCategory: string;
  policy?: Policy;
  rules?: Rule[];
  canEdit?: boolean;
  subjects?: { id: string; name: string }[];
  classes?: { id: string; name: string }[];
};
export type Draft = {
  categories: Category[];
  defaultCategory: string;
  policy: Policy;
  rules: Rule[];
};

const GREY = "#6b7280";
// Works saved before categories existed carry none and count as ФО.
export const LEGACY = "fo";
export const FALLBACK: Category = {
  id: LEGACY,
  name: "ФО",
  title: "Формативное оценивание",
  color: "#2563eb",
  calendar: "",
  active: true,
};
export const CALENDAR_TYPES = [
  { value: "", label: "—" },
  { value: "summative_unit", label: "СОР календаря" },
  { value: "summative_term", label: "СОЧ календаря" },
];
export const METHODS = [
  { value: "average", label: "Среднее процентов с весом" },
  { value: "points", label: "Сумма баллов" },
];
export const MISSING = [
  { value: "renormalize", label: "Пересчитать доли без пустых категорий" },
  { value: "block", label: "Не рекомендовать без всех категорий" },
];

export function categoryOf(
  a: { category?: string },
  config?: { categories: Category[] } | null,
): Category {
  const id = a.category || LEGACY;
  const found = config?.categories.find((c) => c.id === id);
  if (found) return found;
  return id === LEGACY
    ? FALLBACK
    : { id, name: id, title: id, color: GREY, calendar: "", active: true };
}

// The weight presets of the journal editor. The server accepts 0..100; 0 leaves the work out of the calculation.
export const WEIGHT_PRESETS = [
  { value: 0, label: "0x — не входит в расчёт" },
  { value: 0.25, label: "0,25x" },
  { value: 0.5, label: "0,5x" },
  { value: 1, label: "1x" },
  { value: 2, label: "2x" },
  { value: 3, label: "3x" },
];
export function presetOf(weight: number): number | "custom" {
  return WEIGHT_PRESETS.find((p) => p.value === weight)?.value ?? "custom";
}

// Black or white text, whichever reads better on the category colour (WCAG contrast).
export function textColor(hex: string): string {
  const linear = (i: number) => {
    const v = parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const light = 0.2126 * linear(0) + 0.7152 * linear(1) + 0.0722 * linear(2);
  // #111 has relative luminance of about 0.0056.
  return (light + 0.05) / 0.0556 > 1.05 / (light + 0.05) ? "#111" : "#fff";
}

const SHARE_TEXT = /^\d{0,3}(\.\d{1,2})?$/;
const cents = (percent: string | number) =>
  String(percent).trim() === ""
    ? Number.NaN
    : Math.round(Number(percent) * 100);

function policyProblem(p: Policy, ids: Set<string>, where: string): string {
  if (!p.shares.length) return `${where}: добавьте хотя бы одну долю.`;
  const used = new Set<string>();
  let sum = 0;
  for (const s of p.shares) {
    const value = cents(s.percent);
    if (!Number.isFinite(value) || value < 0 || value > 10000)
      return `${where}: доля — число от 0 до 100.`;
    // The server keeps hundredths; cents() would round 50.004 away and the save would fail with a 422.
    if (!SHARE_TEXT.test(String(s.percent).trim()))
      return `${where}: доля — не более двух знаков после запятой.`;
    if (!ids.has(s.categoryId))
      return `${where}: доля ссылается на неизвестную категорию.`;
    if (used.has(s.categoryId))
      return `${where}: категория указана в долях несколько раз.`;
    used.add(s.categoryId);
    sum += value;
  }
  return sum === 10000
    ? ""
    : `${where}: доли категорий должны в сумме давать 100 % (сейчас ${sum / 100} %).`;
}

// Only a stale version (409) means the editor must show the server copy; any other failed save keeps the admin's draft.
export const staleVersion = (error: unknown) =>
  (error as { status?: number } | null)?.status === 409;

// First Russian problem the server would reject, or "" when the configuration may be sent.
export function configProblem(cfg: Draft): string {
  const ids = new Set(cfg.categories.map((c) => c.id));
  if (cfg.categories.some((c) => !c.name.trim() || !c.title.trim()))
    return "Укажите название и описание каждой категории.";
  if (ids.size !== cfg.categories.length)
    return "Коды категорий не должны повторяться.";
  if (
    new Set(cfg.categories.map((c) => c.name.trim().toLowerCase())).size !==
    cfg.categories.length
  )
    return "Названия категорий не должны повторяться.";
  if (!cfg.categories.some((c) => c.id === cfg.defaultCategory && c.active))
    return "Категория по умолчанию должна существовать и быть действующей.";
  const linked = cfg.categories.filter((c) => c.active && c.calendar);
  if (new Set(linked.map((c) => c.calendar)).size !== linked.length)
    return "Тип календаря связан только с одной категорией.";
  const base = policyProblem(cfg.policy, ids, "Правило школы");
  if (base) return base;
  for (const r of cfg.rules) {
    if (!r.name.trim()) return "Укажите название каждого правила.";
    const problem = policyProblem(r, ids, `Правило «${r.name}»`);
    if (problem) return problem;
  }
  return "";
}

// A work entered from a calendar booking takes the booking's topic and the mapped category.
export function bookingDefault(booking: JournalBooking) {
  return {
    id: crypto.randomUUID(),
    name: (booking.topic || booking.type).slice(0, 100),
    kind: "points" as const,
    maxScore: 10,
    weight: 1,
    category: booking.journalCategory,
    bookingId: booking.id,
  };
}

// The catalog of a workspace, or null while it loads or when the account may not read it (badges then fall back to ФО/ids).
// A changed epoch loads it again, for example after the admin saved the catalog.
export function useAssessmentCategories(
  workspace: string,
  epoch = 0,
): CategoryConfig | null {
  const [state, setState] = useState<{
    workspace: string;
    config: CategoryConfig;
  } | null>(null);
  useEffect(() => {
    let live = true;
    api("journal/categories", workspace)
      .then((config: CategoryConfig) => {
        if (live) setState({ workspace, config });
      })
      .catch(() => {
        if (live) setState(null);
      });
    return () => {
      live = false;
    };
  }, [workspace, epoch]);
  return state?.workspace === workspace ? state.config : null;
}

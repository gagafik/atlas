import { wordForm } from "./teachingLoad";

/** «2026-10-05» → «05.10.2026»; anything that is not a plain ISO date comes back unchanged. */
export function formatSchoolDate(value: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return value;
  const [, y, mo, d] = m;
  const date = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)));
  const real =
    date.getUTCFullYear() === Number(y) &&
    date.getUTCMonth() === Number(mo) - 1 &&
    date.getUTCDate() === Number(d);
  return real ? `${d}.${mo}.${y}` : value;
}

export function schoolYearText(start: string, end: string) {
  return `${formatSchoolDate(start)} — ${formatSchoolDate(end)}`;
}

/** «5 дней · 2 смены» with word forms that follow the numbers. */
export function weekShape(days: number, shifts: number) {
  return `${days} ${wordForm(days, "день", "дня", "дней")} · ${shifts} ${wordForm(shifts, "смена", "смены", "смен")}`;
}

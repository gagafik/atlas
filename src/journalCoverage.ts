/** Pure helpers of the journal coverage panel (class summary and reminder settings). */

export const weekdays = [
  "Понедельник",
  "Вторник",
  "Среда",
  "Четверг",
  "Пятница",
  "Суббота",
  "Воскресенье",
];

/** «1 учебный день», «2 учебных дня», «5 учебных дней», «21 учебный день». */
export function schoolDays(n: number): string {
  const last = n % 10,
    tail = n % 100;
  const word =
    last === 1 && tail !== 11
      ? "учебный день"
      : last >= 2 && last <= 4 && (tail < 12 || tail > 14)
        ? "учебных дня"
        : "учебных дней";
  return `${n} ${word}`;
}

/** An ISO date as dd.mm.yyyy, the way the school writes dates. */
export function ruDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${d}.${m}.${y}` : iso;
}

export type Query = {
  start: string;
  end: string;
  mode: string;
  classId: string;
  teacherId: string;
  subjectId: string;
  status: string;
};

/** The filters of the class summary: it counts the lessons of every teacher in the class, whatever the form holds. */
export function summaryFilters(q: Query): Query {
  return { ...q, mode: "classes", classId: "", teacherId: "", status: "all" };
}

/**
 * The filters of «Уроки класса»: the summary's own filters narrowed to one class and its issues,
 * so the list shows the lessons the card counted and no teacher picked in the form can shorten it.
 */
export function drillFilters(q: Query, classId: string): Query {
  return { ...summaryFilters(q), classId, status: "issues" };
}

export type ClassSummary = {
  classId: string;
  className: string;
  classTeacher: string | null;
  expected: number;
  unfilled: number;
  rosterUnknown: number;
  lastRecord: string | null;
  unfilledSince: string | null;
  unbound: number;
  // Daily attendance confirmation, read live by the server; null while nothing is owed (never started, reminders off).
  expectedDays?: number;
  confirmedDays?: number;
  changedDays?: number;
  pendingDays?: number | null;
  pendingSince?: string | null;
};

/** The lines of one class card, in reading order; empty problems are said in words, never left blank. */
export function classCardLines(c: ClassSummary): string[] {
  return [
    `Классный руководитель: ${c.classTeacher ?? "не назначен"}`,
    c.lastRecord
      ? `Последняя запись: ${ruDate(c.lastRecord)}`
      : "Записей за период нет",
    c.unfilledSince
      ? `Не заполнено с: ${ruDate(c.unfilledSince)}`
      : "Незаполненных уроков нет",
    `Не заполнено уроков: ${c.unfilled} из ${c.expected}`,
    ...(c.unbound > 0
      ? [`Некому заполнить (у учителя нет учётной записи): ${c.unbound}`]
      : []),
    ...(c.rosterUnknown > 0
      ? [`Численность класса не подтверждена: ${c.rosterUnknown}`]
      : []),
    ...(c.pendingDays
      ? [
          `Посещаемость ждёт подтверждения: ${c.pendingDays} дн.` +
            (c.pendingSince ? `, с ${ruDate(c.pendingSince)}` : ""),
        ]
      : []),
  ];
}

export type Run = {
  kind: string;
  onDate: string;
  detail: {
    note?: string;
    sent?: number;
    skipped?: number;
    lessons?: number;
    withoutAccount?: number;
    days?: number;
  };
};

/** One line about a daily run of the reminder worker. */
export function runLine(run: Run): string {
  const confirmations = run.kind === "confirmations";
  const name = confirmations
    ? "Напоминания о подтверждении посещаемости"
    : run.kind === "digest"
      ? "Недельная сводка"
      : "Напоминания по урокам";
  const d = run.detail;
  const result = d.note
    ? "не учебный день, напоминания перенесены"
    : [
        confirmations ? `дней: ${d.days ?? 0}` : `уроков: ${d.lessons ?? 0}`,
        `уведомлений: ${d.sent ?? 0}`,
        ...(d.skipped ? [`без учётной записи: ${d.skipped}`] : []),
        ...(d.withoutAccount
          ? [`учителей без учётной записи: ${d.withoutAccount}`]
          : []),
      ].join(", ");
  return `${ruDate(run.onDate)} · ${name}: ${result}`;
}

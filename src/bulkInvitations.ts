import { csvCell } from "./utils";

export type InviteRole = "parent" | "student" | "teacher";
export interface BatchRow {
  key: string;
  recipient: string;
  role: InviteRole;
  entityIds: string[];
}
export interface BatchTarget {
  id: string;
  name: string;
  className: string;
}
export interface BatchWarning {
  kind: "accounts" | "invitations";
  count: number;
  names: string[];
}
export interface ReviewedRow {
  key: string;
  recipient: string;
  role: InviteRole;
  targets: BatchTarget[];
  warnings: BatchWarning[];
}
export interface BatchPreview {
  previewToken: string;
  items: ReviewedRow[];
}
export interface BatchDraft {
  workspace: string;
  days: number;
  rows: BatchRow[];
}
export interface BatchIssue extends BatchDraft {
  requestId: string;
  previewToken: string;
  acknowledgedRows: string[];
}
export interface IssuedRow {
  id: number;
  key: string;
  recipient: string;
  role: InviteRole;
  targets: BatchTarget[];
  expires: string;
  status: "pending" | "accepted" | "revoked" | "expired";
  code?: string;
}
export interface BatchReceipt {
  id: number;
  workspace: string;
  created: string;
  kind?: "manual" | "register";
  source?: string | null;
  items: IssuedRow[];
  replayed?: boolean;
  revokedCount?: number;
}
export const targetLabel = (t: BatchTarget) =>
  [t.name, t.className].filter(Boolean).join(" · ");

export function invitationBatchCsv(receipt: BatchReceipt) {
  const rows = [["Получатель", "Роль", "Доступ", "Срок до", "Код"]];
  for (const item of receipt.items) {
    if (item.status !== "pending" || !item.code) continue;
    rows.push([
      item.recipient,
      { parent: "Родитель", student: "Ученик", teacher: "Учитель" }[item.role],
      item.targets.map(targetLabel).join("; "),
      item.expires,
      item.code,
    ]);
  }
  return "\uFEFF" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
}

export interface RegisterColumns {
  surname: number | null;
  name: number | null;
  children: number | null;
}
export interface RegisterCounts {
  rows: number;
  ready: number;
  readyCovered: number;
  partial: number;
  partialCovered: number;
  notFound: number;
  ambiguous: number;
  noChildren: number;
  noName: number;
  repeated: number;
  formula: number;
  overlap: number;
  unnamed: number;
}
export interface RegisterAttention {
  row: number;
  recipient: string;
  outcome:
    | "ready"
    | "partial"
    | "notFound"
    | "ambiguous"
    | "noName"
    | "formula"
    | "overlap";
  children: string;
  found: BatchTarget[];
  missing: string[];
  ambiguous: string[];
  lookalike: string[];
  overlaps: number[];
  warnings: BatchWarning[];
}
export interface RegisterPreview {
  sheets: string[];
  sheet: number;
  headerRow: number;
  headers: { index: number; label: string }[];
  columns: RegisterColumns;
  rowCount: number;
  needsColumns: boolean;
  notes: string[];
  counts: RegisterCounts | null;
  attention: RegisterAttention[];
  attentionTotal: number;
  offset: number;
  previewToken: string | null;
}
export interface RegisterIssue {
  workspace: string;
  filename: string;
  content: string;
  sheet: number;
  headerRow: number;
  columns: RegisterColumns;
  delimiter: string;
  encoding: string;
  days: number;
  previewToken: string;
  requestId: string;
  includePartial: boolean;
  includeCovered: boolean;
}

/** How many invitations the server will create for the chosen opt-ins. */
export function registerIssueCount(
  counts: RegisterCounts,
  includePartial: boolean,
  includeCovered: boolean,
) {
  return (
    counts.ready +
    (includeCovered ? counts.readyCovered : 0) +
    (includePartial
      ? counts.partial + (includeCovered ? counts.partialCovered : 0)
      : 0)
  );
}

/** Reasons a register row needs attention, in plain words for the administrator. */
export function registerReasons(row: RegisterAttention) {
  const reasons: string[] = [];
  if (row.outcome === "noName")
    reasons.push("Имя родителя длиннее 160 знаков: проверьте ячейки.");
  if (row.outcome === "formula")
    reasons.push(
      "В выбранной ячейке формула. Сохраните файл со значениями вместо формул.",
    );
  if (row.outcome === "overlap")
    reasons.push(
      `Тот же родитель с теми же детьми есть в других строках (${row.overlaps.join(", ")}): один человек получил бы два кода. Объедините строки в файле.`,
    );
  if (row.outcome === "notFound")
    reasons.push("Ни один ребёнок не найден среди действующих учеников.");
  if (row.outcome === "partial")
    reasons.push("Найдены не все дети: доступ получат только найденные.");
  if (row.ambiguous.length)
    reasons.push(
      `Под запись подходит несколько учеников: ${row.ambiguous.join("; ")}.`,
    );
  if (row.missing.length)
    reasons.push(`Не найдено: ${row.missing.join("; ")}.`);
  if (row.lookalike.length)
    reasons.push(
      `Совпадение есть только если считать русские и латинские буквы-двойники одинаковыми (В и B, С и C, Н и H, Р и P, Х и X, і и i). Это могут быть разные классы или имена, поэтому строка не выдаётся: ${row.lookalike.join("; ")}. Исправьте написание в файле.`,
    );
  for (const w of row.warnings)
    reasons.push(
      `${w.kind === "accounts" ? "Уже есть действующие аккаунты" : "Есть ожидающие приглашения"}: ${w.count}. ${w.names.join(", ")}`,
    );
  return reasons;
}

const classOrder = (item: IssuedRow) => item.targets[0]?.className || "";

/** Pending codes of a register, ordered by the class of the first child, then by parent. */
export function registerHandoutOrder(items: IssuedRow[]) {
  return items
    .filter((i) => i.status === "pending" && i.code)
    .sort((a, b) => {
      const x = classOrder(a),
        y = classOrder(b);
      if (!x !== !y) return x ? -1 : 1;
      return (
        x.localeCompare(y, "ru", { numeric: true }) ||
        a.recipient.localeCompare(b.recipient, "ru")
      );
    });
}

export function registerInvitationsCsv(receipt: BatchReceipt) {
  const rows = [["Класс", "Получатель", "Дети", "Срок до", "Код"]];
  for (const item of registerHandoutOrder(receipt.items))
    rows.push([
      classOrder(item),
      item.recipient,
      item.targets.map(targetLabel).join("; "),
      item.expires,
      item.code!,
    ]);
  return "﻿" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
}

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

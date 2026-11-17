import { isValidElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";
import AccessAuditTable from "./AccessAuditTable";
import { auditActionNames, type AuditEntry } from "./accessModel";

const knownCode = Object.keys(auditActionNames)[0];
const entry = (over: Partial<AuditEntry> & { id: number }): AuditEntry => ({
  created: "2026-03-04T09:05:06",
  actorName: "Анна Примерова",
  action: knownCode,
  workspace: null,
  revision: null,
  detail: "",
  ...over,
});

// The table has no hooks and no components inside: its elements are read as text.
const textOf = (node: ReactNode): string =>
  Array.isArray(node)
    ? node.map(textOf).join(" ")
    : typeof node === "string" || typeof node === "number"
      ? String(node)
      : isValidElement(node)
        ? textOf((node.props as { children?: ReactNode }).children)
        : "";

describe("AccessAuditTable", () => {
  it("shows the Russian name of a known action, not its code", () => {
    const text = textOf(AccessAuditTable({ rows: [entry({ id: 1 })] }));
    expect(text).toContain(auditActionNames[knownCode]);
    expect(text).not.toContain(knownCode);
  });
  it("still shows the code of an action it has no name for", () => {
    const text = textOf(
      AccessAuditTable({
        rows: [entry({ id: 2, action: "future.unknown_action" })],
      }),
    );
    expect(text).toContain("future.unknown_action");
  });
  it("shows who did it and the details of the entry", () => {
    const text = textOf(
      AccessAuditTable({
        rows: [
          entry({ id: 3, actorName: "Дмитрий Образцов", detail: "ключ-7" }),
        ],
      }),
    );
    expect(text).toContain("Дмитрий Образцов");
    expect(text).toContain("ключ-7");
  });
  it("says that nothing was found instead of an empty table", () => {
    const text = textOf(AccessAuditTable({ rows: [] }));
    expect(text.trim()).not.toBe("");
  });
  it("does not say that nothing was found while the records are still coming", () => {
    const say = (status: "loading" | "ready" | "failed") =>
      textOf(AccessAuditTable({ rows: [], status })).trim();
    expect(say("loading")).not.toBe("");
    expect(say("failed")).not.toBe("");
    expect(new Set([say("loading"), say("ready"), say("failed")]).size).toBe(3);
  });
  it("shows what is placed under the table", () => {
    const text = textOf(
      AccessAuditTable({ rows: [entry({ id: 4 })], children: "под таблицей" }),
    );
    expect(text).toContain("под таблицей");
  });
});

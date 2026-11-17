import type { ReactNode } from "react";
import {
  auditAction,
  auditDetails,
  auditStamp,
  type AuditEntry,
} from "./accessModel";

// The audit log as a compact table: when, who, what, details. It has no state and no buttons in the rows:
// which records are shown is decided by the page, which also puts the paging line under the table.
export default function AccessAuditTable({
  rows,
  children,
}: {
  rows: AuditEntry[];
  // Placed under the table: «Показано N из M», «Показать ещё», «Показать более ранние».
  children?: ReactNode;
}) {
  return (
    <section aria-label="Журнал действий" className="access-audit-section">
      {!rows.length && <p>Записей по этим условиям нет.</p>}
      {rows.length > 0 && (
        <div className="access-audit-list">
          <table className="access-audit-table" aria-label="Журнал действий">
            <thead>
              <tr>
                <th scope="col">Когда</th>
                <th scope="col">Кто</th>
                <th scope="col">Действие</th>
                <th scope="col">Подробности</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id} className="access-audit-row">
                  <td className="access-a-when">{auditStamp(a.created)}</td>
                  <td className="access-a-who" title={a.actorName}>
                    {a.actorName}
                  </td>
                  <td className="access-a-action">{auditAction(a)}</td>
                  <td className="access-a-details">{auditDetails(a)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {children}
    </section>
  );
}

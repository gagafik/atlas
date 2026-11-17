import type { ReactNode } from "react";
import { roleNames, type Account } from "./auth";
import { accessText, type LoadStatus } from "./accessModel";

// The accounts as a compact table: one row per account and one button, «Изменить», which opens the form in place of the list.
// It has no state: which rows are shown and what the button does are decided by the page.
export default function AccessUsersTable({
  rows,
  status = "ready",
  busy,
  highlightId,
  onEdit,
  children,
}: {
  rows: Account[];
  // Until the accounts have arrived the table does not say that there are none; after a failed load the error says it.
  status?: LoadStatus;
  busy: boolean;
  // The account that was just saved; its row is marked.
  highlightId?: string | null;
  onEdit: (account: Account) => void;
  // Placed under the table: «Показано N из M» and «Показать ещё».
  children?: ReactNode;
}) {
  return (
    <section aria-label="Учётные записи" className="access-users-section">
      {!rows.length && status === "loading" && (
        <p role="status">Загружаю учётные записи…</p>
      )}
      {!rows.length && status === "ready" && (
        <p>Учётных записей по этим условиям нет.</p>
      )}
      {rows.length > 0 && (
        <div className="access-users-list">
          <table className="access-users-table" aria-label="Учётные записи">
            <thead>
              <tr>
                <th scope="col">Имя</th>
                <th scope="col">Логин</th>
                <th scope="col">Роль</th>
                <th scope="col">Состояние</th>
                <th scope="col">Доступ</th>
                <th scope="col">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => {
                const access = accessText(u);
                const classes = [
                  "access-row",
                  u.active ? "" : "access-row-off",
                  u.id === highlightId ? "access-row-saved" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <tr key={u.id} id={`access-row-${u.id}`} className={classes}>
                    <td className="access-c-name" title={u.name}>
                      {u.name}
                    </td>
                    <td className="access-c-login" title={u.login}>
                      {u.login}
                    </td>
                    <td className="access-c-role" title={roleNames[u.role]}>
                      {roleNames[u.role]}
                    </td>
                    <td className="access-c-state">
                      <span
                        className="access-state"
                        data-state={u.active ? "active" : "inactive"}
                      >
                        {u.active ? "Активна" : "Отключена"}
                      </span>
                    </td>
                    <td className="access-c-access" title={access}>
                      {access}
                    </td>
                    <td className="access-c-actions">
                      <button
                        id={`access-edit-${u.id}`}
                        disabled={busy}
                        aria-label={`Изменить: ${u.name}`}
                        onClick={() => onEdit(u)}
                      >
                        Изменить
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {children}
    </section>
  );
}

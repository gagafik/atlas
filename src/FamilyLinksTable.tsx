import type { ReactNode } from "react";
import type { LoadStatus } from "./accessModel";
import {
  accessInactive,
  linkChips,
  linkKey,
  relationships,
  termText,
  type Chip,
  type FamilyLink,
} from "./familyLinksModel";

/** The id of the button of a link: the page puts the focus there when the registry comes back. */
export const familyOpenId = (l: Pick<FamilyLink, "parentId" | "studentId">) =>
  `family-open-${l.parentId}-${l.studentId}`;

const ChipView = ({ chip }: { chip: Chip }) => (
  <span className="family-chip" data-tone={chip.tone} title={chip.title}>
    {chip.text}
  </span>
);

// The registry of the parent-pupil links as a compact table: one row per link and one button, «Открыть», which opens the
// link in place of the registry. It has no state: which rows are shown and what the button does are decided by the page.
export default function FamilyLinksTable({
  rows,
  status = "ready",
  busy,
  highlightKey,
  onOpen,
  children,
}: {
  rows: FamilyLink[];
  // Until the registry has arrived the table does not say that there are no links; after a failed load it says so itself.
  status?: LoadStatus;
  busy: boolean;
  // The link that was just saved; its row is marked.
  highlightKey?: string | null;
  onOpen: (link: FamilyLink) => void;
  // Placed under the table: «Показано N» and «Следующие связи».
  children?: ReactNode;
}) {
  return (
    <section aria-label="Связи родителей и учеников" className="family-section">
      {!rows.length && status === "loading" && (
        <p role="status">Загружаю связи…</p>
      )}
      {!rows.length && status === "ready" && <p>Связи не найдены.</p>}
      {!rows.length && status === "failed" && (
        <p role="status">Связи не загрузились. Нажмите «Обновить реестр».</p>
      )}
      {rows.length > 0 && (
        <div className="family-list">
          <table
            className="family-table"
            aria-label="Связи родителей и учеников"
          >
            <thead>
              <tr>
                <th scope="col">Родитель</th>
                <th scope="col">Ученик</th>
                <th scope="col">Отношение</th>
                <th scope="col">Доступ</th>
                <th scope="col">Проверка</th>
                <th scope="col">Срок</th>
                <th scope="col">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((l) => {
                const chips = linkChips(l);
                const term = termText(l);
                const classes = [
                  "family-row",
                  accessInactive(l) ? "family-row-off" : "",
                  linkKey(l) === highlightKey ? "family-row-saved" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <tr key={linkKey(l)} className={classes}>
                    <td className="family-c-parent" title={l.parentName}>
                      {l.parentName}
                    </td>
                    <td className="family-c-student" title={l.studentName}>
                      {l.studentName}
                    </td>
                    <td
                      className="family-c-relation"
                      title={relationships[l.relationship]}
                    >
                      {relationships[l.relationship] ?? l.relationship}
                    </td>
                    <td className="family-c-access">
                      <ChipView chip={chips.access} />
                    </td>
                    <td className="family-c-check">
                      <ChipView chip={chips.check} />
                      {chips.account && <ChipView chip={chips.account} />}
                    </td>
                    <td className="family-c-term" title={term.title}>
                      {term.text}
                    </td>
                    <td className="family-c-actions">
                      <button
                        id={familyOpenId(l)}
                        disabled={busy}
                        aria-label={`Открыть: ${l.parentName} → ${l.studentName}`}
                        onClick={() => onOpen(l)}
                      >
                        Открыть
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

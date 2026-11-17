import { Fragment, type ReactNode } from "react";
import {
  actionLabels,
  dayLabel,
  kindLabels,
  noteActions,
  noteChips,
  stamp,
  statusLabels,
  type History,
  type Note,
  type NoteChip,
} from "./behaviourNotesModel";

// The words of the small marks of a record; the facts come from `noteChips`.
function chipLabel(chip: NoteChip, note: Note) {
  switch (chip.key) {
    case "pending":
      return statusLabels.pending;
    case "rejected":
      return statusLabels.rejected;
    case "withdrawn":
      return statusLabels.withdrawn;
    case "approved":
      return "Утверждена";
    case "hidden":
      return "Скрыта от семьи";
    case "printed":
      return "В табеле";
    case "letter":
      return `Письмо № ${note.letterNumber}`;
    case "ackDone":
      return "Ознакомлен(а)";
    default:
      return "Ждёт подтверждения";
  }
}
// What a short mark stands for; the same words are in the line that opens under the row.
function chipHint(chip: NoteChip, note: Note) {
  if (chip.title) return chip.title;
  if (chip.key === "ackDone")
    return `Родитель ознакомился ${stamp(note.acknowledgedAt)}`;
  if (chip.key === "ackWait") return "Ожидает подтверждения родителя";
  return undefined;
}

// The records of the dialog as a compact table: one row per record, the rest of it in a line that opens under the row.
// A row holds only what waits for a decision and «Подробнее»; editing, withdrawing and the history are in the opened line.
// It has no state: what the buttons do is decided by the callbacks, which row is open by `openId`.
export default function BehaviourNotesTable({
  notes,
  staff,
  canAcknowledge,
  openId,
  history,
  empty,
  onToggle,
  onDecide,
  onAcknowledge,
  onLetter,
  onEdit,
  onWithdraw,
  onHistory,
  children,
}: {
  notes: Note[];
  staff: boolean;
  canAcknowledge: boolean;
  openId: string | null;
  history: { id: string; items: History } | null;
  // The line shown while there are no records.
  empty: string;
  onToggle: (id: string) => void;
  onDecide: (note: Note, approve: boolean) => void;
  onAcknowledge: (note: Note) => void;
  onLetter: (note: Note) => void;
  onEdit: (note: Note) => void;
  onWithdraw: (note: Note) => void;
  onHistory: (note: Note) => void;
  // Placed under the table («Показать ещё»).
  children?: ReactNode;
}) {
  const columns = staff ? 7 : 6;
  return (
    <section aria-label="Записи" className="bn-records-section">
      {!notes.length && <p>{empty}</p>}
      {notes.length > 0 && (
        <div className="bn-records">
          <table
            className={`bn-notes ${staff ? "bn-notes-staff" : "bn-notes-family"}`}
            aria-label="Записи"
          >
            <thead>
              <tr>
                <th scope="col">Дата</th>
                {staff && <th scope="col">Ученик</th>}
                <th scope="col">Вид и категория</th>
                <th scope="col">Текст</th>
                <th scope="col">Автор</th>
                <th scope="col">Состояние</th>
                <th scope="col">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {notes.map((n) => {
                const can = noteActions(n, { staff, canAcknowledge });
                const open = openId === n.id;
                const muted =
                  n.status === "withdrawn" || n.status === "rejected"
                    ? " bn-withdrawn"
                    : "";
                // Every button says which record it belongs to: the table has many of the same.
                const about = `${n.student}, ${dayLabel(n.day)}`;
                return (
                  <Fragment key={n.id}>
                    <tr className={`bn-notes-row${muted}`}>
                      <td className="bn-c-date">{dayLabel(n.day)}</td>
                      {staff && (
                        <td className="bn-c-pupil">
                          {n.student}
                          {n.className && <small>{n.className}</small>}
                        </td>
                      )}
                      <td className="bn-c-kind">
                        <span className="bn-kind" data-kind={n.kind}>
                          {kindLabels[n.kind]}
                        </span>{" "}
                        {n.category}
                        {n.level !== "teacher" && <small>{n.levelTitle}</small>}
                      </td>
                      <td
                        className="bn-c-text"
                        title={n.text || undefined}
                        onClick={() => onToggle(n.id)}
                      >
                        {n.text || "—"}
                      </td>
                      <td className="bn-c-author">{n.author}</td>
                      <td className="bn-c-state">
                        <div className="bn-chips">
                          {noteChips(n, staff).map((chip) => (
                            <span
                              key={chip.key}
                              className="bn-state"
                              data-state={chip.key}
                              title={chipHint(chip, n)}
                            >
                              {chipLabel(chip, n)}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="bn-c-actions">
                        <div className="bn-row-actions">
                          {can.decide && (
                            <>
                              <button
                                aria-label={`Утвердить: ${about}`}
                                onClick={() => onDecide(n, true)}
                              >
                                Утвердить
                              </button>
                              <button
                                aria-label={`Отклонить: ${about}`}
                                onClick={() => onDecide(n, false)}
                              >
                                Отклонить
                              </button>
                            </>
                          )}
                          {can.acknowledge && (
                            <button
                              aria-label={`Ознакомлен: ${about}`}
                              onClick={() => onAcknowledge(n)}
                            >
                              Ознакомлен
                            </button>
                          )}
                          {can.letter && (
                            <button
                              aria-label={`Письмо-решение: ${about}`}
                              onClick={() => onLetter(n)}
                            >
                              Письмо-решение
                            </button>
                          )}
                          <button
                            aria-label={`Подробнее: ${about}`}
                            aria-expanded={open}
                            aria-controls={`bn-detail-${n.id}`}
                            onClick={() => onToggle(n.id)}
                          >
                            Подробнее
                          </button>
                        </div>
                      </td>
                    </tr>
                    {open && (
                      <tr
                        id={`bn-detail-${n.id}`}
                        className={`bn-detail-row${muted}`}
                      >
                        <td colSpan={columns}>
                          {n.text && <p className="bn-text">{n.text}</p>}
                          <p className="bn-meta">
                            Автор: {n.author}
                            {n.lesson && ` · Урок: ${n.lesson}`}
                            {n.approvedBy && ` · Утвердил(а): ${n.approvedBy}`}
                            {n.decision && ` · Решение: ${n.decision}`}
                            {n.requireAck &&
                              n.status === "published" &&
                              (n.acknowledged
                                ? ` · Родитель ознакомился ${stamp(n.acknowledgedAt)} (${n.acknowledgedBy})`
                                : " · Ожидает подтверждения родителя")}
                            {n.status === "withdrawn" &&
                              ` · Отозвана: ${n.withdrawReason || ""}`}
                          </p>
                          {(can.edit || can.history) && (
                            <div className="bn-detail-actions">
                              {can.edit && (
                                <>
                                  <button
                                    aria-label={`Изменить: ${about}`}
                                    disabled={can.edit === "off"}
                                    onClick={() => onEdit(n)}
                                  >
                                    Изменить
                                  </button>
                                  <button
                                    aria-label={`Отозвать: ${about}`}
                                    onClick={() => onWithdraw(n)}
                                  >
                                    Отозвать
                                  </button>
                                </>
                              )}
                              {can.history && (
                                <button
                                  aria-label={`История: ${about}`}
                                  onClick={() => onHistory(n)}
                                >
                                  История
                                </button>
                              )}
                            </div>
                          )}
                          {history?.id === n.id && (
                            <ol className="bn-history">
                              {history.items.map((h) => (
                                <li key={h.seq}>
                                  {stamp(h.created)} · {h.actor} ·{" "}
                                  {actionLabels[h.action] || h.action}
                                  {h.reason ? ` · ${h.reason}` : ""}
                                </li>
                              ))}
                            </ol>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
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

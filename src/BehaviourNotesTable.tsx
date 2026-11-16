import type { ReactNode } from "react";
import {
  actionLabels,
  dayLabel,
  kindLabels,
  noteActions,
  stamp,
  statusLabels,
  type History,
  type Note,
} from "./behaviourNotesModel";

// The records of the dialog. It has no state: what the buttons do is decided by the callbacks.
export default function BehaviourNotesTable({
  notes,
  staff,
  canAcknowledge,
  history,
  empty,
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
  history: { id: string; items: History } | null;
  // The line shown while there are no records.
  empty: string;
  onDecide: (note: Note, approve: boolean) => void;
  onAcknowledge: (note: Note) => void;
  onLetter: (note: Note) => void;
  onEdit: (note: Note) => void;
  onWithdraw: (note: Note) => void;
  onHistory: (note: Note) => void;
  // Placed under the list («Показать ещё»).
  children?: ReactNode;
}) {
  return (
    <section aria-label="Записи">
      {!notes.length && <p>{empty}</p>}
      <ul className="bn-list">
        {notes.map((n) => {
          const can = noteActions(n, { staff, canAcknowledge });
          return (
            <li
              key={n.id}
              className={`bn-note bn-${n.kind}${n.status === "withdrawn" || n.status === "rejected" ? " bn-withdrawn" : ""}`}
            >
              <p>
                <strong>{kindLabels[n.kind]}</strong> · {n.category} ·{" "}
                {dayLabel(n.day)}
                {n.level !== "teacher" && ` · ${n.levelTitle}`}
                {staff && n.status !== "published" && (
                  <span className="bn-badge">{statusLabels[n.status]}</span>
                )}
                {staff && n.visible === false && (
                  <span className="bn-badge">Скрыта от семьи</span>
                )}
                {staff && n.printed && (
                  <span className="bn-badge">В табеле</span>
                )}
                {n.letterNumber && (
                  <span className="bn-badge">Письмо № {n.letterNumber}</span>
                )}
              </p>
              {staff && (
                <p>
                  {n.student}
                  {n.className ? ` · ${n.className}` : ""}
                </p>
              )}
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
              <div className="bn-controls">
                {can.decide && (
                  <>
                    <button onClick={() => onDecide(n, true)}>Утвердить</button>
                    <button onClick={() => onDecide(n, false)}>
                      Отклонить
                    </button>
                  </>
                )}
                {can.acknowledge && (
                  <button onClick={() => onAcknowledge(n)}>Ознакомлен</button>
                )}
                {can.letter && (
                  <button onClick={() => onLetter(n)}>Письмо-решение</button>
                )}
                {can.edit && (
                  <>
                    <button
                      disabled={can.edit === "off"}
                      onClick={() => onEdit(n)}
                    >
                      Изменить
                    </button>
                    <button onClick={() => onWithdraw(n)}>Отозвать</button>
                  </>
                )}
                {can.history && (
                  <button onClick={() => onHistory(n)}>История</button>
                )}
              </div>
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
            </li>
          );
        })}
      </ul>
      {children}
    </section>
  );
}

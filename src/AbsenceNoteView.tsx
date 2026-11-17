import type { Ref } from "react";
import { StatusBadge } from "./AbsenceTables";
import { sharedDecision } from "./absenceForm";
import { statusLabel, type AbsenceNote } from "./absenceNotes";

type HistoryEntry = {
  id: number;
  version: number;
  created: string;
  actor: string;
  action: string;
  comment: string;
  batchId?: string | null;
};

// One opened note in place of the list: the facts, the reason and files, the decision block and the history.
// The panel keeps the state and makes the requests.
export default function AbsenceNoteView({
  note,
  facts,
  busy,
  comment,
  history,
  historyNext,
  headingRef,
  fileUrl,
  onBack,
  onComment,
  onDecide,
  onRefresh,
  onHistory,
}: {
  note: AbsenceNote;
  facts: string;
  busy: boolean;
  comment: string;
  history: HistoryEntry[];
  historyNext: number | null;
  headingRef: Ref<HTMLHeadingElement>;
  fileUrl: (id: string) => string;
  onBack: () => void;
  onComment: (value: string) => void;
  onDecide: (action: string) => void;
  onHistory: (before?: number) => void;
  onRefresh: () => void;
}) {
  const decides =
    note.canWithdraw ||
    (note.canReview && ["pending", "approved"].includes(note.status));
  return (
    <article className="absence-notes-note">
      <button type="button" className="absence-notes-back" onClick={onBack}>
        ← К запискам
      </button>
      <div className="absence-notes-note-head">
        <h3 tabIndex={-1} ref={headingRef}>
          {note.studentName}
        </h3>
        <StatusBadge status={note.status} />
      </div>
      <p className="absence-notes-facts">{facts}</p>
      {note.summary ? (
        <>
          <p className="info-line">
            Причина и файлы доступны взрослым представителям и школе.
          </p>
          <div className="absence-notes-buttons">
            <button disabled={busy} onClick={onRefresh}>
              Обновить
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="journal-text">{note.reason}</p>
          {note.files?.map((f) => (
            <p key={f.id}>
              <a href={fileUrl(f.id)}>{f.filename}</a> ·{" "}
              {Math.ceil(f.size / 1024)} КиБ
            </p>
          ))}
          <details className="absence-notes-help">
            <summary>Что меняет решение</summary>
            <p>
              Принятие меняет учёт отметки «Отсутствует» на «Уважительная
              причина». Оно не ставит отсутствие вместо «Не отмечено»,
              «Присутствует» или «Опоздание» и не публикует черновики журнала.
              Причина и файлы в журнал не передаются.
            </p>
          </details>
          <fieldset disabled={busy} className="access-fields">
            {decides && (
              <>
                <label className="absence-notes-field">
                  <span>
                    {note.canWithdraw && !note.canReview
                      ? "Комментарий (необязательно)"
                      : "Объяснение решения"}
                  </span>
                  <textarea
                    maxLength={2000}
                    value={comment}
                    onChange={(e) => onComment(e.target.value)}
                  />
                </label>
                {!!note.periodLocks?.length && note.canReview && (
                  <p className="info-line">
                    Период журнала закрыт: {note.periodLocks.join(", ")}.
                    Принять объяснительную или отменить решение можно после
                    того, как администратор откроет период.
                  </p>
                )}
                <div className="absence-notes-buttons">
                  {note.canWithdraw && (
                    <button onClick={() => onDecide("withdraw")}>
                      Отозвать заявление
                    </button>
                  )}
                  {note.canReview && note.status === "pending" && (
                    <>
                      <button
                        disabled={!comment.trim() || !!note.periodLocks?.length}
                        onClick={() => onDecide("approve")}
                      >
                        Принять объяснительную
                      </button>
                      <button
                        disabled={!comment.trim()}
                        onClick={() => onDecide("reject")}
                      >
                        Отклонить объяснительную
                      </button>
                    </>
                  )}
                  {note.canReview && note.status === "approved" && (
                    <button
                      disabled={!comment.trim() || !!note.periodLocks?.length}
                      onClick={() => onDecide("revoke")}
                    >
                      Отменить принятое решение
                    </button>
                  )}
                </div>
              </>
            )}
            <div className="absence-notes-buttons">
              <button onClick={onRefresh}>Обновить</button>
              <button onClick={() => onHistory()}>История записки</button>
            </div>
          </fieldset>
        </>
      )}
      {history.map((h) => (
        <div className="family-history" key={h.id}>
          <p>
            {new Date(h.created).toLocaleString("ru-RU")} · {h.actor} ·{" "}
            {statusLabel(h.action)} · редакция {h.version}
            {sharedDecision(h) && " · общее решение"}
          </p>
          {h.comment && <p className="journal-text">{h.comment}</p>}
        </div>
      ))}
      {historyNext !== null && (
        <button disabled={busy} onClick={() => onHistory(historyNext)}>
          Предыдущие решения
        </button>
      )}
    </article>
  );
}

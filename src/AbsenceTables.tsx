import {
  BULK_LIMIT,
  allPicked,
  bulkEligible,
  changedLabel,
  filedBy,
  periodLabel,
  pickEligible,
  statusLabel,
  statusTone,
  subjectName,
  type AbsenceNote,
  type ClassSetting,
  type NoteViewer,
} from "./absenceNotes";
import { codeName, type PreviewLesson } from "./absenceForm";

// The tables of the absence notes screen. They have no state: which notes are picked, which tab and which view is shown
// is decided by the panel.

/** Marks the «Открыть» button of a note, so that the panel can put the focus back on it. */
export const noteOpenAttr = "data-note-open";

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className="absence-notes-badge" data-tone={statusTone(status)}>
      {statusLabel(status)}
    </span>
  );
}

type Names = {
  codes: { id: string; name: string }[];
  subjects: { id: string; name: string }[];
};

// The notes of the school or of the family. A reviewer's rows may be picked for a shared decision.
export function NotesTable({
  items,
  codes,
  subjects,
  viewer,
  loading = false,
  busy = false,
  pick,
  onOpen,
}: Names & {
  items: AbsenceNote[];
  viewer: NoteViewer;
  loading?: boolean;
  busy?: boolean;
  /** Present for a reviewer: the picked ids, the action of the shared decision and what the checkboxes do. */
  pick?: {
    ids: string[];
    action: string;
    onToggle: (id: string, checked: boolean) => void;
    onToggleAll: () => void;
  };
  onOpen: (note: AbsenceNote) => void;
}) {
  if (!items.length) return loading ? null : <p>Записок не найдено.</p>;
  const eligible = pick ? pickEligible(items, pick.action) : [];
  const picked = pick ? pick.ids.filter((id) => eligible.includes(id)) : [];
  return (
    <div className="absence-notes-table absence-notes-t-notes">
      <table aria-label="Записки об отсутствии">
        <thead>
          <tr>
            {pick && (
              <th
                scope="col"
                className="absence-notes-c-pick"
                data-all-label={`Выбрать до ${BULK_LIMIT} загруженных`}
              >
                <input
                  type="checkbox"
                  aria-label={`Выбрать до ${BULK_LIMIT} загруженных`}
                  title={`Выбрать до ${BULK_LIMIT} загруженных`}
                  checked={allPicked(items, pick.ids, pick.action)}
                  ref={(el) => {
                    if (el)
                      el.indeterminate =
                        picked.length > 0 &&
                        !allPicked(items, pick.ids, pick.action);
                  }}
                  disabled={busy || !eligible.length}
                  onChange={pick.onToggleAll}
                />
              </th>
            )}
            <th scope="col">Ученик</th>
            <th scope="col">Период</th>
            <th scope="col">Тип</th>
            <th scope="col">Предмет</th>
            <th scope="col">Кем подано</th>
            <th scope="col">Статус</th>
            <th scope="col">
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((n) => {
            const period = periodLabel(n.start, n.end),
              chosen = !!pick && pick.ids.includes(n.id);
            return (
              <tr key={n.id} className="absence-notes-row">
                {pick && (
                  <td className="absence-notes-c-pick">
                    {bulkEligible(n.status, pick.action) && (
                      <input
                        type="checkbox"
                        aria-label={`Включить в общее решение: ${n.studentName}, ${period}`}
                        checked={chosen}
                        disabled={
                          busy || (!chosen && pick.ids.length >= BULK_LIMIT)
                        }
                        onChange={(e) => pick.onToggle(n.id, e.target.checked)}
                      />
                    )}
                  </td>
                )}
                <th scope="row" className="absence-notes-c-name">
                  {n.studentName}
                </th>
                <td className="absence-notes-c-period">{period}</td>
                <td className="absence-notes-c-type" data-label="Тип">
                  {codeName(codes, n.codeId)}
                </td>
                <td className="absence-notes-c-subject" data-label="Предмет">
                  {subjectName(subjects, n.subjectId)}
                </td>
                <td className="absence-notes-c-by" data-label="Кем подано">
                  {filedBy(n, viewer)}
                </td>
                <td className="absence-notes-c-status">
                  <StatusBadge status={n.status} />
                </td>
                <td className="absence-notes-c-actions">
                  <button
                    className="absence-notes-row-btn"
                    {...{ [noteOpenAttr]: n.id }}
                    aria-label={`Открыть записку: ${n.studentName}, ${period}`}
                    disabled={busy}
                    onClick={() => onOpen(n)}
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
  );
}

// The classes with the switch of electronic notes; the button of a row changes only that class.
export function ClassesTable({
  classes,
  busy = false,
  onSwitch,
}: {
  classes: ClassSetting[];
  busy?: boolean;
  onSwitch: (c: ClassSetting) => void;
}) {
  return (
    <div className="absence-notes-table absence-notes-t-classes">
      <table aria-label="Электронные записки по классам">
        <thead>
          <tr>
            <th scope="col">Класс</th>
            <th scope="col">Классный руководитель</th>
            <th scope="col">Записки</th>
            <th scope="col">Изменено</th>
            <th scope="col">
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {classes.map((c) => (
            <tr key={c.id} className="absence-notes-row">
              <th scope="row" className="absence-notes-c-name">
                {c.name}
              </th>
              <td
                className="absence-notes-c-teacher"
                data-label="Классный руководитель"
              >
                {c.classTeacher || "не назначен"}
              </td>
              <td className="absence-notes-c-enabled" data-label="Записки">
                <span
                  className="absence-notes-badge"
                  data-tone={c.enabled ? "positive" : "muted"}
                >
                  {c.enabled ? "включены" : "выключены"}
                </span>
              </td>
              <td className="absence-notes-c-changed" data-label="Изменено">
                {changedLabel(c)}
              </td>
              <td className="absence-notes-c-actions">
                {c.canChange && (
                  <button
                    className="absence-notes-row-btn"
                    aria-label={`${c.enabled ? "Выключить" : "Включить"} электронные записки ${c.name}`}
                    disabled={busy}
                    onClick={() => onSwitch(c)}
                  >
                    {c.enabled ? "Выключить" : "Включить"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// What the shared decision will do to each picked note.
export function BulkPreviewTable({
  items,
  fileUrl,
}: {
  items: (AbsenceNote & { nextStatus: string })[];
  fileUrl: (id: string) => string;
}) {
  return (
    <div className="absence-notes-table absence-notes-t-bulk">
      <table aria-label="Предпросмотр общего решения">
        <thead>
          <tr>
            <th scope="col">Ученик</th>
            <th scope="col">Период</th>
            <th scope="col">Было → станет</th>
            <th scope="col">Причина</th>
            <th scope="col">Файлы</th>
          </tr>
        </thead>
        <tbody>
          {items.map((n) => (
            <tr key={n.id} className="absence-notes-row">
              <th scope="row" className="absence-notes-c-name">
                {n.studentName}
              </th>
              <td className="absence-notes-c-period">
                {periodLabel(n.start, n.end)}
              </td>
              <td className="absence-notes-c-change" data-label="Было → станет">
                {statusLabel(n.status)} → {statusLabel(n.nextStatus)}
              </td>
              <td
                className="absence-notes-c-reason journal-text"
                data-label="Причина"
              >
                {n.reason}
              </td>
              <td className="absence-notes-c-files" data-label="Файлы">
                {n.files?.map((f) => (
                  <a key={f.id} href={fileUrl(f.id)}>
                    {f.filename}
                  </a>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// The lessons inside a period, one row each; the two buttons set the first or the last lesson of the note.
// The group column is there only when the lessons carry one (the parent's preview does, the school's does not).
export function LessonsTable({
  lessons,
  onEdge,
}: {
  lessons: (PreviewLesson & { group?: string })[];
  onEdge: (lesson: PreviewLesson, which: "start" | "end") => void;
}) {
  if (!lessons.length) return null;
  const grouped = lessons.some((l) => l.group !== undefined);
  return (
    <div className="absence-notes-table absence-notes-t-lessons">
      <table aria-label="Уроки в периоде">
        <thead>
          <tr>
            <th scope="col">Дата</th>
            <th scope="col">Время</th>
            <th scope="col">Предмет</th>
            {grouped && <th scope="col">Группа</th>}
            <th scope="col">
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {lessons.map((l, i) => (
            <tr key={i} className="absence-notes-row">
              <th scope="row" className="absence-notes-c-period">
                {l.date}
              </th>
              <td className="absence-notes-c-period" data-label="Время">
                {l.start}–{l.end}
              </td>
              <td className="absence-notes-c-subject" data-label="Предмет">
                {l.subject}
              </td>
              {grouped && (
                <td className="absence-notes-c-group" data-label="Группа">
                  {l.group}
                </td>
              )}
              <td className="absence-notes-c-actions">
                <button
                  type="button"
                  className="absence-notes-row-btn"
                  onClick={() => onEdge(l, "start")}
                >
                  С этого урока
                </button>
                <button
                  type="button"
                  className="absence-notes-row-btn"
                  onClick={() => onEdge(l, "end")}
                >
                  По этот урок
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

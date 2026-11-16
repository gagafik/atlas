import {
  dateLabel,
  itemAction,
  itemChips,
  manyPupils,
  queueSections,
  sentLabel,
  shortDate,
  submissionTag,
  taskGroups,
  workKey,
  type ChipKey,
  type QueueResult,
  type QueueRow,
} from "./homeworkQueue";
import "./HomeworkQueue.css";

const statusLabels: Record<string, string> = {
  not_submitted: "Не отправлено",
  submitted: "На проверке",
  returned: "На доработке",
  accepted: "Принято",
};
const chipLabels: Record<ChipKey, string> = {
  offline: "Без онлайн-сдачи",
  overdue: "Срок прошёл",
  closed: "Приём закрыт",
  late: "Отправлено после срока",
  readOnly: "Прежний урок · только просмотр",
};
const sectionTitles = {
  past: "Прошедшие сроки",
  next: "Ближайшие две недели",
  all: "",
};
const actionLabels = {
  review: "Проверить работу",
  submit: "Выполнить задание",
  task: "Посмотреть задание",
  work: "Посмотреть работу",
};

// The work opened in place of the list; `rowKey` names the row's button so that the focus can go back to it.
export interface OpenedWork {
  lessonKey: string;
  studentId?: string;
  subject: string;
  group: string;
  student?: string;
  rowKey: string;
}

const openRow = (item: QueueRow): OpenedWork => ({
  lessonKey: item.lessonKey,
  studentId: item.studentId,
  subject: item.subject,
  group: item.group,
  student: item.student,
  rowKey: workKey(item.lessonKey, item.studentId),
});
// The task's own text cut by the cell; a text that the server cut at 240 characters shows the ellipsis.
const previewText = (item: QueueRow) =>
  item.preview + (item.preview.length === 240 ? "…" : "");

function Chips({ item, today }: { item: QueueRow; today: string }) {
  return (
    <>
      <span
        className="homework-queue-chip homework-queue-chip-main"
        data-status={item.assignmentChanged ? "changed" : item.status}
      >
        {submissionTag(item, today, statusLabels)}
      </span>
      {itemChips(item).map((key) => (
        <span key={key} className="homework-queue-chip">
          {chipLabels[key]}
        </span>
      ))}
    </>
  );
}

interface TableProps {
  result: QueueResult;
  child?: string;
  // A work is being edited: another one is not opened in the meantime.
  busy: boolean;
  onOpen: (work: OpenedWork) => void;
}

/** «Домашние задания»: the family sees one row per work, the staff see the pupils grouped under their task. */
export default function HomeworkQueueTable(props: TableProps) {
  return (
    <div className="homework-queue-list">
      {props.result.staff ? (
        <StaffTable {...props} />
      ) : (
        <FamilyTable {...props} />
      )}
    </div>
  );
}

function FamilyTable({ result, busy, onOpen }: TableProps) {
  // A parent without a chosen child sees several pupils on one page: they get a column. One pupil is named in the caption.
  const many = manyPupils(result.items);
  const columns = many ? 6 : 5;
  const pupil = !many && result.items.length ? result.items[0].student : "";
  return (
    <table
      className="homework-queue-table homework-queue-family"
      aria-label="Домашние работы"
    >
      {pupil && <caption>{pupil}</caption>}
      <thead>
        <tr>
          {many && <th scope="col">Ученик</th>}
          <th scope="col">Срок</th>
          <th scope="col">Предмет</th>
          <th scope="col">Задание</th>
          <th scope="col">Состояние</th>
          <th scope="col">
            <span className="sr-only">Действие</span>
          </th>
        </tr>
      </thead>
      {queueSections(result.items, result.schoolToday, result.window).map(
        (section) => (
          <tbody key={section.key}>
            {sectionTitles[section.key] && (
              <tr className="homework-queue-section-row">
                <th colSpan={columns} scope="colgroup">
                  {sectionTitles[section.key]}
                </th>
              </tr>
            )}
            {section.items.map((item) => {
              const label = actionLabels[itemAction(item)];
              const key = workKey(item.lessonKey, item.studentId);
              return (
                <tr
                  key={key}
                  className="homework-queue-row"
                  data-student={item.student}
                >
                  {many && (
                    <td className="homework-queue-pupil">{item.student}</td>
                  )}
                  <td className="homework-queue-due" data-label="Срок">
                    <span>
                      {item.dueDate ? dateLabel(item.dueDate) : "не указан"}
                      {item.extended && (
                        <span className="homework-queue-mark">
                          {" "}
                          индивидуальный
                        </span>
                      )}
                    </span>
                    <small>урок {shortDate(item.lessonDate)}</small>
                  </td>
                  <td
                    className="homework-queue-subject"
                    title={`${item.subject} · ${item.group}`}
                  >
                    {item.subject}
                    <small>{item.group}</small>
                  </td>
                  <td className="homework-queue-text" title={previewText(item)}>
                    {previewText(item)}
                  </td>
                  <td className="homework-queue-state">
                    <div className="homework-queue-chips">
                      <Chips item={item} today={result.schoolToday} />
                    </div>
                    {item.submittedAt && (
                      <small>Отправлено: {sentLabel(item.submittedAt)}</small>
                    )}
                  </td>
                  <td className="homework-queue-action">
                    <button
                      data-row={key}
                      disabled={busy}
                      aria-label={`${label}: ${item.subject}`}
                      onClick={() => onOpen(openRow(item))}
                    >
                      {label}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        ),
      )}
    </table>
  );
}

function StaffTable({ result, busy, onOpen }: TableProps) {
  return (
    <table
      className="homework-queue-table homework-queue-staff"
      aria-label="Домашние работы"
    >
      <thead>
        <tr>
          <th scope="col">Ученик</th>
          <th scope="col">Состояние</th>
          <th scope="col">Отправлено</th>
          <th scope="col">
            <span className="sr-only">Действие</span>
          </th>
        </tr>
      </thead>
      {taskGroups(result.items).map((group) => {
        const first = group.items[0];
        const title = `${first.subject} · ${first.group}`;
        return (
          <tbody key={group.lessonKey + ":" + first.studentId}>
            <tr className="homework-queue-task-row">
              <th colSpan={4} scope="colgroup">
                <div className="homework-queue-task-head">
                  <strong>{title}</strong>
                  <span>
                    срок{" "}
                    {first.baseDueDate
                      ? dateLabel(first.baseDueDate)
                      : "не указан"}
                  </span>
                  <span>урок {shortDate(first.lessonDate)}</span>
                  <span
                    className="homework-queue-task-text"
                    title={previewText(first)}
                  >
                    {previewText(first)}
                  </span>
                  <button
                    data-row={workKey(group.lessonKey)}
                    disabled={busy}
                    aria-label={`Всё задание, ${title}`}
                    onClick={() =>
                      onOpen({
                        lessonKey: group.lessonKey,
                        subject: first.subject,
                        group: first.group,
                        rowKey: workKey(group.lessonKey),
                      })
                    }
                  >
                    Всё задание
                  </button>
                </div>
              </th>
            </tr>
            {group.items.map((item) => {
              const action = itemAction(item);
              const key = workKey(item.lessonKey, item.studentId);
              return (
                <tr
                  key={key}
                  className="homework-queue-row"
                  data-student={item.student}
                >
                  <td className="homework-queue-pupil">{item.student}</td>
                  <td className="homework-queue-state">
                    <div className="homework-queue-chips">
                      <Chips item={item} today={result.schoolToday} />
                      {item.extended && item.dueDate && (
                        <span className="homework-queue-chip">
                          срок {dateLabel(item.dueDate)} · индивидуальный
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="homework-queue-sent" data-label="Отправлено">
                    {item.submittedAt ? sentLabel(item.submittedAt) : "—"}
                  </td>
                  <td className="homework-queue-action">
                    <button
                      data-row={key}
                      disabled={busy}
                      aria-label={`${actionLabels[action]}, ${item.student}`}
                      onClick={() => onOpen(openRow(item))}
                    >
                      {action === "review" ? "Проверить" : "Открыть"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        );
      })}
    </table>
  );
}

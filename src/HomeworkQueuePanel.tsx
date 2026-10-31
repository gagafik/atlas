import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import HomeworkPanel from "./HomeworkPanel";
import { canLeaveHomework } from "./homeworkNavigation";
import { queueParams, queueSections, submissionTag } from "./homeworkQueue";

interface Item {
  lessonKey: string;
  studentId: string;
  student: string;
  subject: string;
  group: string;
  preview: string;
  lessonDate: string;
  dueDate: string | null;
  baseDueDate: string | null;
  extended: boolean;
  status: string;
  submittedAt: string | null;
  late: boolean;
  assignmentChanged: boolean;
  overdue: boolean;
  deadlineClosed: boolean;
  submission: "online" | "offline";
  readOnly: boolean;
  canReview: boolean;
  canSubmit: boolean;
}
interface Result {
  items: Item[];
  nextCursor: string | null;
  staff: boolean;
  filter: string;
  // "upcoming" is the default window of pupils and parents; explicit dates give "range".
  window: "upcoming" | "range";
  start: string;
  end: string;
  yearStart: string;
  yearEnd: string;
  schoolToday: string;
}
const labels: Record<string, string> = {
  not_submitted: "Не отправлено",
  submitted: "На проверке",
  returned: "На доработке",
  accepted: "Принято",
};
const sectionTitles = {
  past: "Прошедшие сроки",
  next: "Ближайшие две недели",
  all: "",
};
const initial = { start: "", end: "", filter: "", history: false, search: "" };
const dateLabel = (value: string) => value.split("-").reverse().join(".");

export default function HomeworkQueuePanel({
  workspace,
}: {
  workspace: string;
}) {
  const [fields, setFields] = useState(initial),
    [applied, setApplied] = useState(initial);
  const [result, setResult] = useState<Result | null>(null),
    [error, setError] = useState("");
  const [cursor, setCursor] = useState(""),
    [epoch, setEpoch] = useState(0),
    [busy, setBusy] = useState(true);
  const [selected, setSelected] = useState<Item | null>(null);
  const [activity, setActivity] = useState({ dirty: false, busy: false });
  const initialized = useRef(false);
  useEffect(() => {
    const leave = (e: Event) => {
      if (
        activity.busy ||
        (activity.dirty &&
          !window.confirm(
            "В домашней работе есть несохранённые изменения. Уйти без сохранения?",
          ))
      )
        e.preventDefault();
    };
    window.addEventListener("atlas-homework-leave", leave);
    return () => window.removeEventListener("atlas-homework-leave", leave);
  }, [activity]);
  useEffect(() => {
    let live = true;
    setBusy(true);
    setError("");
    setResult(null);
    api(`journal/homework-queue?${queueParams(applied, cursor)}`, workspace)
      .then((r: Result) => {
        if (!live) return;
        setResult(r);
        if (!initialized.current) {
          initialized.current = true;
          // The default window has no dates of its own: leave the fields empty to keep it.
          setFields((v) => ({
            ...v,
            ...(r.window === "range" ? { start: r.start, end: r.end } : {}),
            filter: r.filter,
          }));
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setBusy(false);
      });
    return () => {
      live = false;
    };
  }, [workspace, applied, cursor, epoch]);
  function refresh() {
    setCursor("");
    setEpoch((v) => v + 1);
  }
  function wholeYear(year: Result) {
    if (!canLeaveHomework()) return;
    setSelected(null);
    setCursor("");
    setFields((v) => ({ ...v, start: year.yearStart, end: year.yearEnd }));
    setApplied((v) => ({ ...v, start: year.yearStart, end: year.yearEnd }));
  }
  function changePage(next: string) {
    if (!canLeaveHomework()) return;
    setSelected(null);
    setCursor(next);
  }
  return (
    <section className="homework-queue" aria-label="Очередь домашних заданий">
      <p className="info-line">
        Опубликованные задания и отправленные ответы. Даты фильтра относятся к
        сроку сдачи; для задания без срока — к дате урока. Срок заканчивается в
        конце дня по местному времени школы.
      </p>
      <form
        className="homework-queue-filters"
        onSubmit={(e) => {
          e.preventDefault();
          if (!canLeaveHomework()) return;
          setSelected(null);
          setCursor("");
          setApplied({ ...fields });
        }}
      >
        <label className="field">
          <span>С даты</span>
          <input
            type="date"
            value={fields.start}
            onChange={(e) => setFields({ ...fields, start: e.target.value })}
          />
        </label>
        <label className="field">
          <span>По дату</span>
          <input
            type="date"
            value={fields.end}
            onChange={(e) => setFields({ ...fields, end: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Статус работы</span>
          <select
            value={fields.filter}
            onChange={(e) => setFields({ ...fields, filter: e.target.value })}
          >
            {!fields.filter && <option value="">Загрузка…</option>}
            <option value="all">Все работы</option>
            <option value="todo">Нужно сдать или исправить</option>
            <option value="submitted">На проверке</option>
            <option value="returned">На доработке</option>
            <option value="accepted">Принято</option>
            <option value="overdue">Просрочено, нужно сдать</option>
          </select>
        </label>
        <label className="field">
          <span>Поиск</span>
          <input
            value={fields.search}
            maxLength={100}
            placeholder="Предмет, группа или ученик"
            onChange={(e) => setFields({ ...fields, search: e.target.value })}
          />
        </label>
        <label className="homework-queue-history">
          <input
            type="checkbox"
            checked={fields.history}
            onChange={(e) =>
              setFields({ ...fields, history: e.target.checked })
            }
          />{" "}
          Включить прежние и отменённые уроки
        </label>
        <div className="dated-toolbar">
          <button className="primary" disabled={busy || activity.busy}>
            Применить фильтры
          </button>
          <button type="button" disabled={busy} onClick={refresh}>
            Обновить список
          </button>
        </div>
      </form>
      {selected && (
        <section
          className="homework-queue-detail"
          aria-label="Выбранная домашняя работа"
        >
          <div className="dated-toolbar">
            <h3>
              {selected.subject} · {selected.student}
            </h3>
            <button
              disabled={activity.busy}
              onClick={() => {
                if (canLeaveHomework()) setSelected(null);
              }}
            >
              Закрыть работу
            </button>
          </div>
          <HomeworkPanel
            key={selected.lessonKey + ":" + selected.studentId}
            workspace={workspace}
            lessonKey={selected.lessonKey}
            studentId={selected.studentId}
            initiallyOpen
            onActivity={setActivity}
            onChanged={refresh}
          />
        </section>
      )}
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {busy && <p role="status">Загружаем работы…</p>}
      {result && (
        <>
          {result.window === "upcoming" && (
            <div className="homework-queue-window">
              <p className="info-line">
                Сначала работы с прошедшим сроком — от недавних к ранним, затем
                сроки на 14 дней вперёд. Для другого периода укажите даты.
              </p>
              <button
                type="button"
                disabled={busy || activity.busy}
                onClick={() => wholeYear(result)}
              >
                Весь учебный год
              </button>
            </div>
          )}
          <p role="status">
            На этой странице: {result.items.length}.{" "}
            {result.window === "range" &&
              "Сначала ближайшие сроки, затем более поздние. "}
            {result.staff
              ? "Личные черновики учеников здесь не показываются."
              : "Показаны только доступные вам ученики."}
          </p>
          {!result.items.length && (
            <p className="info-line">
              Работ по этим условиям на этой странице нет.{" "}
              {result.nextCursor
                ? "Продолжите просмотр следующей страницы."
                : "Попробуйте другой статус, период или включите прежние уроки."}
            </p>
          )}
          {queueSections(result.items, result.schoolToday, result.window).map(
            (section) => (
              <section key={section.key} className="homework-queue-section">
                {sectionTitles[section.key] && (
                  <h3>{sectionTitles[section.key]}</h3>
                )}
                <div className="homework-queue-list">
                  {section.items.map((item) => (
                    <article
                      className="homework-queue-card"
                      key={item.lessonKey + ":" + item.studentId}
                    >
                      <div>
                        <h3>{item.subject}</h3>
                        <p>
                          {item.student} · {item.group}
                        </p>
                      </div>
                      <p className="homework-queue-preview">
                        {item.preview}
                        {item.preview.length === 240 ? "…" : ""}
                      </p>
                      <div className="homework-queue-tags">
                        <span>
                          {submissionTag(item, result.schoolToday, labels)}
                        </span>
                        {item.submission === "offline" && (
                          <span>Без онлайн-сдачи</span>
                        )}
                        {item.overdue && <span>Срок прошёл</span>}
                        {item.deadlineClosed && <span>Приём закрыт</span>}
                        {item.late && <span>Отправлено после срока</span>}
                        {item.readOnly && (
                          <span>Прежний урок · только просмотр</span>
                        )}
                      </div>
                      <p>
                        Срок:{" "}
                        <strong>
                          {item.dueDate ? dateLabel(item.dueDate) : "не указан"}
                          {item.extended ? " · индивидуальный" : ""}
                        </strong>{" "}
                        · урок {dateLabel(item.lessonDate)}
                      </p>
                      {item.submittedAt && (
                        <small>
                          Отправлено:{" "}
                          {new Date(item.submittedAt).toLocaleString("ru-RU", {
                            timeZone: "Asia/Qyzylorda",
                          })}
                        </small>
                      )}
                      <button
                        disabled={activity.busy}
                        onClick={() => {
                          if (canLeaveHomework()) setSelected(item);
                        }}
                      >
                        {item.canReview
                          ? "Проверить работу"
                          : item.canSubmit
                            ? "Выполнить задание"
                            : item.submission === "offline"
                              ? "Посмотреть задание"
                              : "Посмотреть работу"}
                      </button>
                    </article>
                  ))}
                </div>
              </section>
            ),
          )}
        </>
      )}
      <div className="dated-toolbar">
        {cursor && (
          <button
            disabled={busy || activity.busy}
            onClick={() => changePage("")}
          >
            В начало списка
          </button>
        )}
        {result?.nextCursor && (
          <button
            disabled={busy || activity.busy}
            onClick={() => changePage(result.nextCursor!)}
          >
            Следующие работы
          </button>
        )}
      </div>
    </section>
  );
}

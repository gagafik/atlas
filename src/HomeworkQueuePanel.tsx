import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import HomeworkPanel from "./HomeworkPanel";
import { canLeaveHomework } from "./homeworkNavigation";
import HomeworkQueueTable, { type OpenedWork } from "./HomeworkQueueTable";
import {
  fieldsFromAnswer,
  listKey,
  mayLeave,
  nextConditions,
  pageExpired,
  periodButtonLabel,
  periodPatch,
  periodRefused,
  queueParams,
  queueTabs,
  type QueueFields,
  type QueueMeta,
  type QueueResult,
} from "./homeworkQueue";
import "./HomeworkQueue.css";

const initial: QueueFields = {
  start: "",
  end: "",
  filter: "",
  history: false,
  search: "",
};
const heading = (o: OpenedWork) =>
  [o.subject, o.group, o.student ?? "всё задание"].join(" · ");

export default function HomeworkQueuePanel({
  workspace,
  child,
}: {
  workspace: string;
  // The child a parent's portal looks at: the queue lists their work only.
  child?: string;
}) {
  // `fields` is what is committed (the tabs, the search, the period); what is only typed lives in its own draft.
  const [fields, setFields] = useState(initial),
    [applied, setApplied] = useState(initial);
  const [result, setResult] = useState<QueueResult | null>(null),
    [meta, setMeta] = useState<QueueMeta | null>(null),
    [error, setError] = useState(""),
    // The server refused the period itself (422), not the page or the connection.
    [refused, setRefused] = useState(false);
  const [cursor, setCursor] = useState(""),
    [epoch, setEpoch] = useState(0),
    [busy, setBusy] = useState(true);
  const [opened, setOpened] = useState<OpenedWork | null>(null);
  const [searchText, setSearchText] = useState("");
  const [periodOpen, setPeriodOpen] = useState(false);
  const [period, setPeriod] = useState({ start: "", end: "", history: false });
  const [activity, setActivity] = useState({ dirty: false, busy: false });
  const initialized = useRef(false);
  const browse = useRef<HTMLDivElement>(null),
    work = useRef<HTMLElement>(null);
  const listScroll = useRef(0),
    returnTo = useRef(""),
    wasOpened = useRef(false),
    shownList = useRef("");
  useEffect(() => {
    const leave = (e: Event) => {
      if (
        !mayLeave(activity, () =>
          window.confirm(
            "В домашней работе есть несохранённые изменения. Уйти без сохранения?",
          ),
        )
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
    setRefused(false);
    // Another list clears the screen; the same page read again (after a review) stays until the new answer comes.
    const key = listKey(workspace, child, applied, cursor);
    const reread = key === shownList.current;
    if (!reread) {
      shownList.current = key;
      setResult(null);
    }
    api(
      `journal/homework-queue?${queueParams(applied, cursor, child)}`,
      workspace,
    )
      .then((r: QueueResult) => {
        if (!live) return;
        setResult(r);
        setMeta({
          staff: r.staff,
          filter: r.filter,
          window: r.window,
          start: r.start,
          end: r.end,
          yearStart: r.yearStart,
          yearEnd: r.yearEnd,
          schoolToday: r.schoolToday,
        });
        if (!initialized.current) {
          initialized.current = true;
          // The default window has no dates of its own: leave the fields empty to keep it.
          setFields((v) => fieldsFromAnswer(v, r));
        }
      })
      .catch((e) => {
        if (!live) return;
        // A page lives fifteen minutes on the server: a later page read again after a review may be gone.
        // Then the list starts from the first page instead of showing an error nobody asked for.
        if (pageExpired(e.status, cursor, reread)) return setCursor("");
        setError(e.message);
        setRefused(periodRefused(e.status, applied));
        setResult(null);
      })
      .finally(() => {
        if (live) setBusy(false);
      });
    return () => {
      live = false;
    };
  }, [workspace, child, applied, cursor, epoch]);
  // The button starts again from the first page; a change made in the opened work reads the same page again.
  function refresh() {
    setCursor("");
    setEpoch((v) => v + 1);
  }
  const reload = () => setEpoch((v) => v + 1);
  // Every change of what is listed asks to leave the opened work first, then starts again from the first page.
  // What is typed in the search goes with it, so the field and the list never disagree.
  function apply(patch: Partial<QueueFields>) {
    if (!canLeaveHomework()) return;
    const next = nextConditions(fields, searchText, patch);
    setFields(next);
    setApplied(next);
    setCursor("");
    setOpened(null);
    setPeriodOpen(false);
  }
  function open(row: OpenedWork) {
    if (!canLeaveHomework()) return;
    listScroll.current = window.scrollY;
    returnTo.current = row.rowKey;
    setOpened(row);
  }
  function changePage(next: string) {
    if (!canLeaveHomework()) return;
    setOpened(null);
    setCursor(next);
  }
  // The opened work takes the top of the screen; closing it brings the list back where it was.
  useEffect(() => {
    if (opened) {
      wasOpened.current = true;
      work.current?.focus({ preventScroll: true });
      work.current?.scrollIntoView({ block: "start" });
      return;
    }
    if (!wasOpened.current) return;
    wasOpened.current = false;
    window.scrollTo(0, listScroll.current);
    const button = browse.current?.querySelector<HTMLElement>(
      `[data-row="${CSS.escape(returnTo.current)}"]`,
    );
    (button ?? browse.current)?.focus({ preventScroll: true });
  }, [opened]);
  return (
    <section className="homework-queue" aria-label="Очередь домашних заданий">
      <div
        className="homework-queue-browse"
        ref={browse}
        tabIndex={-1}
        hidden={!!opened}
      >
        <div className="homework-queue-toolbar">
          {meta && (
            <div className="segmented" role="group" aria-label="Статус работы">
              {queueTabs(meta.staff).map((tab) => (
                <button
                  key={tab.filter}
                  type="button"
                  className={fields.filter === tab.filter ? "selected" : ""}
                  aria-pressed={fields.filter === tab.filter}
                  onClick={() => apply({ filter: tab.filter })}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          )}
          <form
            className="homework-queue-search"
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              apply({ search: searchText });
            }}
          >
            <input
              value={searchText}
              maxLength={100}
              aria-label="Поиск"
              placeholder="Предмет, группа или ученик"
              onChange={(e) => setSearchText(e.target.value)}
            />
            <button>Найти</button>
          </form>
          <details
            className="homework-queue-period"
            open={periodOpen}
            onToggle={(e) => {
              const isOpen = e.currentTarget.open;
              if (isOpen === periodOpen) return;
              setPeriodOpen(isOpen);
              if (isOpen)
                setPeriod({
                  start: fields.start,
                  end: fields.end,
                  history: fields.history,
                });
            }}
          >
            <summary data-refused={refused ? "" : undefined}>
              Период
              {meta || applied.start || applied.end
                ? ": " + periodButtonLabel(meta, applied, refused)
                : ""}
            </summary>
            <div className="homework-queue-period-panel">
              <label className="field">
                <span>С даты</span>
                <input
                  type="date"
                  value={period.start}
                  onChange={(e) =>
                    setPeriod({ ...period, start: e.target.value })
                  }
                />
              </label>
              <label className="field">
                <span>По дату</span>
                <input
                  type="date"
                  value={period.end}
                  onChange={(e) =>
                    setPeriod({ ...period, end: e.target.value })
                  }
                />
              </label>
              <label className="homework-queue-history">
                <input
                  type="checkbox"
                  checked={period.history}
                  onChange={(e) =>
                    setPeriod({ ...period, history: e.target.checked })
                  }
                />{" "}
                Прежние и отменённые уроки
              </label>
              <div className="homework-queue-period-actions">
                <button
                  type="button"
                  className="primary"
                  onClick={() => apply(periodPatch("dates", period, meta))}
                >
                  Показать
                </button>
                {meta && (
                  <button
                    type="button"
                    onClick={() => apply(periodPatch("year", period, meta))}
                  >
                    Весь учебный год
                  </button>
                )}
                {meta && !meta.staff && (
                  <button
                    type="button"
                    onClick={() => apply(periodPatch("default", period, meta))}
                  >
                    Ближайшие сроки
                  </button>
                )}
              </div>
            </div>
          </details>
          <button
            type="button"
            className="homework-queue-refresh"
            disabled={busy}
            aria-label="Обновить список"
            title="Обновить список"
            onClick={refresh}
          >
            ↻
          </button>
        </div>
        <p className="homework-queue-note">
          {meta
            ? meta.window === "upcoming"
              ? "Сначала работы с прошедшим сроком, затем сроки на 14 дней вперёд."
              : meta.staff
                ? "Сначала ближайшие сроки, затем более поздние. Одно задание может продолжаться на следующей странице: список разбит по ученикам."
                : "Сначала ближайшие сроки, затем более поздние."
            : "Опубликованные задания и отправленные ответы."}
        </p>
        <details className="homework-queue-rules">
          <summary>Как считаются сроки</summary>
          <p>
            Опубликованные задания и отправленные ответы. Даты фильтра относятся
            к сроку сдачи; для задания без срока — к дате урока. Срок
            заканчивается в конце дня по местному времени школы.
          </p>
          {meta?.window === "upcoming" && (
            <p>
              Сначала работы с прошедшим сроком — от недавних к ранним, затем
              сроки на 14 дней вперёд. Для другого периода укажите даты.
            </p>
          )}
          {meta?.window === "range" && (
            <p>Сначала ближайшие сроки, затем более поздние.</p>
          )}
          {meta && (
            <p>
              {meta.staff
                ? "Личные черновики учеников здесь не показываются."
                : child
                  ? "Показаны работы выбранного ребёнка."
                  : "Показаны только доступные вам ученики."}
            </p>
          )}
        </details>
        {error && (
          <p className="error-banner" role="alert">
            {error}
          </p>
        )}
        {busy && !result && <p role="status">Загружаем работы…</p>}
        {result && !result.items.length && (
          <p className="info-line">
            Работ по этим условиям на этой странице нет.{" "}
            {result.nextCursor
              ? "Продолжите просмотр следующей страницы."
              : "Попробуйте другой статус, период или включите прежние уроки."}
          </p>
        )}
        {result && result.items.length > 0 && (
          <HomeworkQueueTable
            result={result}
            child={child}
            busy={activity.busy}
            onOpen={open}
          />
        )}
        <div className="homework-queue-paging">
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
          {result && (
            <span className="homework-queue-count" role="status">
              На этой странице: {result.items.length}.
            </span>
          )}
        </div>
      </div>
      {opened && (
        <section
          ref={work}
          tabIndex={-1}
          className="homework-queue-work"
          aria-label="Выбранная домашняя работа"
        >
          <div className="homework-queue-work-head">
            <button
              type="button"
              disabled={activity.busy}
              onClick={() => {
                if (canLeaveHomework()) setOpened(null);
              }}
            >
              ← К списку
            </button>
            <h3>{heading(opened)}</h3>
          </div>
          <HomeworkPanel
            key={opened.rowKey}
            workspace={workspace}
            lessonKey={opened.lessonKey}
            studentId={opened.studentId}
            initiallyOpen
            onActivity={setActivity}
            onChanged={reload}
          />
        </section>
      )}
    </section>
  );
}

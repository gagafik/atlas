import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import HomeworkPanel from "./HomeworkPanel";
import { canLeaveHomework } from "./homeworkNavigation";
import HomeworkQueueTable, { type OpenedWork } from "./HomeworkQueueTable";
import {
  periodLabel,
  queueParams,
  queueTabs,
  type QueueMeta,
  type QueueResult,
} from "./homeworkQueue";
import "./HomeworkQueue.css";

const initial = { start: "", end: "", filter: "", history: false, search: "" };
type Fields = typeof initial;
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
    [error, setError] = useState("");
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
    wasOpened = useRef(false);
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
  }, [workspace, child, applied, cursor, epoch]);
  function refresh() {
    setCursor("");
    setEpoch((v) => v + 1);
  }
  // Every change of what is listed asks to leave the opened work first, then starts again from the first page.
  function apply(patch: Partial<Fields>) {
    if (!canLeaveHomework()) return;
    const next = { ...fields, ...patch };
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
            <summary>Период{meta ? ": " + periodLabel(meta) : ""}</summary>
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
                  onClick={() => apply(period)}
                >
                  Показать
                </button>
                {meta && (
                  <button
                    type="button"
                    onClick={() =>
                      apply({
                        start: meta.yearStart,
                        end: meta.yearEnd,
                        history: period.history,
                      })
                    }
                  >
                    Весь учебный год
                  </button>
                )}
                {meta && !meta.staff && (
                  <button
                    type="button"
                    onClick={() =>
                      apply({ start: "", end: "", history: period.history })
                    }
                  >
                    Ближайшие сроки
                  </button>
                )}
              </div>
            </div>
          </details>
          <button type="button" disabled={busy} onClick={refresh}>
            Обновить
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
            onChanged={refresh}
          />
        </section>
      )}
    </section>
  );
}

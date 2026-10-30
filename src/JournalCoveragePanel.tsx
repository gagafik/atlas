import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { download } from "./utils";
import JournalCoverageReminders from "./JournalCoverageReminders";
import {
  classCardLines,
  drillFilters,
  ruDate,
  schoolDays,
  summaryFilters,
  type ClassSummary,
  type Query,
} from "./journalCoverage";

const labels: Record<string, string> = {
  missing: "Журнал не создан",
  draft: "Нет публикации",
  incomplete: "Заполнен не полностью",
  stale: "Расписание или состав изменились",
  complete: "Заполнен",
  upcoming: "Урок ещё не закончился",
  orphaned: "Публикация вне текущего расписания",
};
type Named = { id: string; name: string };
type Options = {
  modes: string[];
  classes: Record<string, Named[]>;
  teachers: Record<string, Named[]>;
  subjects: Named[];
  yearStart: string;
  yearEnd: string;
  today: string;
  canConfigure: boolean;
  reminders: { enabled: boolean; afterDays: number; weeklyDigest: boolean };
};
type Lesson = {
  lessonKey: string;
  date: string;
  start: string;
  end: string;
  subject: string;
  group: string;
  teachers: string;
  classNames: string[];
  status: string;
  due: boolean;
  pupils: number;
  missingPupils: number;
  unmarkedPupils: number;
  rosterUnknown: boolean;
  topicFilled: boolean;
  needsSync: boolean;
  version: number;
  publishedVersion: number | null;
  hasDraftChanges: boolean;
  canOpen: boolean;
};
type Result = {
  token: string;
  asOf: string;
  counts: Record<string, number>;
  items: Lesson[];
  nextOffset: number | null;
  total: number;
};
type Detail = {
  lesson: Lesson;
  pupils: { id: string; name: string; className: string; issue: string }[];
  nextOffset: number | null;
  total: number;
};
type Summary = {
  token: string;
  asOf: string;
  classes: ClassSummary[];
  // Administrators only: teachers without an account who still have unfilled lessons.
  unbound?: {
    teacherId: string | null;
    teacher: string;
    lessons: number;
    since: string;
    classNames: string[];
  }[];
};
const initial: Query = {
  start: "",
  end: "",
  mode: "classes",
  classId: "",
  teacherId: "",
  subjectId: "",
  status: "issues",
};

export default function JournalCoveragePanel({
  workspace,
  onOpen,
}: {
  workspace: string;
  onOpen?: (key: string, day: string) => void;
}) {
  const [expanded, setExpanded] = useState(false),
    [options, setOptions] = useState<Options | null>(null);
  const [query, setQuery] = useState<Query>(initial),
    [applied, setApplied] = useState<Query | null>(null);
  const [result, setResult] = useState<Result | null>(null),
    [detail, setDetail] = useState<Detail | null>(null),
    [summary, setSummary] = useState<Summary | null>(null);
  const [remindersOpen, setRemindersOpen] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const detailRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (detail) {
      detailRef.current?.focus({ preventScroll: true });
      detailRef.current?.scrollIntoView({ block: "start" });
    }
  }, [detail?.lesson.lessonKey]);
  useEffect(() => {
    if (!expanded || options) return;
    let live = true;
    setBusy(true);
    api("journal/coverage/options", workspace)
      .then((value: Options) => {
        if (!live) return;
        setOptions(value);
        const end =
          value.today < value.yearStart
            ? value.yearStart
            : value.today > value.yearEnd
              ? value.yearEnd
              : value.today;
        const start = new Date(end + "T00:00:00Z");
        start.setUTCDate(start.getUTCDate() - 6);
        setQuery((q) => ({
          ...q,
          mode: value.modes[0],
          start: [start.toISOString().slice(0, 10), value.yearStart]
            .sort()
            .at(-1)!,
          end,
        }));
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
  }, [expanded, workspace, options]);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const change = (values: Partial<Query>, keepSummary = false) => {
    setQuery((q) => ({ ...q, ...values }));
    setResult(null);
    setApplied(null);
    setDetail(null);
    if (!keepSummary) setSummary(null);
    setError("");
  };
  // asOf pins a drill-down to the moment of the class summary; it is sent without a token.
  const load = async (offset = 0, filters?: Query, asOf?: string) => {
    filters ??= offset && applied ? applied : query;
    const value: Result = await api(
      "journal/coverage/report",
      workspace,
      "POST",
      {
        ...filters,
        offset,
        ...(offset && result
          ? { token: result.token, asOf: result.asOf }
          : asOf
            ? { asOf }
            : {}),
      },
    );
    setResult((old) =>
      offset && old
        ? { ...value, items: [...old.items, ...value.items] }
        : value,
    );
    setApplied(filters);
    if (!offset) setDetail(null);
  };
  const classes = async () => {
    setDetail(null);
    setSummary(
      await api(
        "journal/coverage/classes",
        workspace,
        "POST",
        summaryFilters(query),
      ),
    );
  };
  const drill = (classId: string) => {
    if (!summary) return;
    const filters = drillFilters(query, classId);
    change(filters, true);
    void run(() => load(0, filters, summary.asOf));
  };
  const pupils = async (lesson: Lesson, offset = 0) => {
    if (!result || !applied) return;
    const value: Detail = await api(
      "journal/coverage/details",
      workspace,
      "POST",
      {
        ...applied,
        token: result.token,
        asOf: result.asOf,
        lessonKey: lesson.lessonKey,
        day: lesson.date,
        pupilOffset: offset,
      },
    );
    setDetail((old) =>
      offset && old
        ? { ...value, pupils: [...old.pupils, ...value.pupils] }
        : value,
    );
  };
  const csv = async () => {
    if (!result || !applied) return;
    const response = await fetch(
      `/api/journal/coverage/export?workspace=${encodeURIComponent(workspace)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Atlas-Request": "1" },
        body: JSON.stringify({
          ...applied,
          token: result.token,
          asOf: result.asOf,
        }),
      },
    );
    if (!response.ok) {
      if (response.status === 401)
        window.dispatchEvent(new Event("atlas-auth-required"));
      const e = await response.json();
      throw new Error(
        typeof e.detail === "string"
          ? e.detail
          : "Не удалось скачать проверку.",
      );
    }
    download(
      `Заполнение-журнала-${applied.start}-${applied.end}.csv`,
      "\ufeff" + (await response.text()).replace(/^\ufeff/, ""),
      "text/csv;charset=utf-8",
    );
  };
  return (
    <details
      className="absence-panel coverage-panel"
      open={expanded}
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Контроль заполнения журналов</summary>
      {expanded && (
        <>
          <p>
            Проверка по текущему расписанию и составу учеников: отмены, переносы
            и замены учитываются. Урок требует заполнения после его окончания по
            времени школы. Проверяются публикация, тема и отметка каждого
            известного ученика.
          </p>
          <p>
            Прошлые даты проверяются по версии расписания и составам,
            действовавшим в тот день; опубликованный прошедший урок — по своему
            опубликованному составу. Домашнее задание и оценки не обязательны
            для каждого урока.
          </p>
          {options?.reminders.enabled && (
            <p>
              Если урок не заполнен через{" "}
              {schoolDays(options.reminders.afterDays)} после него, учитель
              получает одно напоминание в уведомлениях.
            </p>
          )}
          {error && (
            <p role="alert" className="error-banner">
              {error}
            </p>
          )}
          {busy && <p role="status">Проверяю журналы…</p>}
          {options && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void run(() => load());
              }}
            >
              <fieldset disabled={busy} className="access-fields">
                <div className="dated-toolbar">
                  <label className="field">
                    <span>Начало проверки</span>
                    <input
                      type="date"
                      required
                      min={options.yearStart}
                      max={options.yearEnd}
                      value={query.start}
                      onChange={(e) => change({ start: e.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span>Конец проверки</span>
                    <input
                      type="date"
                      required
                      min={options.yearStart}
                      max={options.yearEnd}
                      value={query.end}
                      onChange={(e) => change({ end: e.target.value })}
                    />
                  </label>
                </div>
                <label className="field">
                  <span>Область проверки</span>
                  <select
                    value={query.mode}
                    onChange={(e) =>
                      change({
                        mode: e.target.value,
                        classId: "",
                        teacherId: "",
                      })
                    }
                  >
                    {options.modes.map((m) => (
                      <option key={m} value={m}>
                        {m === "teaching"
                          ? "Мои уроки"
                          : "Классы под моим контролем"}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Класс проверки</span>
                  <select
                    value={query.classId}
                    onChange={(e) => change({ classId: e.target.value })}
                  >
                    <option value="">Все доступные классы</option>
                    {options.classes[query.mode]?.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Учитель проверки</span>
                  <select
                    value={query.teacherId}
                    onChange={(e) => change({ teacherId: e.target.value })}
                  >
                    <option value="">Все доступные учителя</option>
                    {options.teachers[query.mode]?.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Предмет проверки</span>
                  <select
                    value={query.subjectId}
                    onChange={(e) => change({ subjectId: e.target.value })}
                  >
                    <option value="">Все предметы</option>
                    {options.subjects.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Состояние журнала</span>
                  <select
                    value={query.status}
                    onChange={(e) => change({ status: e.target.value })}
                  >
                    <option value="issues">Требуют внимания</option>
                    <option value="all">Все состояния</option>
                    {Object.entries(labels).map(([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="submit">Проверить заполнение</button>
                {options.modes.includes("classes") && (
                  <button type="button" onClick={() => void run(classes)}>
                    Сводка по классам
                  </button>
                )}
              </fieldset>
            </form>
          )}
          {options?.canConfigure && (
            <details
              className="absence-panel"
              open={remindersOpen}
              onToggle={(e) => setRemindersOpen(e.currentTarget.open)}
            >
              <summary>Напоминания учителям</summary>
              {remindersOpen && (
                <JournalCoverageReminders
                  workspace={workspace}
                  onSaved={(value) =>
                    setOptions((o) => o && { ...o, reminders: value })
                  }
                />
              )}
            </details>
          )}
          {summary && (
            <section aria-label="Сводка по классам">
              <h3>Сводка по классам</h3>
              <p>
                На момент {new Date(summary.asOf).toLocaleString("ru-RU")} · по
                времени вашего устройства. Урок, общий для нескольких классов,
                учитывается в каждом из них.
              </p>
              {!summary.classes.length && <p>Классов для сводки нет.</p>}
              <div className="attendance-cards">
                {summary.classes.map((c) => (
                  <article className="absence-card" key={c.classId}>
                    <h4>{c.className}</h4>
                    {classCardLines(c).map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                    <button disabled={busy} onClick={() => drill(c.classId)}>
                      Уроки класса
                    </button>
                  </article>
                ))}
              </div>
              {!!summary.unbound?.length && (
                <section aria-label="Уроки без учётной записи учителя">
                  <h3>Уроки без учётной записи учителя</h3>
                  <p>Привяжите учётную запись в разделе доступа.</p>
                  <div className="attendance-cards">
                    {summary.unbound.map((g) => (
                      <article
                        className="absence-card"
                        key={g.teacherId ?? "none"}
                      >
                        <h4>{g.teacher}</h4>
                        <p>
                          Не заполнено уроков: {g.lessons} · с {ruDate(g.since)}
                        </p>
                        <p>Классы: {g.classNames.join(", ")}</p>
                      </article>
                    ))}
                  </div>
                </section>
              )}
            </section>
          )}
          {result && applied && (
            <section aria-label="Результат контроля журналов">
              <h3>
                Проверка: {applied.start} — {applied.end}
              </h3>
              <p>
                На момент {new Date(result.asOf).toLocaleString("ru-RU")} · по
                времени вашего устройства.
              </p>
              <p>
                Запланировано уроков: <strong>{result.counts.expected}</strong>.
                Отменено и исключено из проверки:{" "}
                <strong>{result.counts.cancelled}</strong>.
              </p>
              <div className="attendance-counts">
                {Object.entries(labels).map(([id, name]) => (
                  <span key={id}>
                    {name}: <strong>{result.counts[id]}</strong>
                  </span>
                ))}
              </div>
              <p>
                Уже прошедшие уроки: нет записи ученика —{" "}
                {result.counts.missingPupils}; запись есть без отметки —{" "}
                {result.counts.unmarkedPupils}. Это число записей на уроках, а
                не разных учеников.
              </p>
              <button disabled={busy} onClick={() => void run(csv)}>
                Скачать проверку CSV
              </button>
              <p>
                По выбранному состоянию: {result.total}. CSV включает все
                страницы. Причины отсутствия, оценки и комментарии не
                выгружаются.
              </p>
              {!result.total && <p>По выбранным условиям уроков нет.</p>}
              <div className="attendance-cards">
                {result.items.map((row) => (
                  <article className="absence-card" key={row.lessonKey}>
                    <h4>
                      {row.date} · {row.start}–{row.end} · {row.subject}
                    </h4>
                    <p>
                      {row.group} · {row.teachers || "Учитель не назначен"}
                    </p>
                    <p>
                      <strong>{labels[row.status]}</strong>
                    </p>
                    {row.status === "orphaned" ? (
                      <p>
                        Эта публикация больше не соответствует ожидаемому уроку
                        периода. Проверьте перенос или отмену; публикация не
                        удаляется автоматически.
                      </p>
                    ) : (
                      <>
                        <p>
                          Ученики в области проверки: {row.pupils}. Нет записи:{" "}
                          {row.missingPupils}. Не отмечено: {row.unmarkedPupils}
                          .
                        </p>
                        {!row.topicFilled && <p>Тема ещё не опубликована.</p>}
                        {row.rosterUnknown && (
                          <p>
                            Проверьте состав группы и численность класса:
                            полнота списка учеников не подтверждена.
                          </p>
                        )}
                        {row.needsSync && (
                          <p>
                            Публикация отличается от текущих данных урока. Нужны
                            проверка, синхронизация и повторная публикация.
                          </p>
                        )}
                        {row.missingPupils + row.unmarkedPupils > 0 && (
                          <button
                            disabled={busy}
                            onClick={() => void run(() => pupils(row))}
                          >
                            Ученики без отметки
                          </button>
                        )}
                      </>
                    )}
                    {row.hasDraftChanges && (
                      <p>Есть изменения после последней публикации.</p>
                    )}
                    {row.canOpen && onOpen && (
                      <button
                        disabled={busy}
                        onClick={() => onOpen(row.lessonKey, row.date)}
                      >
                        Открыть журнал урока
                      </button>
                    )}
                  </article>
                ))}
              </div>
              {result.nextOffset !== null && (
                <button
                  disabled={busy}
                  onClick={() => void run(() => load(result.nextOffset!))}
                >
                  Следующие уроки
                </button>
              )}
              {detail && (
                <section
                  className="absence-card"
                  ref={detailRef}
                  tabIndex={-1}
                  aria-label="Ученики без отметки"
                >
                  <h3>
                    {detail.lesson.date} · {detail.lesson.subject} ·{" "}
                    {detail.lesson.group}
                  </h3>
                  <p>Записей для проверки: {detail.total}</p>
                  {detail.pupils.map((p) => (
                    <p key={p.id}>
                      <strong>{p.name}</strong> · {p.className} ·{" "}
                      {p.issue === "missing"
                        ? "Нет опубликованной записи"
                        : "Отметка не заполнена"}
                    </p>
                  ))}
                  {!detail.total && (
                    <p>У всех известных учеников есть отметка.</p>
                  )}
                  {detail.nextOffset !== null && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(() =>
                          pupils(detail.lesson, detail.nextOffset!),
                        )
                      }
                    >
                      Следующие ученики
                    </button>
                  )}
                </section>
              )}
            </section>
          )}
        </>
      )}
    </details>
  );
}

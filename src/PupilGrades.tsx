import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { api } from "./api";
import CategoryBadge from "./CategoryBadge";
import {
  categoryOf,
  textColor,
  useAssessmentCategories,
  type CategoryConfig,
} from "./assessmentCategories";
import { finalLabel } from "./finalGrades";
import { FamilyFeedView, useFamilyFeed } from "./GradeFeed";
import { PeriodPicker } from "./JournalPickers";
import Popover from "./Popover";
import {
  journalCsv,
  loadRemainingReport,
  mergeReportPage,
  type JournalReportPage,
} from "./journal";
import {
  bandSegments,
  behaviourRow,
  chartBlocks,
  dayText,
  formatNumber,
  groupNotes,
  marksByDate,
  markText,
  noteKind,
  subjectRows,
  type BehaviourFamily,
  type BehaviourNote,
  type FinalMark,
  type NoteGrouping,
  type PeriodChoice,
  type PeriodSelection,
  type PupilMark,
  type SubjectRow,
} from "./pupilJournal";
import { usePupilPeriod } from "./usePupilPeriod";

type Tab = "summary" | "date" | "notes" | "chart" | "feed";
const TAB_NAMES: Record<Tab, string> = {
  summary: "Сводка оценок",
  date: "По дате",
  notes: "Заметки",
  chart: "Оценки на графике",
  feed: "Новые оценки",
};
const PAGE = 100;
// The least width of a work on the chart: a narrower block cannot show its points.
const BLOCK_WIDTH = 68;
const NOTE_PAGES = 10;
const stamp = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("ru-RU", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : "";

// The notes of the period: page one gives the totals and the subjects, the tab «Заметки» reads on.
function useBehaviour(
  workspace: string,
  studentId: string,
  start: string,
  end: string,
) {
  const key = `${workspace}|${studentId}|${start}|${end}`;
  const [state, setState] = useState<{
    key: string;
    family: BehaviourFamily | null;
    items: BehaviourNote[];
    missing: boolean;
  }>({ key, family: null, items: [], missing: false });
  const [epoch, setEpoch] = useState(0);
  const [reading, setReading] = useState(false);
  const query = (before?: number) =>
    new URLSearchParams({
      studentId,
      start,
      end,
      ...(before ? { before: String(before) } : {}),
    });
  useEffect(() => {
    let live = true;
    api(`behaviour/family?${query()}`, workspace)
      .then((family: BehaviourFamily) => {
        if (live)
          setState({ key, family, items: family.items, missing: false });
      })
      .catch(() => {
        // A child the account no longer sees, or a school without the module: the notes are simply not shown.
        if (live) setState({ key, family: null, items: [], missing: true });
      });
    return () => {
      live = false;
    };
  }, [key, epoch]);
  const current = state.key === key ? state : null;
  // Reads the next pages (at most ten of fifty) for the tab «Заметки».
  async function readAll() {
    if (!current?.family || reading) return;
    setReading(true);
    try {
      let items = current.items,
        before = current.family.nextBefore,
        pages = 1;
      while (before && pages++ < NOTE_PAGES) {
        const page: BehaviourFamily = await api(
          `behaviour/family?${query(before)}`,
          workspace,
        );
        items = [...items, ...page.items];
        before = page.nextBefore;
        setState((old) => (old.key === key ? { ...old, items } : old));
      }
      setState((old) =>
        old.key === key
          ? {
              ...old,
              family: old.family && { ...old.family, nextBefore: before },
            }
          : old,
      );
    } finally {
      setReading(false);
    }
  }
  return {
    family: current?.family ?? null,
    items: current?.items ?? [],
    missing: !!current?.missing,
    reading,
    readAll,
    reload: () => setEpoch((n) => n + 1),
  };
}

// The published period finals of the pupil by subject, for the period being shown.
function useFinals(workspace: string, studentId: string, periodId: string) {
  const [periods, setPeriods] = useState<Set<string> | null>(null);
  useEffect(() => {
    let live = true;
    api("final-grades/options", workspace)
      .then((r: { periods?: { id: string }[] }) => {
        if (live) setPeriods(new Set((r.periods ?? []).map((p) => p.id)));
      })
      .catch(() => {
        if (live) setPeriods(new Set());
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  const [finals, setFinals] = useState<Record<string, FinalMark>>({});
  const has = !!periods?.has(periodId);
  useEffect(() => {
    setFinals({});
    if (!has) return;
    let live = true;
    api(
      `final-grades/report?${new URLSearchParams({ studentId, periodId })}`,
      workspace,
    )
      .then(
        (r: {
          items: { subjectId: string; outcome: string; label: string }[];
        }) => {
          if (live)
            setFinals(
              Object.fromEntries(
                r.items.map((i) => [
                  i.subjectId,
                  { label: finalLabel(i), outcome: i.outcome },
                ]),
              ),
            );
        },
      )
      // The family link may have ended meanwhile: the final column then stays empty.
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [workspace, studentId, periodId, has]);
  return finals;
}

const chipText = (m: PupilMark) =>
  m.kind === "points" && m.score !== null
    ? `${formatNumber(m.score)} / ${formatNumber(m.maxScore)}`
    : m.label || "—";

function MarkDetails({
  mark,
  categories,
}: {
  mark: PupilMark;
  categories: CategoryConfig | null;
}) {
  const category = categoryOf(mark, categories);
  return (
    <dl className="pupil-details">
      <dt>Предмет</dt>
      <dd>{mark.subject}</dd>
      <dt>Категория</dt>
      <dd>
        <CategoryBadge category={category} /> {category.title}
      </dd>
      {mark.kind === "points" && (
        <>
          <dt>Вес</dt>
          <dd>вес {formatNumber(mark.weight)}x</dd>
        </>
      )}
      <dt>Оценка</dt>
      <dd>
        {mark.kind === "points" && mark.score !== null
          ? markText(mark).replace(" = ", " (") + ")"
          : mark.label || "—"}
      </dd>
      <dt>Урок</dt>
      <dd>
        {dayText(mark.date)}, {mark.start}–{mark.end}
      </dd>
      {mark.teachers && (
        <>
          <dt>Учитель</dt>
          <dd>{mark.teachers}</dd>
        </>
      )}
      {mark.comment && (
        <>
          <dt>Комментарий</dt>
          <dd>{mark.comment}</dd>
        </>
      )}
    </dl>
  );
}

function Chip({
  mark,
  categories,
  pupil,
  onOpen,
}: {
  mark: PupilMark;
  categories: CategoryConfig | null;
  pupil: boolean;
  onOpen: (mark: PupilMark) => void;
}) {
  const color = categoryOf(mark, categories).color;
  return (
    <button
      type="button"
      className={`pupil-chip${mark.unsigned ? " pupil-chip-new" : ""}`}
      style={{ background: color, color: textColor(color) }}
      title={`${mark.name}, ${dayText(mark.date)}`}
      aria-label={`${mark.name}: ${chipText(mark)}, ${dayText(mark.date)}${
        mark.unsigned ? (pupil ? ", не просмотрена" : ", не подписана") : ""
      }`}
      onClick={() => onOpen(mark)}
    >
      {chipText(mark)}
    </button>
  );
}

function Average({
  row,
  categories,
}: {
  row: SubjectRow;
  categories: CategoryConfig | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <span className="pupil-average">
        {row.average !== null ? `${formatNumber(row.average)}%` : "—"}
      </span>{" "}
      <button
        type="button"
        className="secondary pupil-info"
        aria-label={`Как считается среднее: ${row.subject}`}
        onClick={() => setOpen(true)}
      >
        ?
      </button>
      {open && (
        <Popover
          title={`Среднее: ${row.subject}`}
          onClose={() => setOpen(false)}
        >
          <dl className="pupil-details">
            {row.rule && (
              <>
                <dt>Правило школы</dt>
                <dd>{row.rule}</dd>
              </>
            )}
            {row.average === null && (
              <>
                <dt>Среднее</dt>
                <dd>
                  По этому правилу среднее не считается: нет баллов или оценок
                  не во всех категориях.
                </dd>
              </>
            )}
            {!!row.categories.length && (
              <>
                <dt>По категориям</dt>
                <dd>
                  {row.categories.map((k) => (
                    <span key={k.id} className="pupil-category-share">
                      <CategoryBadge
                        category={categoryOf({ category: k.id }, categories)}
                      />
                      {formatNumber(k.percentage)}%
                    </span>
                  ))}
                </dd>
              </>
            )}
            <dt>Обычный средний процент</dt>
            <dd>
              {row.plain !== null
                ? `${formatNumber(row.plain)}% (сумма процентов с весами работ, делённая на сумму весов)`
                : "—"}
            </dd>
            <dt>Оценок</dt>
            <dd>
              с баллами: {row.pointsCount}, текстовых: {row.labelCount}
            </dd>
          </dl>
        </Popover>
      )}
    </>
  );
}

function SummaryTab({
  rows,
  behaviour,
  notesShown,
  categories,
  pupil,
  onMark,
}: {
  rows: SubjectRow[];
  behaviour: { praise: number; remark: number } | null;
  notesShown: boolean;
  categories: CategoryConfig | null;
  pupil: boolean;
  onMark: (m: PupilMark) => void;
}) {
  if (!rows.length)
    return (
      <p className="empty-state">Опубликованных уроков за этот период нет.</p>
    );
  return (
    <div className="pupil-scroll">
      <table className="pupil-table">
        <thead>
          <tr>
            <th scope="col" className="pupil-sticky">
              Предмет
            </th>
            <th scope="col">Оценки</th>
            <th scope="col">Среднее</th>
            <th scope="col">Итог</th>
            {notesShown && <th scope="col">Заметки</th>}
          </tr>
        </thead>
        <tbody>
          {behaviour && (
            <tr>
              <th scope="row" className="pupil-sticky">
                Поведение
              </th>
              <td className="pupil-muted">—</td>
              <td className="pupil-muted">—</td>
              <td className="pupil-muted">—</td>
              <td>
                <NoteCounts
                  praise={behaviour.praise}
                  remark={behaviour.remark}
                />
              </td>
            </tr>
          )}
          {rows.map((row) => (
            <tr key={row.subjectId}>
              <th scope="row" className="pupil-sticky" title={row.subject}>
                {row.subject}
              </th>
              <td>
                <div className="pupil-chips">
                  {row.marks.map((m) => (
                    <Chip
                      key={`${m.lessonKey}:${m.assessmentId}`}
                      mark={m}
                      categories={categories}
                      pupil={pupil}
                      onOpen={onMark}
                    />
                  ))}
                  {!row.marks.length && <span className="pupil-muted">—</span>}
                </div>
              </td>
              <td>
                <Average row={row} categories={categories} />
              </td>
              <td>{row.final?.label || "—"}</td>
              {notesShown && (
                <td>
                  <NoteCounts
                    praise={row.notes.praise}
                    remark={row.notes.remark}
                  />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NoteCounts({ praise, remark }: { praise: number; remark: number }) {
  if (!praise && !remark) return <span className="pupil-muted">—</span>;
  return (
    <span className="pupil-note-counts">
      {!!praise && (
        <span className="pupil-praise" title="Похвалы">
          Похвал: {praise}
        </span>
      )}
      {!!remark && (
        <span className="pupil-remark" title="Замечания">
          Замечаний: {remark}
        </span>
      )}
    </span>
  );
}

function DateTab({
  marks,
  categories,
}: {
  marks: PupilMark[];
  categories: CategoryConfig | null;
}) {
  const ordered = useMemo(() => marksByDate(marks), [marks]);
  const [shown, setShown] = useState(PAGE);
  if (!ordered.length)
    return <p className="empty-state">Оценок за этот период нет.</p>;
  return (
    <>
      <ul className="pupil-mark-list">
        {ordered.slice(0, shown).map((m) => {
          const category = categoryOf(m, categories);
          return (
            <li
              key={`${m.lessonKey}:${m.assessmentId}`}
              className={m.unsigned ? "pupil-mark-new" : undefined}
            >
              <div className="pupil-mark-head">
                <strong>{m.subject}</strong>
                <span>
                  {dayText(m.date)}, {m.start}–{m.end}
                </span>
              </div>
              <p>
                <CategoryBadge category={category} />
                {m.name}:{" "}
                <strong>
                  {m.kind === "points" && m.score !== null
                    ? markText(m).replace(" = ", " (") + ")"
                    : m.label || "—"}
                </strong>
                {m.kind === "points" && ` · вес ${formatNumber(m.weight)}x`}
              </p>
              {m.comment && <p>Комментарий учителя: {m.comment}</p>}
              {m.teachers && <p className="pupil-muted">{m.teachers}</p>}
            </li>
          );
        })}
      </ul>
      {shown < ordered.length && (
        <button type="button" onClick={() => setShown((n) => n + PAGE)}>
          Показать ещё
        </button>
      )}
    </>
  );
}

const GROUPINGS: [NoteGrouping, string][] = [
  ["subject", "Предмет"],
  ["teacher", "Учитель"],
  ["date", "Дата"],
  ["type", "Тип"],
];
function NotesTab({
  workspace,
  behaviour,
}: {
  workspace: string;
  behaviour: ReturnType<typeof useBehaviour>;
}) {
  const [by, setBy] = useState<NoteGrouping>("subject");
  const { family, readAll } = behaviour;
  useEffect(() => {
    if (family?.nextBefore) void readAll();
  }, [family?.nextBefore === null]);
  const groups = useMemo(
    () => groupNotes(behaviour.items, by),
    [behaviour.items, by],
  );
  return (
    <>
      <div className="pupil-notes-bar">
        <div
          role="group"
          aria-label="Группировка заметок"
          className="pupil-switch"
        >
          {GROUPINGS.map(([id, name]) => (
            <button
              key={id}
              type="button"
              aria-pressed={by === id}
              onClick={() => setBy(id)}
            >
              {name}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="secondary"
          onClick={() =>
            window.dispatchEvent(
              new CustomEvent("atlas-open-behaviour", {
                detail: { workspace },
              }),
            )
          }
        >
          Открыть «Официальные письма»
        </button>
      </div>
      {behaviour.reading && <p role="status">Загружаю заметки…</p>}
      {!behaviour.items.length && !behaviour.reading && (
        <p className="empty-state">Заметок за этот период нет.</p>
      )}
      {groups.map((g) => (
        <section key={g.key} className="pupil-note-group">
          <h3>{g.title}</h3>
          <ul className="pupil-mark-list">
            {g.items.map((n) => (
              <li key={n.id}>
                <div className="pupil-mark-head">
                  <strong
                    className={
                      n.kind === "praise" ? "pupil-praise" : "pupil-remark"
                    }
                  >
                    {noteKind(n)}
                  </strong>
                  <span>{dayText(n.day)}</span>
                </div>
                <p>
                  {n.category} · {n.levelTitle}
                </p>
                {n.text && <p>{n.text}</p>}
                <p className="pupil-muted">
                  {n.author}
                  {n.lesson && ` · ${n.lesson}`}
                  {n.letterNumber && ` · письмо № ${n.letterNumber}`}
                  {n.requireAck &&
                    (n.acknowledged
                      ? " · родитель ознакомлен"
                      : " · ждёт ознакомления родителя")}
                </p>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

function ChartTab({
  rows,
  scale,
  categories,
}: {
  rows: SubjectRow[];
  scale: ReturnType<typeof usePupilPeriod>["data"] extends infer D
    ? D extends { scale: infer S }
      ? S
      : never
    : never;
  categories: CategoryConfig | null;
}) {
  const segments = bandSegments(scale);
  const charts = rows
    .map((row) => ({ row, ...chartBlocks(row.marks) }))
    .filter((c) => c.blocks.length);
  if (!charts.length)
    return <p className="empty-state">Оценок с баллами за этот период нет.</p>;
  return (
    <>
      <p className="pupil-muted">
        Каждая работа — блок шириной по весу и максимуму баллов, закрашенный до
        полученного процента.
        {segments.length ? ` Фон — школьная шкала «${scale!.name}».` : ""}
      </p>
      {charts.map(({ row, blocks, excluded }) => (
        <section key={row.subjectId} className="pupil-chart-row">
          <h3>
            {row.subject}
            {row.average !== null && (
              <span className="pupil-average">
                Среднее: {formatNumber(row.average)}%
              </span>
            )}
          </h3>
          <div
            className="pupil-chart-bar"
            role="img"
            aria-label={`${row.subject}: ${blocks.map((b) => b.text).join("; ")}${
              row.average !== null
                ? `; среднее ${formatNumber(row.average)}%`
                : ""
            }`}
          >
            <div
              className={`pupil-bands${segments.length ? "" : " pupil-bands-plain"}`}
            >
              {segments.map((s) => (
                <span
                  key={s.label}
                  style={{
                    left: `${s.from}%`,
                    width: `${s.to - s.from}%`,
                    background: `hsl(${segments.length > 1 ? (s.tone / (segments.length - 1)) * 120 : 120} 60% 82%)`,
                  }}
                >
                  {s.label}
                </span>
              ))}
            </div>
            {row.average !== null && (
              <span
                className="pupil-marker"
                style={{
                  left: `${Math.min(100, Math.max(0, row.average))}%`,
                }}
                aria-hidden="true"
              >
                <b>{formatNumber(row.average)}%</b>
              </span>
            )}
            <div className="pupil-chart-scroll">
              <div
                className="pupil-blocks"
                style={{ minWidth: `${blocks.length * BLOCK_WIDTH}px` }}
              >
                {blocks.map((b) => {
                  const category = categoryOf(
                    { category: b.category },
                    categories,
                  );
                  return (
                    <div
                      key={b.key}
                      className="pupil-block"
                      style={{ flexBasis: `${b.share * 100}%` }}
                      title={`${b.text}, ${category.name}`}
                    >
                      <span
                        className="pupil-block-fill"
                        style={{
                          width: `${Math.min(100, b.percent)}%`,
                          background: category.color,
                        }}
                      />
                      <span className="pupil-block-text">
                        {b.text}
                        <small>{category.name}</small>
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
          {excluded > 0 && (
            <p className="pupil-muted">Работ без баллов: {excluded}</p>
          )}
        </section>
      ))}
    </>
  );
}

export default function PupilGrades({
  workspace,
  child,
  choices,
  period,
  onPeriod,
}: {
  workspace: string;
  child: string;
  choices: PeriodChoice[];
  period: PeriodSelection;
  onPeriod: (value: PeriodSelection) => void;
}) {
  const categories = useAssessmentCategories(workspace);
  const data = usePupilPeriod(workspace, child, period.start, period.end);
  const feed = useFamilyFeed(workspace, child, () => data.reload());
  const behaviour = useBehaviour(workspace, child, period.start, period.end);
  const finals = useFinals(workspace, child, period.id);
  const [tab, setTab] = useState<Tab>("summary");
  const [mark, setMark] = useState<PupilMark | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const notesShown = !behaviour.missing;
  const rows = useMemo(
    () =>
      data.data
        ? subjectRows(data.data, finals, behaviour.family?.subjects ?? [])
        : [],
    [data.data, finals, behaviour.family],
  );
  const tabs = (Object.keys(TAB_NAMES) as Tab[]).filter(
    (t) => t !== "notes" || notesShown,
  );
  useEffect(() => {
    if (tab === "notes" && !notesShown) setTab("summary");
  }, [tab, notesShown]);
  // Arrows, Home and End walk the tabs; only the chosen one is in the Tab order.
  const moveTab = (e: KeyboardEvent) => {
    const at = tabs.indexOf(tab);
    const to =
      e.key === "ArrowRight"
        ? (at + 1) % tabs.length
        : e.key === "ArrowLeft"
          ? (at + tabs.length - 1) % tabs.length
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? tabs.length - 1
              : -1;
    if (to < 0) return;
    e.preventDefault();
    setTab(tabs[to]);
    document.getElementById(`pupil-tab-${tabs[to]}`)?.focus();
  };
  async function csv() {
    setBusy(true);
    setProblem("");
    try {
      const params = (after?: string) =>
        new URLSearchParams({
          studentId: child,
          start: period.start,
          end: period.end,
          limit: "200",
          ...(after ? { after } : {}),
        });
      const first = mergeReportPage(
        null,
        (await api(
          `journal/report?${params()}`,
          workspace,
        )) as JournalReportPage,
      );
      const all = await loadRemainingReport(first, (after) =>
        api(`journal/report?${params(after)}`, workspace),
      );
      journalCsv(all.rows, "Личный журнал.csv", categories, all.absenceCodes);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const state = feed.feed;
  const unsigned = feed.unsigned;
  return (
    <section className="pupil-screen" aria-label="Оценки">
      <div className="pupil-toolbar">
        <PeriodPicker choices={choices} value={period} onChange={onPeriod} />
        <div className="pupil-actions">
          <button
            type="button"
            disabled={data.loading}
            onClick={() => {
              data.reload();
              behaviour.reload();
              feed.reload();
            }}
          >
            Обновить
          </button>
          <button
            type="button"
            disabled={busy || !data.data?.marks.length}
            onClick={() => void csv()}
          >
            Скачать CSV
          </button>
          <button type="button" onClick={() => window.print()}>
            Печать
          </button>
          {state?.canSign && (
            <button
              type="button"
              className="primary"
              disabled={
                feed.busy || !unsigned || !state.latest || feed.unseen > 0
              }
              onClick={() => void feed.sign()}
            >
              {feed.pupil ? "Просмотрено" : "Подписать оценки"}
            </button>
          )}
        </div>
      </div>
      {(state?.signedAt || feed.unseen > 0 || feed.error || feed.notice) && (
        <div className="pupil-sign-line">
          {state?.signedAt && (
            <span className="pupil-muted">
              Последняя подпись: {stamp(state.signedAt)}
            </span>
          )}
          {feed.unseen > 0 && (
            <span className="pupil-muted">
              Ещё {feed.unseen}{" "}
              {feed.pupil ? "непросмотренных" : "неподписанных"} оценок:
              откройте вкладку «Новые оценки» и нажмите «Показать ещё».
            </span>
          )}
          {feed.error && (
            <span role="alert" className="pupil-problem">
              {feed.error}
            </span>
          )}
          {feed.notice && <span role="status">{feed.notice}</span>}
        </div>
      )}
      {problem && (
        <p role="alert" className="pupil-problem">
          {problem}
        </p>
      )}
      {data.error && (
        <p role="alert" className="error-banner">
          {data.error}
        </p>
      )}
      <div
        role="tablist"
        aria-label="Разделы оценок"
        className="pupil-tabs"
        onKeyDown={moveTab}
      >
        {tabs.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            id={`pupil-tab-${t}`}
            aria-selected={tab === t}
            aria-controls="pupil-tabpanel"
            tabIndex={tab === t ? 0 : -1}
            onClick={() => setTab(t)}
          >
            {TAB_NAMES[t]}
            {t === "feed" && unsigned > 0 && (
              <span className="pupil-count">{unsigned}</span>
            )}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id="pupil-tabpanel"
        aria-labelledby={`pupil-tab-${tab}`}
        className="pupil-panel"
      >
        {data.loading && !data.data && <p role="status">Загружаю оценки…</p>}
        {data.data && tab === "summary" && (
          <SummaryTab
            rows={rows}
            behaviour={behaviourRow(behaviour.family)}
            notesShown={notesShown}
            categories={categories}
            pupil={feed.pupil}
            onMark={setMark}
          />
        )}
        {data.data && tab === "date" && (
          <DateTab marks={data.data.marks} categories={categories} />
        )}
        {tab === "notes" && notesShown && (
          <NotesTab workspace={workspace} behaviour={behaviour} />
        )}
        {data.data && tab === "chart" && (
          <ChartTab
            rows={rows}
            scale={data.data.scale}
            categories={categories}
          />
        )}
        {tab === "feed" && <FamilyFeedView feed={feed} showSign={false} />}
      </div>
      {mark && (
        <Popover title={mark.name} onClose={() => setMark(null)}>
          <MarkDetails mark={mark} categories={categories} />
        </Popover>
      )}
    </section>
  );
}

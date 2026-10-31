import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { api } from "./api";
import HomeworkPanel from "./HomeworkPanel";
import HomeworkParallelPanel from "./HomeworkParallelPanel";
import { nextLessonText, whileCurrent, type NextLesson } from "./homeworkBulk";
import LessonMaterials from "./LessonMaterials";
import LessonTopic, { SavedPlanTopic } from "./LessonTopic";
import AssignmentFilesPanel, {
  SavedMaterialLinks,
} from "./AssignmentFilesPanel";
import AbsencePanel from "./AbsencePanel";
import { useLessonMarks } from "./LessonMarks";
import LessonPupilCards from "./LessonPupilCards";
import AttendancePanel from "./AttendancePanel";
import AttendanceGridPanel from "./AttendanceGridPanel";
import JournalCoveragePanel from "./JournalCoveragePanel";
import CourseTablePanel from "./CourseTablePanel";
import JournalPeriodsPanel from "./JournalPeriodsPanel";
import FinalGradesPanel from "./FinalGradesPanel";
import GradeFeed from "./GradeFeed";
import CategoryBadge from "./CategoryBadge";
import {
  WEIGHT_PRESETS,
  bookingDefault,
  categoryOf,
  presetOf,
  useAssessmentCategories,
  type CategoryConfig,
} from "./assessmentCategories";
import {
  absenceCodeNames,
  attendanceNames,
  attendanceProblem,
  attendanceText,
  gradeLabel,
  journalCsv,
  documentRows,
  numberField,
  numberValue,
  contentProblem,
  withKind,
  hasEarlierRevisions,
  defaultReportPeriod,
  mergeReportPage,
  loadRemainingReport,
  journalPanels,
  lessonsText,
  noAbsentees,
  withNoAbsentees,
  type Assessment,
  type Grade,
  type JournalContent,
  type JournalDocument,
  type JournalEntry,
  type JournalLesson,
  type JournalPanelName,
  type JournalReport,
  type JournalReportPage,
  type JournalRow,
  type PupilRecord,
} from "./journal";

interface Options {
  staff: boolean;
  // Which management panels have anything for this account; the server still enforces rights.
  panels?: Partial<Record<JournalPanelName, boolean>>;
  yearStart: string;
  yearEnd: string;
  students: { id: string; name: string }[];
}
interface DayLesson {
  lessonKey: string;
  lesson: JournalLesson;
  studentCount: number;
  version: number;
  publishedVersion: number | null;
  hasPublished: boolean;
  archived: boolean;
  frozen?: boolean;
}
interface History {
  version: number;
  updated: string;
  actor: string;
  action: string;
  document?: JournalDocument;
}

function PublishedRows({
  rows,
  workspace,
  categories,
  codeNames,
}: {
  rows: JournalRow[];
  workspace?: string;
  categories?: CategoryConfig | null;
  codeNames?: Record<string, string>;
}) {
  return (
    <div className="journal-published-list">
      {rows.map((row) => (
        <article key={row.lessonKey + row.record.studentId}>
          <h3>
            {row.lesson.date} · {row.lesson.start}–{row.lesson.end} ·{" "}
            {row.lesson.subject}
          </h3>
          <p>
            {row.student.name} · {row.lesson.group} · {row.lesson.teachers}
          </p>
          <p>
            <strong>{attendanceText(row.record, row.lesson, codeNames)}</strong>
            {row.record.comment && ` · ${row.record.comment}`}
            {row.absenceApproval && " · объяснительная принята школой"}
          </p>
          {row.content.topic && (
            <p className="journal-text">
              <strong>Тема: </strong>
              {row.content.topic}
            </p>
          )}
          {row.content.homework && (
            <p className="journal-text">
              <strong>
                Домашнее задание
                {row.content.dueDate ? ` к ${row.content.dueDate}` : ""}:{" "}
              </strong>
              {row.content.homework}
            </p>
          )}
          {row.content.assessments.map((a) => {
            const g = row.record.grades.find((g) => g.assessmentId === a.id);
            return (
              <p key={a.id} className="journal-text">
                <CategoryBadge category={categoryOf(a, categories)} />
                <strong>
                  {a.name}: {gradeLabel(g, a)}
                </strong>
                {a.kind === "points" && ` · вес ${a.weight}x`}
                {g?.comment && ` · ${g.comment}`}
              </p>
            );
          })}
          <SavedPlanTopic value={row.content.curriculum} />
          {workspace && (
            <LessonMaterials
              items={row.content.materials || []}
              workspace={workspace}
              lessonKey={row.lessonKey}
            />
          )}
          {workspace && row.content.homework && (
            <HomeworkPanel
              key={workspace + row.lessonKey + row.student.id}
              workspace={workspace}
              lessonKey={row.lessonKey}
              studentId={row.student.id}
            />
          )}
        </article>
      ))}
    </div>
  );
}

export default function JournalPanel({
  workspace,
  focus,
}: {
  workspace: string;
  // Set by an opened notification: show this pupil's report for that one day, or the report card of a period.
  focus?: { studentId: string; day?: string; finalsPeriodId?: string };
}) {
  const coverageOpened = useRef(false),
    editorPanel = useRef<HTMLElement | null>(null);
  const [options, setOptions] = useState<Options | null>(null),
    [mode, setMode] = useState("day");
  const [day, setDay] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [student, setStudent] = useState(""),
    [lessons, setLessons] = useState<DayLesson[]>([]),
    [search, setSearch] = useState("");
  const [entry, setEntry] = useState<JournalEntry | null>(null),
    [syncRoster, setSyncRoster] = useState(false),
    [dirty, setDirty] = useState(false);
  const categories = useAssessmentCategories(workspace);
  // Works whose weight is typed by hand even when it equals a preset.
  const [customWeights, setCustomWeights] = useState<Set<string>>(new Set());
  const lessonMarks = useLessonMarks(
    workspace,
    entry?.lessonKey,
    entry?.currentDate ?? undefined,
  );
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    if (entry && coverageOpened.current) {
      coverageOpened.current = false;
      editorPanel.current?.focus({ preventScroll: true });
      editorPanel.current?.scrollIntoView({ block: "start" });
    }
  }, [entry]);
  const [report, setReport] = useState<JournalReport | null>(null),
    [history, setHistory] = useState<History[]>([]),
    [historyMore, setHistoryMore] = useState(false),
    [historical, setHistorical] = useState<History | null>(null);
  const [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false),
    [epoch, setEpoch] = useState(0);
  const [dueNote, setDueNote] = useState(""),
    [dueBusy, setDueBusy] = useState(false);
  const sequence = useRef(0),
    saveLock = useRef(false),
    // Changes whenever the report selection does, so a late page or export is dropped.
    reportScope = useRef(0);
  const [paging, setPaging] = useState(false);
  useEffect(() => {
    let live = true;
    setError("");
    api("journal/options", workspace)
      .then((r: Options) => {
        if (!live) return;
        setOptions(r);
        setMode(r.staff ? "day" : "report");
        setStudent(r.students[0]?.id || "");
        const today = new Date().toLocaleDateString("sv-SE");
        setDay(
          today < r.yearStart
            ? r.yearStart
            : today > r.yearEnd
              ? r.yearEnd
              : today,
        );
        const period = defaultReportPeriod(today, r.yearStart, r.yearEnd);
        setStart(period.start);
        setEnd(period.end);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  useEffect(() => {
    if (
      !focus?.day ||
      !options?.students.some((s) => s.id === focus.studentId)
    )
      return;
    setMode("report");
    setStudent(focus.studentId);
    setStart(focus.day);
    setEnd(focus.day);
  }, [options, focus]);
  useEffect(() => {
    if (!options || !day || mode !== "day") return;
    let live = true;
    setLoading(true);
    setLessons([]);
    setError("");
    api(`journal/day?day=${day}`, workspace)
      .then((r) => {
        if (live) setLessons(r.lessons);
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [workspace, options, day, mode, epoch]);
  useEffect(() => {
    if (!options || !student || !start || !end || mode !== "report") return;
    let live = true;
    ++reportScope.current;
    setLoading(true);
    setPaging(false);
    setReport(null);
    setError("");
    api(`journal/report?${reportParams()}`, workspace)
      .then((r: JournalReportPage) => {
        if (live) setReport(mergeReportPage(null, r));
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [workspace, options, student, start, end, mode, epoch]);
  function reportParams(after?: string, limit?: number) {
    const params = new URLSearchParams({ studentId: student, start, end });
    if (after) params.set("after", after);
    if (limit) params.set("limit", String(limit));
    return params;
  }
  async function reportPage(after: string, limit?: number) {
    return (await api(
      `journal/report?${reportParams(after, limit)}`,
      workspace,
    )) as JournalReportPage;
  }
  // One page more of the same selection; a page that arrives after the selection changed is dropped.
  async function moreReport() {
    if (!report?.nextAfter || paging) return;
    const scope = reportScope.current;
    setPaging(true);
    setError("");
    try {
      const page = await reportPage(report.nextAfter);
      if (reportScope.current === scope)
        setReport((current) => mergeReportPage(current, page));
    } catch (e: any) {
      if (reportScope.current === scope) setError(e.message);
    } finally {
      if (reportScope.current === scope) setPaging(false);
    }
  }
  // CSV and print cover the whole period, not just the pages on screen.
  async function wholeReport(use: (all: JournalReport) => void) {
    if (!report || paging) return;
    const scope = reportScope.current;
    setPaging(true);
    setError("");
    try {
      const all = await loadRemainingReport(report, (after) =>
        reportPage(after, 200),
      );
      if (reportScope.current !== scope) return;
      flushSync(() => setReport(all));
      use(all);
    } catch (e: any) {
      if (reportScope.current === scope) setError(e.message);
    } finally {
      if (reportScope.current === scope) setPaging(false);
    }
  }
  useEffect(() => {
    const leave = (e: BeforeUnloadEvent) => {
      if (dirty || busy) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", leave);
    const navigate = (e: Event) => {
      if (!canLeave()) e.preventDefault();
    };
    window.addEventListener("atlas-homework-leave", navigate);
    return () => {
      window.removeEventListener("beforeunload", leave);
      window.removeEventListener("atlas-homework-leave", navigate);
    };
  }, [dirty, busy]);
  function canLeave() {
    if (busy || saveLock.current) return false;
    return (
      !dirty ||
      window.confirm(
        "В журнале есть несохранённые поля. Перейти без сохранения?",
      )
    );
  }
  function clearEditor() {
    ++sequence.current;
    setDueBusy(false);
    setEntry(null);
    setDirty(false);
    setSyncRoster(false);
    setHistory([]);
    setHistoryMore(false);
    setHistorical(null);
    setMessage("");
  }
  async function openEntry(key: string, sync = false, entryDay = day) {
    const current = ++sequence.current;
    setDueBusy(false);
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const r = await api(
        `journal/entry?${new URLSearchParams({ lessonKey: key, day: entryDay, syncRoster: String(sync) })}`,
        workspace,
      );
      if (sequence.current !== current) return;
      setEntry(r);
      setSyncRoster(sync);
      setDirty(sync);
      setHistory([]);
      setHistoryMore(false);
      setHistorical(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      if (sequence.current === current) setBusy(false);
    }
  }
  function content(update: (c: JournalContent) => JournalContent) {
    setEntry(
      (e) =>
        e && {
          ...e,
          document: { ...e.document, content: update(e.document.content) },
        },
    );
    setDirty(true);
    setMessage("");
  }
  function patchWork(id: string, change: Partial<Assessment>) {
    content((c) => ({
      ...c,
      assessments: c.assessments.map((x) =>
        x.id === id ? { ...x, ...change } : x,
      ),
    }));
  }
  function pupil(id: string, update: (p: PupilRecord) => PupilRecord) {
    content((c) => ({
      ...c,
      records: c.records.map((p) => (p.studentId === id ? update(p) : p)),
    }));
  }
  function grade(id: string, a: Assessment, update: Partial<Grade>) {
    pupil(id, (p) => {
      const existing = p.grades.find((g) => g.assessmentId === a.id) || {
        assessmentId: a.id,
        score: null,
        label: "",
        comment: "",
      };
      return {
        ...p,
        grades: [
          ...p.grades.filter((g) => g.assessmentId !== a.id),
          { ...existing, ...update },
        ],
      };
    });
  }
  // override: the content to send instead of the editor's (the one-click «Отсутствующих нет»); the editor changes only if it is saved.
  async function save(publish: boolean, override?: JournalContent) {
    const sent = override ?? entry?.document.content;
    const problem =
      entry &&
      sent &&
      (contentProblem(sent) ||
        attendanceProblem({ ...entry.document, content: sent }));
    if (problem) {
      setError(problem);
      setMessage("");
      return;
    }
    if (!entry || !sent || saveLock.current) return;
    saveLock.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const r = await api("journal/entry", workspace, "PUT", {
        lessonKey: entry.lessonKey,
        day: entry.document.lesson.date,
        revision: entry.revision,
        version: entry.version,
        content: sent,
        publish,
        syncRoster,
      });
      setEntry(r);
      setDirty(false);
      setSyncRoster(false);
      setEpoch((n) => n + 1);
      setHistory([]);
      setHistoryMore(false);
      setHistorical(null);
      setMessage(
        publish
          ? "Запись опубликована. Ученики видят каждый свои оценки и отметки." +
              (sent.topic.trim()
                ? ""
                : " Тема урока не заполнена: контроль покажет журнал незаполненным.")
          : "Черновик сохранён. Видимая ученикам редакция не изменена.",
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
      saveLock.current = false;
    }
  }
  async function historyPage(before?: number) {
    if (!entry) return;
    setBusy(true);
    setError("");
    try {
      const r = await api(
        `journal/history?${new URLSearchParams({ lessonKey: entry.lessonKey, ...(before ? { beforeVersion: String(before) } : {}) })}`,
        workspace,
      );
      setHistory((rows) => (before ? [...rows, ...r] : r));
      setHistoryMore(hasEarlierRevisions(r));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const editable = !!entry?.writable && !entry.needsSync && !busy;
  // A held lesson where nobody is missing is one click: «Отсутствующих нет» marks the unmarked present and saves for the family.
  const nobodyAbsent =
    !!entry &&
    entry.writable &&
    !entry.needsSync &&
    !!entry.lessonBegun &&
    entry.document.lesson.students.some((s) => s.enrolled) &&
    noAbsentees(
      entry.document.content,
      entry.document.lesson.students,
      entry.attendanceApprovals,
    );
  useEffect(() => setDueNote(""), [entry?.lessonKey]);
  // Only the date is taken from the timetable and saved; a later timetable change does not move it.
  async function dueNextLesson() {
    if (!entry || dueBusy) return;
    // The reply may come after another lesson or day was opened: it is then dropped, like a late entry.
    const made = sequence.current,
      current = () => sequence.current === made;
    setDueBusy(true);
    setError("");
    setDueNote("");
    await whileCurrent<NextLesson>(
      () =>
        api(
          `journal/homework-next-lesson?${new URLSearchParams({ lessonKey: entry.lessonKey, day: entry.document.lesson.date })}`,
          workspace,
        ),
      current,
      (r) => {
        if (r.date) {
          content((c) => ({ ...c, dueDate: r.date }));
          setDueNote(nextLessonText(r));
        } else setError(nextLessonText(r));
      },
      (e) => setError(e.message),
    );
    if (current()) setDueBusy(false);
  }
  function adminPanel(name: JournalPanelName) {
    switch (name) {
      case "courseTable":
        return (
          <CourseTablePanel
            key={`course-table-${workspace}`}
            workspace={workspace}
            onOpen={(key, date) => {
              if (!canLeave()) return;
              coverageOpened.current = true;
              clearEditor();
              setMode("day");
              setDay(date);
              void openEntry(key, false, date);
            }}
          />
        );
      case "finalGrades":
        return (
          <FinalGradesPanel
            key={`final-grades-${workspace}`}
            workspace={workspace}
            focus={focus?.finalsPeriodId ? focus : undefined}
          />
        );
      case "absences":
        return <AbsencePanel key={workspace} workspace={workspace} />;
      case "periods":
        return (
          <JournalPeriodsPanel
            key={`periods-${workspace}`}
            workspace={workspace}
            onChanged={() => {
              setEpoch((x) => x + 1);
              if (entry) {
                const key = entry.lessonKey;
                api(
                  `journal/entry?${new URLSearchParams({ lessonKey: key, day })}`,
                  workspace,
                )
                  .then((fresh: JournalEntry) => {
                    setEntry((old) =>
                      old?.lessonKey === key
                        ? {
                            ...old,
                            writable: fresh.writable,
                            canWithdraw: fresh.canWithdraw,
                            periodLocks: fresh.periodLocks,
                          }
                        : old,
                    );
                  })
                  .catch((e) => setError(e.message));
              }
            }}
          />
        );
      case "coverage":
        return (
          <JournalCoveragePanel
            key={`coverage-${workspace}`}
            workspace={workspace}
            onOpen={(key, date) => {
              if (!canLeave()) return;
              coverageOpened.current = true;
              clearEditor();
              setMode("day");
              setDay(date);
              void openEntry(key, false, date);
            }}
          />
        );
      case "attendance":
        return (
          <AttendancePanel
            key={`attendance-${workspace}`}
            workspace={workspace}
          />
        );
      case "attendanceGrid":
        return (
          <AttendanceGridPanel
            key={`attendance-grid-${workspace}`}
            workspace={workspace}
          />
        );
    }
  }
  const panels = options ? journalPanels(options) : [];
  return (
    <section className="journal-panel">
      <p>
        Посещаемость, оценки, темы и домашние задания. Записи журнала
        сохраняются отдельно от расписания; переносы и правки групп не удаляют
        прежние отметки.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="info-line">
          {message}
        </p>
      )}
      {!options && !error && <p role="status">Загружаю журнал…</p>}
      {options && (
        <>
          <div className="dated-toolbar journal-controls">
            {options.staff && (
              <>
                <button
                  aria-pressed={mode === "day"}
                  disabled={busy}
                  onClick={() => {
                    if (canLeave()) {
                      clearEditor();
                      setMode("day");
                    }
                  }}
                >
                  Журнал уроков
                </button>
                <button
                  aria-pressed={mode === "report"}
                  disabled={busy}
                  onClick={() => {
                    if (canLeave()) {
                      clearEditor();
                      setMode("report");
                    }
                  }}
                >
                  Отчёт ученика
                </button>
              </>
            )}
            <button
              disabled={busy || loading}
              onClick={() => {
                if (canLeave()) {
                  clearEditor();
                  setEpoch((n) => n + 1);
                }
              }}
            >
              Обновить журнал
            </button>
          </div>
          {mode === "day" ? (
            <>
              <div className="dated-fields journal-controls">
                <label className="field">
                  <span>Дата журнала</span>
                  <input
                    type="date"
                    value={day}
                    disabled={busy}
                    onChange={(e) => {
                      if (canLeave()) {
                        clearEditor();
                        setDay(e.target.value);
                      }
                    }}
                  />
                </label>
                <label className="field">
                  <span>Найти урок</span>
                  <input
                    type="search"
                    placeholder="Предмет или группа"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
              </div>
              {loading && <p role="status">Загружаю уроки…</p>}
              {!loading && !lessons.length && (
                <p className="empty-state">Доступных уроков на эту дату нет.</p>
              )}
              <div className="journal-lessons">
                {lessons
                  .filter((l) =>
                    `${l.lesson.subject} ${l.lesson.group} ${l.lesson.teachers}`
                      .toLocaleLowerCase()
                      .includes(search.toLocaleLowerCase()),
                  )
                  .map((l) => (
                    <button
                      key={l.lessonKey}
                      disabled={busy}
                      aria-pressed={l.lessonKey === entry?.lessonKey}
                      onClick={() => {
                        if (canLeave()) void openEntry(l.lessonKey);
                      }}
                    >
                      <strong>
                        {l.lesson.start}–{l.lesson.end} · {l.lesson.subject}
                      </strong>
                      <span>
                        {l.lesson.group} · {l.studentCount} уч.
                      </span>
                      <small>
                        {l.archived
                          ? "Архивная запись"
                          : l.lesson.status === "cancelled"
                            ? "Отменено"
                            : l.hasPublished
                              ? `Ученикам: редакция ${l.publishedVersion}${l.publishedVersion !== l.version ? " · есть черновик" : ""}`
                              : l.version
                                ? "Черновик"
                                : "Не заполнено"}
                      </small>
                    </button>
                  ))}
              </div>
              {entry && (
                <section
                  className="journal-editor"
                  ref={editorPanel}
                  tabIndex={-1}
                  aria-label="Редактор журнала урока"
                >
                  <header>
                    <div>
                      <h2>
                        {entry.document.lesson.subject} ·{" "}
                        {entry.document.lesson.group}
                      </h2>
                      <p>
                        {entry.document.lesson.date} ·{" "}
                        {entry.document.lesson.start}–
                        {entry.document.lesson.end} ·{" "}
                        {entry.document.lesson.teachers}
                      </p>
                    </div>
                    <span>
                      {dirty
                        ? "Есть несохранённые поля"
                        : entry.version
                          ? `Редакция ${entry.version}`
                          : "Новая запись"}
                    </span>
                  </header>
                  <p className="info-line">
                    {entry.attendanceDefaulted
                      ? "Все отмечены присутствующими по умолчанию. Семья увидит отметки после сохранения."
                      : entry.hasPublished &&
                          entry.lessonBegun &&
                          entry.publishedVersion === entry.version
                        ? "Семья видит эту редакцию; каждое сохранение сразу видно семье."
                        : entry.hasPublished
                          ? `Ученики видят редакцию ${entry.publishedVersion}. Изменения черновика появятся после публикации.`
                          : "Запись ещё не видна ученикам."}
                    {entry.updated &&
                      ` Последнее изменение: ${entry.actor}, ${new Date(entry.updated).toLocaleString("ru-RU")}.`}
                  </p>
                  {!entry.document.lesson.rosterComplete && (
                    <p className="error-banner">
                      Состав группы не подтверждён полностью. Здесь показаны
                      только внесённые ученики.
                    </p>
                  )}
                  {!entry.writable && (
                    <p className="info-line">
                      {entry.periodLocks?.length
                        ? `Период закрыт: ${entry.periodLocks.map((p) => p.name).join(", ")}. Для исправления администратор должен открыть все закрытые периоды с указанием причины.`
                        : "Запись доступна только для просмотра: урок отменён, удалён или назначен другому преподавателю."}
                    </p>
                  )}
                  {entry.futureLesson && (
                    <p className="info-line">
                      Урок ещё не прошёл. Тему, домашнее задание и материалы
                      можно опубликовать заранее; посещаемость и оценки
                      публикуются в день урока.
                    </p>
                  )}
                  {entry.rosterFrozen && entry.hasPublished && (
                    <p className="info-line">
                      Состав, время и учителя урока зафиксированы при
                      публикации: последующие изменения расписания и составов
                      их не меняют.
                    </p>
                  )}
                  {entry.needsSync && entry.writable && (
                    <div className="info-line">
                      <p>
                        Расписание или состав группы изменились. Просмотрите
                        обновление перед редактированием. Прежние ученики и их
                        оценки сохранятся; новые ученики появятся без отметок.
                        {entry.currentDate !== entry.document.lesson.date &&
                          ` Текущая дата урока: ${entry.currentDate}.`}
                      </p>
                      <button
                        disabled={busy}
                        onClick={() => void openEntry(entry.lessonKey, true)}
                      >
                        Обновить состав и время
                      </button>
                    </div>
                  )}
                  {syncRoster && (
                    <p className="info-line">
                      Обновлённый состав показан для проверки. Он сохранится
                      вместе с записью.
                    </p>
                  )}
                  <fieldset
                    disabled={!editable}
                    className="journal-content-fields"
                  >
                    <label className="field">
                      <span>Тема урока</span>
                      <textarea
                        maxLength={2000}
                        value={entry.document.content.topic}
                        onChange={(e) =>
                          content((c) => ({ ...c, topic: e.target.value }))
                        }
                      />
                    </label>
                    <LessonTopic
                      workspace={workspace}
                      lessonKey={entry.lessonKey}
                      day={entry.document.lesson.date}
                      value={entry.document.content.curriculum}
                      materials={entry.document.content.materials || []}
                      disabled={!editable}
                      onChange={(curriculum, materials) =>
                        content((c) => ({
                          ...c,
                          curriculum,
                          materials: materials ?? c.materials,
                          topic:
                            curriculum &&
                            (curriculum.planId !== c.curriculum?.planId ||
                              curriculum.version !== c.curriculum?.version ||
                              curriculum.topicId !== c.curriculum?.topicId)
                              ? curriculum.title
                              : c.topic,
                        }))
                      }
                    />
                    <label className="field">
                      <span>Домашнее задание</span>
                      <textarea
                        maxLength={4000}
                        value={entry.document.content.homework}
                        onChange={(e) =>
                          content((c) => ({
                            ...c,
                            homework: e.target.value,
                            dueDate: e.target.value ? c.dueDate : null,
                            homeworkFiles: e.target.value
                              ? c.homeworkFiles
                              : [],
                            homeworkLatePolicy: e.target.value
                              ? c.homeworkLatePolicy
                              : "allow",
                            homeworkSubmission: e.target.value
                              ? c.homeworkSubmission
                              : "online",
                          }))
                        }
                      />
                    </label>
                    <label className="field">
                      <span>Срок домашнего задания</span>
                      <input
                        type="date"
                        disabled={!entry.document.content.homework}
                        value={entry.document.content.dueDate || ""}
                        onChange={(e) =>
                          content((c) => ({
                            ...c,
                            dueDate: e.target.value || null,
                            homeworkLatePolicy: e.target.value
                              ? c.homeworkLatePolicy
                              : "allow",
                          }))
                        }
                      />
                    </label>
                    <div className="journal-due-next">
                      <button
                        type="button"
                        disabled={!entry.document.content.homework || dueBusy}
                        onClick={() => void dueNextLesson()}
                      >
                        К следующему уроку
                      </button>
                      {dueNote && <small role="status">{dueNote}</small>}
                    </div>
                    <label className="field">
                      <span>Сдача работы</span>
                      <select
                        disabled={!entry.document.content.homework}
                        value={
                          entry.document.content.homeworkSubmission || "online"
                        }
                        onChange={(e) =>
                          content((c) => ({
                            ...c,
                            homeworkSubmission: e.target.value as
                              "online" | "offline",
                            homeworkLatePolicy:
                              e.target.value === "offline"
                                ? "allow"
                                : c.homeworkLatePolicy,
                          }))
                        }
                      >
                        <option value="online">
                          Ученики отправляют ответ в Атласе
                        </option>
                        <option value="offline">
                          Без онлайн-сдачи (показывают на уроке)
                        </option>
                      </select>
                    </label>
                    <label className="field">
                      <span>Приём после срока</span>
                      <select
                        disabled={
                          !entry.document.content.dueDate ||
                          entry.document.content.homeworkSubmission ===
                            "offline"
                        }
                        value={
                          entry.document.content.homeworkLatePolicy || "allow"
                        }
                        onChange={(e) =>
                          content((c) => ({
                            ...c,
                            homeworkLatePolicy: e.target.value as
                              "allow" | "block",
                          }))
                        }
                      >
                        <option value="allow">
                          Разрешать с отметкой об опоздании
                        </option>
                        <option value="block">
                          Закрывать приём после срока
                        </option>
                      </select>
                      <small>
                        Срок заканчивается в 23:59 по времени школы (Кызылорда).
                        При закрытом приёме для повторной сдачи после возврата
                        тоже потребуется продление срока.
                      </small>
                    </label>
                    <LessonMaterials
                      key={workspace + entry.lessonKey + entry.version}
                      items={entry.document.content.materials || []}
                      workspace={workspace}
                      lessonKey={entry.lessonKey}
                      disabled={!editable}
                      onChange={(materials) =>
                        content((c) => ({ ...c, materials }))
                      }
                    />
                    <AssignmentFilesPanel
                      key={workspace + entry.lessonKey}
                      entry={entry}
                      workspace={workspace}
                      disabled={!editable}
                      change={(ids) =>
                        content((c) => ({ ...c, homeworkFiles: ids }))
                      }
                      onUploading={(value) => {
                        saveLock.current = value;
                        setBusy(value);
                      }}
                    />
                    <div className="journal-assessments">
                      <h3>Проверочные работы</h3>
                      <p>
                        Баллы — от 0 до выбранного максимума. Текстовые оценки
                        (A*, зачёт и другие) не входят в средний процент. Вес 0
                        исключает работу из расчёта; итог периода считается по
                        долям категорий школы.
                      </p>
                      {!!entry.bookings?.length && (
                        <div className="info-line">
                          <strong>По календарю контрольных:</strong>
                          {entry.bookings.map((b) => (
                            <p key={b.id}>
                              {b.short} «{b.topic}»{" "}
                              <button
                                type="button"
                                disabled={
                                  !editable ||
                                  entry.document.content.assessments.length >=
                                    10 ||
                                  entry.document.content.assessments.some(
                                    (x) => x.bookingId === b.id,
                                  )
                                }
                                onClick={() =>
                                  content((c) => ({
                                    ...c,
                                    assessments: [
                                      ...c.assessments,
                                      bookingDefault(b),
                                    ],
                                  }))
                                }
                              >
                                Добавить работу из календаря
                              </button>
                            </p>
                          ))}
                        </div>
                      )}
                      {entry.document.content.assessments.map((a) => (
                        <div key={a.id} className="journal-assessment">
                          <label className="field">
                            <span>Название работы</span>
                            <input
                              maxLength={100}
                              value={a.name}
                              onChange={(e) =>
                                content((c) => ({
                                  ...c,
                                  assessments: c.assessments.map((x) =>
                                    x.id === a.id
                                      ? { ...x, name: e.target.value }
                                      : x,
                                  ),
                                }))
                              }
                            />
                          </label>
                          <label className="field">
                            <span>Формат</span>
                            <select
                              value={a.kind}
                              disabled={entry.document.content.records.some(
                                (p) =>
                                  p.grades.some(
                                    (g) =>
                                      g.assessmentId === a.id &&
                                      (g.score !== null || g.label),
                                  ),
                              )}
                              onChange={(e) =>
                                content((c) => ({
                                  ...c,
                                  assessments: c.assessments.map((x) =>
                                    x.id === a.id
                                      ? withKind(
                                          x,
                                          e.target.value as Assessment["kind"],
                                        )
                                      : x,
                                  ),
                                }))
                              }
                            >
                              <option value="points">Баллы</option>
                              <option value="label">Текстовая оценка</option>
                            </select>
                          </label>
                          <label className="field">
                            <span>Категория</span>
                            <select
                              value={categoryOf(a, categories).id}
                              onChange={(e) =>
                                patchWork(a.id, { category: e.target.value })
                              }
                            >
                              {(categories?.categories ?? [])
                                .filter(
                                  (k) =>
                                    k.active ||
                                    k.id === categoryOf(a, categories).id,
                                )
                                .map((k) => (
                                  <option key={k.id} value={k.id}>
                                    {k.name}
                                    {k.active ? "" : " (отключена)"}
                                  </option>
                                ))}
                              {!categories?.categories.some(
                                (k) => k.id === categoryOf(a, categories).id,
                              ) && (
                                <option value={categoryOf(a, categories).id}>
                                  {categoryOf(a, categories).name}
                                </option>
                              )}
                            </select>
                          </label>
                          {a.bookingId &&
                            entry.bookings?.some(
                              (b) => b.id === a.bookingId,
                            ) && (
                              <p className="muted category-note">
                                Из календаря:{" "}
                                {
                                  entry.bookings.find(
                                    (b) => b.id === a.bookingId,
                                  )?.short
                                }
                              </p>
                            )}
                          {a.kind === "points" && (
                            <>
                              <label className="field">
                                <span>Максимум баллов</span>
                                <input
                                  type="number"
                                  min="0.01"
                                  max="10000"
                                  step="0.01"
                                  value={numberValue(a.maxScore)}
                                  onChange={(e) =>
                                    content((c) => ({
                                      ...c,
                                      assessments: c.assessments.map((x) =>
                                        x.id === a.id
                                          ? {
                                              ...x,
                                              maxScore: numberField(
                                                e.target.value,
                                              ),
                                            }
                                          : x,
                                      ),
                                    }))
                                  }
                                />
                              </label>
                              <label className="field">
                                <span>Вес работы</span>
                                <select
                                  value={
                                    customWeights.has(a.id)
                                      ? "custom"
                                      : String(presetOf(a.weight))
                                  }
                                  onChange={(e) => {
                                    const custom = e.target.value === "custom";
                                    setCustomWeights((s) => {
                                      const next = new Set(s);
                                      if (custom) next.add(a.id);
                                      else next.delete(a.id);
                                      return next;
                                    });
                                    if (!custom)
                                      patchWork(a.id, {
                                        weight: Number(e.target.value),
                                      });
                                  }}
                                >
                                  {WEIGHT_PRESETS.map((w) => (
                                    <option key={w.value} value={w.value}>
                                      {w.label}
                                    </option>
                                  ))}
                                  <option value="custom">Свой</option>
                                </select>
                              </label>
                              {(customWeights.has(a.id) ||
                                presetOf(a.weight) === "custom") && (
                                <label className="field">
                                  <span>Свой вес (от 0 до 100)</span>
                                  <input
                                    type="number"
                                    min="0"
                                    max="100"
                                    step="0.01"
                                    value={numberValue(a.weight)}
                                    onChange={(e) =>
                                      patchWork(a.id, {
                                        weight: numberField(e.target.value),
                                      })
                                    }
                                  />
                                </label>
                              )}
                            </>
                          )}
                          <button
                            type="button"
                            onClick={() => {
                              if (
                                entry.document.content.records.some((p) =>
                                  p.grades.some(
                                    (g) =>
                                      g.assessmentId === a.id &&
                                      (g.score !== null ||
                                        g.label ||
                                        g.comment),
                                  ),
                                ) &&
                                !window.confirm(
                                  "Удалить работу и её оценки из текущей редакции? Сохранённые редакции останутся в истории.",
                                )
                              )
                                return;
                              content((c) => ({
                                ...c,
                                assessments: c.assessments.filter(
                                  (x) => x.id !== a.id,
                                ),
                                records: c.records.map((p) => ({
                                  ...p,
                                  grades: p.grades.filter(
                                    (g) => g.assessmentId !== a.id,
                                  ),
                                })),
                              }));
                            }}
                          >
                            Удалить работу
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        disabled={
                          entry.document.content.assessments.length >= 10
                        }
                        onClick={() =>
                          content((c) => ({
                            ...c,
                            assessments: [
                              ...c.assessments,
                              {
                                id: crypto.randomUUID(),
                                name: `Работа ${c.assessments.length + 1}`,
                                kind: "points",
                                maxScore: 10,
                                weight: 1,
                                category: categories?.defaultCategory || "fo",
                              },
                            ],
                          }))
                        }
                      >
                        Добавить работу
                      </button>
                    </div>
                    <h3>Ученики и посещаемость</h3>
                    <p>
                      Комментарии к ученику и оценкам будут видны этому ученику
                      и его родителю после публикации.
                    </p>
                    {nobodyAbsent ? (
                      <div className="lesson-quick">
                        <button
                          type="button"
                          className="primary"
                          disabled={!editable}
                          onClick={() =>
                            void save(
                              true,
                              withNoAbsentees(
                                entry.document.content,
                                entry.document.lesson.students,
                                entry.attendanceApprovals,
                              ),
                            )
                          }
                        >
                          Отсутствующих нет
                        </button>
                        <small>
                          Отметит присутствующими всех, кто не отмечен, и сразу
                          сохранит урок для семьи.
                        </small>
                      </div>
                    ) : (
                      <button
                        type="button"
                        disabled={!!entry.futureLesson}
                        onClick={() =>
                          content((c) =>
                            withNoAbsentees(
                              c,
                              entry.document.lesson.students,
                              entry.attendanceApprovals,
                            ),
                          )
                        }
                      >
                        Неотмеченные присутствуют
                      </button>
                    )}
                    <LessonPupilCards
                      entry={entry}
                      categories={categories}
                      lessonMarks={lessonMarks}
                      onPupil={pupil}
                      onGrade={grade}
                    />
                  </fieldset>
                  <div className="dated-toolbar journal-savebar">
                    {entry.lessonBegun ? (
                      <>
                        <button
                          className="primary"
                          disabled={!editable}
                          onClick={() => void save(true)}
                        >
                          Сохранить
                        </button>
                        {!entry.hasPublished && (
                          <button
                            disabled={!editable}
                            onClick={() => void save(false)}
                          >
                            Сохранить без показа семье
                          </button>
                        )}
                      </>
                    ) : (
                      <>
                        <button
                          className="primary"
                          disabled={!editable}
                          onClick={() => void save(false)}
                        >
                          Сохранить черновик
                        </button>
                        <button
                          disabled={!editable}
                          onClick={() => void save(true)}
                        >
                          Показать семье заранее
                        </button>
                      </>
                    )}
                    {entry.canWithdraw && (
                      <button
                        disabled={busy || dirty}
                        onClick={async () => {
                          if (
                            !window.confirm(
                              "Скрыть эту запись от учеников и родителей? История и черновик сохранятся.",
                            )
                          )
                            return;
                          setBusy(true);
                          setError("");
                          try {
                            await api("journal/withdraw", workspace, "POST", {
                              lessonKey: entry.lessonKey,
                              version: entry.version,
                            });
                            await openEntry(entry.lessonKey);
                            setEpoch((n) => n + 1);
                            setMessage(
                              "Запись скрыта от учеников и родителей.",
                            );
                          } catch (e: any) {
                            setError(e.message);
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        Скрыть от учеников
                      </button>
                    )}
                    <button
                      onClick={() =>
                        journalCsv(
                          documentRows(
                            entry.document,
                            entry.lessonKey,
                            entry.version,
                            entry.attendanceApprovals,
                          ),
                          undefined,
                          categories,
                          absenceCodeNames(entry.absenceCodes),
                        )
                      }
                    >
                      CSV текущих полей
                    </button>
                    <button
                      disabled={!entry.version || busy}
                      onClick={() => void historyPage()}
                    >
                      История записи
                    </button>
                  </div>
                  {!!history.length && (
                    <section className="journal-history">
                      <h3>Сохранённые редакции</h3>
                      {history.map((h) => (
                        <div key={h.version}>
                          <span>
                            № {h.version} ·{" "}
                            {new Date(h.updated).toLocaleString("ru-RU")} ·{" "}
                            {h.actor} ·{" "}
                            {h.action === "journal.published"
                              ? "Опубликовано"
                              : h.action === "journal.withdrawn"
                                ? "Скрыто"
                                : "Черновик"}
                          </span>
                          <button
                            disabled={busy}
                            onClick={async () => {
                              setBusy(true);
                              try {
                                const r = await api(
                                  `journal/history?${new URLSearchParams({ lessonKey: entry.lessonKey, version: String(h.version) })}`,
                                  workspace,
                                );
                                setHistorical(r[0] || null);
                              } catch (e: any) {
                                setError(e.message);
                              } finally {
                                setBusy(false);
                              }
                            }}
                          >
                            Посмотреть редакцию {h.version}
                          </button>
                        </div>
                      ))}
                      {historyMore && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            void historyPage(history.at(-1)!.version)
                          }
                        >
                          Более ранние редакции
                        </button>
                      )}
                    </section>
                  )}
                  {entry.hasPublished &&
                    entry.publishedVersion === entry.version &&
                    entry.document.content.homework.trim() && (
                      <HomeworkParallelPanel
                        key={workspace + entry.lessonKey + entry.version}
                        workspace={workspace}
                        entry={entry}
                        disabled={dirty || busy}
                        onPublished={() => setEpoch((n) => n + 1)}
                      />
                    )}
                  {!!entry.version && (
                    <HomeworkPanel
                      key={workspace + entry.lessonKey + entry.publishedVersion}
                      workspace={workspace}
                      lessonKey={entry.lessonKey}
                      onGraded={() => {
                        setEpoch((n) => n + 1);
                        if (dirtyRef.current) {
                          setMessage(
                            "Оценка опубликована. Ваши несохранённые поля оставлены на экране; перед сохранением откройте актуальную редакцию журнала.",
                          );
                        } else {
                          void openEntry(entry.lessonKey).then(() =>
                            setMessage("Оценка опубликована, журнал обновлён."),
                          );
                        }
                      }}
                    />
                  )}
                  {historical?.document && (
                    <section className="journal-history-preview">
                      <h3>Редакция {historical.version} · только просмотр</h3>
                      <p>
                        Поздняя сдача:{" "}
                        {historical.document.content.homeworkLatePolicy ===
                        "block"
                          ? "приём закрывается после срока"
                          : "разрешена с отметкой"}
                        .
                      </p>
                      {historical.document.content.homeworkSubmission ===
                        "offline" && <p>Сдача: без онлайн-сдачи.</p>}
                      <SavedPlanTopic
                        value={historical.document.content.curriculum}
                      />
                      <LessonMaterials
                        items={historical.document.content.materials || []}
                        workspace={workspace}
                        lessonKey={entry.lessonKey}
                      />
                      <SavedMaterialLinks
                        ids={historical.document.content.homeworkFiles || []}
                        workspace={workspace}
                        lessonKey={entry.lessonKey}
                        day={historical.document.lesson.date}
                      />
                      <button onClick={() => setHistorical(null)}>
                        Закрыть редакцию
                      </button>
                      <PublishedRows
                        rows={documentRows(
                          historical.document,
                          entry.lessonKey,
                          historical.version,
                        )}
                        categories={categories}
                        codeNames={absenceCodeNames(entry.absenceCodes)}
                      />
                    </section>
                  )}
                </section>
              )}
            </>
          ) : (
            <>
              <div className="dated-fields journal-controls">
                <label className="field">
                  <span>Ученик журнала</span>
                  <select
                    value={student}
                    onChange={(e) => setStudent(e.target.value)}
                  >
                    {options.students.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Начало периода</span>
                  <input
                    type="date"
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Конец периода</span>
                  <input
                    type="date"
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </label>
                <button
                  disabled={paging}
                  onClick={() => {
                    const period = defaultReportPeriod(
                      new Date().toLocaleDateString("sv-SE"),
                      options.yearStart,
                      options.yearEnd,
                    );
                    setStart(period.start);
                    setEnd(period.end);
                  }}
                >
                  Последние 30 дней
                </button>
                <button
                  disabled={paging}
                  onClick={() => {
                    setStart(options.yearStart);
                    setEnd(options.yearEnd);
                  }}
                >
                  Весь учебный год
                </button>
              </div>
              {student && (
                <GradeFeed
                  workspace={workspace}
                  studentId={student}
                  staff={Boolean(options.staff)}
                />
              )}
              <p className="info-line">
                Только опубликованные записи. Учитель видит записи своих уроков.
                Средний процент — сумма процентов по работам с их весами,
                делённая на сумму весов. Это не итоговая оценка и не
                автоматический перевод в школьную шкалу.
              </p>
              {loading && <p role="status">Загружаю опубликованные записи…</p>}
              {!options.students.length && (
                <p className="empty-state">Ученики для журнала не назначены.</p>
              )}
              {report && (
                <>
                  <h2>
                    {options.students.find((s) => s.id === student)?.name ||
                      "Ученик"}
                  </h2>
                  <p>
                    Период: {start} — {end}
                  </p>
                  <div className="dated-toolbar journal-controls">
                    <button
                      disabled={!report.rows.length || paging}
                      onClick={() =>
                        void wholeReport((all) =>
                          journalCsv(
                            all.rows,
                            "Личный журнал.csv",
                            categories,
                            all.absenceCodes,
                          ),
                        )
                      }
                    >
                      Скачать журнал CSV
                    </button>
                    <button
                      disabled={paging}
                      onClick={() => void wholeReport(() => window.print())}
                    >
                      Печать журнала
                    </button>
                  </div>
                  <p role="status" className="info-line">
                    Показано записей: {report.rows.length}
                    {report.nextAfter && " · ещё есть"}
                    {paging && " · загружаю…"}
                  </p>
                  <h3>Итоги за выбранный период</h3>
                  <div className="journal-summary">
                    {Object.entries(report.summary.attendance).map(
                      ([key, value]) => (
                        <span key={key}>
                          {attendanceNames[key]}: <strong>{value}</strong>
                        </span>
                      ),
                    )}
                    <span>
                      Опоздания:{" "}
                      <strong>
                        {report.summary.lateMinutes} мин
                        {!!report.summary.lateShare &&
                          ` · ${lessonsText(report.summary.lateShare)}`}
                      </strong>
                    </span>
                  </div>
                  {!!report.summary.subjects.length && (
                    <div className="journal-subjects">
                      {report.summary.subjects.map((s) => (
                        <article key={s.subjectId}>
                          <h3>{s.subject}</h3>
                          <strong>
                            {s.percentage === null
                              ? "Без среднего процента"
                              : `${s.percentage}%`}
                          </strong>
                          <p>
                            Работ с баллами: {s.pointsCount} · текстовых оценок:{" "}
                            {s.labelCount}
                          </p>
                          {s.sharePercentage != null && (
                            <p>
                              По долям категорий:{" "}
                              <strong>{s.sharePercentage}%</strong>
                              {s.rule ? ` · ${s.rule}` : ""}
                            </p>
                          )}
                          {!!s.categories?.length && (
                            <p>
                              {s.categories.map((k) => (
                                <span key={k.id}>
                                  <CategoryBadge
                                    category={categoryOf(
                                      { category: k.id },
                                      categories,
                                    )}
                                  />
                                  {k.percentage}%{" "}
                                </span>
                              ))}
                            </p>
                          )}
                        </article>
                      ))}
                    </div>
                  )}
                  {!report.rows.length && (
                    <p className="empty-state">
                      Опубликованных записей за этот период нет.
                    </p>
                  )}
                  <PublishedRows
                    rows={report.rows}
                    workspace={workspace}
                    categories={categories}
                    codeNames={report.absenceCodes}
                  />
                  {report.nextAfter && (
                    <div className="dated-toolbar journal-controls">
                      <button
                        disabled={paging}
                        onClick={() => void moreReport()}
                      >
                        Показать ещё
                      </button>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </>
      )}
      {!!panels.length && (
        <section
          className="journal-admin-panels"
          aria-label="Отчёты и управление журналом"
        >
          {panels.map(adminPanel)}
        </section>
      )}
    </section>
  );
}

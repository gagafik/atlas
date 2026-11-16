import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "./api";
import LessonEditor from "./LessonEditor";
import { updateRecord } from "./lessonEditorModel";
import { nextLessonText, whileCurrent, type NextLesson } from "./homeworkBulk";
import AbsencePanel from "./AbsencePanel";
import FamilyJournal from "./FamilyJournal";
import { useLessonMarks } from "./LessonMarks";
import AttendancePanel from "./AttendancePanel";
import AttendanceGridPanel from "./AttendanceGridPanel";
import JournalCoveragePanel from "./JournalCoveragePanel";
import TeacherGradebook from "./TeacherGradebook";
import JournalPeriodsPanel from "./JournalPeriodsPanel";
import FinalGradesPanel from "./FinalGradesPanel";
import MonthCalendar from "./MonthCalendar";
import { ClassesTable, LessonsTable } from "./JournalDayTables";
import {
  NO_CLASS,
  classSummaries,
  classToShow,
  countFill,
  lessonRows,
  lessonsOfClass,
  matchesQuery,
  naturalOrder,
  searchLessons,
} from "./journalDay";
import { dayProfile, slotsFor } from "./bells";
import { dayText, shiftDate, weekdayName } from "./pupilJournal";
import { useCoverageDays, type CoverageScope } from "./useCoverageDays";
import { useLaunch, type LaunchIntent } from "./launch";
import { useAssessmentCategories } from "./assessmentCategories";
import {
  attendanceProblem,
  lessonOfNow,
  contentProblem,
  hasEarlierRevisions,
  journalPanels,
  noAbsentees,
  type Assessment,
  type DayLesson,
  type Grade,
  type JournalContent,
  type JournalEntry,
  type JournalHistory,
  type JournalOptions,
  type JournalPanelName,
  type JournalView,
  type PupilRecord,
} from "./journal";

type Options = JournalOptions;
// The screens of a pupil's report for the staff: the same ones the family has, over the records the account may read.
type PupilTab = "grades" | "attendance" | "day";
const PUPIL_TABS: [PupilTab, string][] = [
  ["grades", "Оценки"],
  ["attendance", "Посещаемость"],
  ["day", "Записи уроков"],
];
const clampDay = (day: string, first: string, last: string) =>
  day < first ? first : day > last ? last : day;

// Set by an opened notification: one pupil's day and lesson, the report card of a period, or the absence notes.
export type JournalFocus =
  | {
      studentId: string;
      day?: string;
      lessonKey?: string;
      finalsPeriodId?: string;
      panel?: never;
    }
  | {
      panel: "absences";
      studentId?: never;
      day?: never;
      lessonKey?: never;
      finalsPeriodId?: never;
    };

// Brings a part of the opened lesson into view; a folded block (a <details>) is opened first, so «Из плана» in the day
// table lands on the open picker. The text of the topic and the homework have ids of their own and open nothing.
function reveal(id: string) {
  const target = document.getElementById(id);
  if (!target) return;
  const fold =
    target instanceof HTMLDetailsElement
      ? target
      : target.querySelector<HTMLDetailsElement>("details[data-reveal]");
  if (fold) fold.open = true;
  target.scrollIntoView({ block: "start" });
}

export default function JournalPanel({
  workspace,
  year,
  child,
  focus,
  view,
  onLeaveView,
}: {
  workspace: string;
  // The archived school year on show; undefined for the current one, so the requests stay as they were.
  year?: string;
  // The child a pupil's or parent's portal looks at; the family screens follow it.
  child?: string;
  focus?: JournalFocus;
  // Set by a menu entry: show this part of the journal alone, if the server offers it to the account;
  // for a pupil or a parent it names the screen of the family journal.
  view?: JournalView;
  // The panel opened a lesson and left that part: the menu and the title must stop naming it.
  onLeaveView?: () => void;
}) {
  const coverageOpened = useRef(false),
    editorPanel = useRef<HTMLElement | null>(null);
  const [options, setOptions] = useState<Options | null>(null),
    [mode, setMode] = useState("day"),
    [only, setOnly] = useState(view),
    // «Тема урока и посещаемость», «Добавить домашнее задание» of the launcher: waits for the day grid of today.
    [aimed, setAimed] = useState<LaunchIntent | null>(null);
  useLaunch(["todayLesson", "homework"], setAimed);
  // The lesson editor is hidden while a part shows alone: what was typed there was given up at the leave prompt.
  useEffect(() => {
    setOnly(view);
    if (view) clearEditor();
  }, [view]);
  const single =
    options?.staff &&
    only &&
    (journalPanels(options) as JournalView[]).includes(only)
      ? (only as JournalPanelName)
      : undefined;
  const [day, setDay] = useState(""),
    // The month on the calendar (YYYY-MM), which its red days follow.
    [month, setMonth] = useState("");
  // The list belongs to the day it was read for: a reload of the same day keeps it on screen, so an open editor stays mounted.
  const [listed, setListed] = useState<{ day: string; items: DayLesson[] }>({
      day: "",
      items: [],
    }),
    [search, setSearch] = useState(""),
    [classFilter, setClassFilter] = useState(""),
    [pupilTab, setPupilTab] = useState<PupilTab>("grades");
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
  const [history, setHistory] = useState<JournalHistory[]>([]),
    [historyMore, setHistoryMore] = useState(false),
    [historical, setHistorical] = useState<JournalHistory | null>(null);
  const [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false),
    [epoch, setEpoch] = useState(0);
  const [dueNote, setDueNote] = useState(""),
    [dueBusy, setDueBusy] = useState(false);
  const sequence = useRef(0),
    saveLock = useRef(false),
    // The part of the editor to scroll to once the opened lesson is on screen: the topic or the pupil cards.
    aim = useRef<{ key: string; id: string } | null>(null);
  useEffect(() => {
    let live = true;
    setError("");
    api(`journal/options${year ? `?year=${year}` : ""}`, workspace)
      .then((r: Options) => {
        if (!live) return;
        setOptions(r);
        setMode(r.staff ? "day" : "family");
        const today = r.today ?? new Date().toLocaleDateString("sv-SE");
        setDay(clampDay(today, r.yearStart, r.yearEnd));
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace, year]);
  useEffect(() => {
    if (
      !options?.staff ||
      !focus?.day ||
      !options?.students.some((s) => s.id === focus.studentId)
    )
      return;
    setMode("pupil");
    setPupilTab("day");
  }, [options, focus]);
  useEffect(() => {
    if (
      !options?.staff ||
      focus?.panel !== "absences" ||
      !options.panels?.absences
    )
      return;
    document
      .getElementById("journal-panel-absences")
      ?.scrollIntoView({ block: "start" });
  }, [options, focus]);
  useEffect(() => {
    if (!options || single || !day || mode !== "day") return;
    let live = true;
    setLoading(true);
    setError("");
    api(`journal/day?day=${day}`, workspace)
      .then((r) => {
        if (live) setListed({ day, items: r.lessons });
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
  }, [workspace, options, single, day, mode, epoch]);
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
      records: updateRecord(c.records, id, update),
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
  // Resolves to false when the revisions could not be read.
  async function historyPage(before?: number) {
    if (!entry) return true;
    setBusy(true);
    setError("");
    try {
      const r = await api(
        `journal/history?${new URLSearchParams({ lessonKey: entry.lessonKey, ...(before ? { beforeVersion: String(before) } : {}) })}`,
        workspace,
      );
      setHistory((rows) => (before ? [...rows, ...r] : r));
      setHistoryMore(hasEarlierRevisions(r));
      return true;
    } catch (e: any) {
      setError(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  // The lesson changed in a way the day list must show: it is read again.
  function refreshDay() {
    setEpoch((n) => n + 1);
  }
  // A homework mark was published under the lesson: the list is read again, and the lesson too unless it holds unsaved fields.
  function homeworkGraded() {
    if (!entry) return;
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
  }
  // The files of the homework are being sent: saving waits until the upload ends.
  function uploading(value: boolean) {
    saveLock.current = value;
    setBusy(value);
  }
  async function withdraw() {
    if (!entry) return;
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
      setMessage("Запись скрыта от учеников и родителей.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function showVersion(version: number) {
    if (!entry) return;
    setBusy(true);
    try {
      const r = await api(
        `journal/history?${new URLSearchParams({ lessonKey: entry.lessonKey, version: String(version) })}`,
        workspace,
      );
      setHistorical(r[0] || null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function closeVersion() {
    setHistorical(null);
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
  // A lesson named by a part of the journal (the gradebook, the coverage report) opens in the day view, and the menu stops naming the part.
  function openLesson(key: string, date: string) {
    if (!canLeave()) return;
    setOnly(undefined);
    onLeaveView?.();
    coverageOpened.current = true;
    clearEditor();
    // The administrator lands in the class of the lesson, picked below once the list of the day is read.
    setSearch("");
    setClassFilter("");
    setMode("day");
    setDay(date);
    void openEntry(key, false, date);
  }
  // A lesson of the day grid: the lesson opens in place of the day; `target` is the place of the editor to scroll to.
  function openFromGrid(key: string, target = "") {
    if (entry?.lessonKey === key) {
      if (target) reveal(target);
      else if (canLeave()) clearEditor();
      return;
    }
    if (!canLeave()) return;
    aim.current = target ? { key, id: target } : null;
    void openEntry(key);
  }
  // A part of the journal opens alone, from the menu, and is open at once. The course table is the gradebook of the
  // teacher: course cards, then the table of one course at full width.
  function adminPanel(name: JournalPanelName) {
    switch (name) {
      case "courseTable":
        return (
          <TeacherGradebook
            key={`gradebook-${workspace}`}
            workspace={workspace}
            onOpen={openLesson}
          />
        );
      case "finalGrades":
        return (
          <FinalGradesPanel
            key={`final-grades-${workspace}`}
            workspace={workspace}
            year={year}
            focus={focus?.finalsPeriodId ? focus : undefined}
          />
        );
      case "absences":
        return (
          <div key={workspace} id="journal-panel-absences">
            <AbsencePanel workspace={workspace} defaultOpen />
          </div>
        );
      case "periods":
        return (
          <JournalPeriodsPanel
            key={`periods-${workspace}`}
            workspace={workspace}
            defaultOpen
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
            defaultOpen
            onOpen={openLesson}
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
        // The grid of the classes comes with the summary of attendance when the account has one (the administrator).
        return (
          <Fragment key={`attendance-grid-${workspace}`}>
            <AttendanceGridPanel workspace={workspace} defaultOpen />
            {panels.includes("attendance") && adminPanel("attendance")}
          </Fragment>
        );
    }
  }
  // The editor of the open lesson; it is shown in place of the day, at the full width of the panel.
  function editor() {
    if (!entry) return null;
    return (
      <LessonEditor
        workspace={workspace}
        entry={entry}
        panelRef={editorPanel}
        dirty={dirty}
        busy={busy}
        editable={editable}
        syncRoster={syncRoster}
        categories={categories}
        lessonMarks={lessonMarks}
        customWeights={customWeights}
        setCustomWeights={setCustomWeights}
        dueNote={dueNote}
        dueBusy={dueBusy}
        nobodyAbsent={nobodyAbsent}
        history={history}
        historyMore={historyMore}
        historical={historical}
        actions={{
          content,
          patchWork,
          pupil,
          grade,
          save,
          openEntry,
          historyPage,
          dueNextLesson,
          withdraw,
          showVersion,
          closeVersion,
          uploading,
          refreshDay,
          homeworkGraded,
          close: () => {
            if (canLeave()) clearEditor();
          },
        }}
      />
    );
  }
  const panels = options ? journalPanels(options) : [];
  const today = options?.today ?? new Date().toLocaleDateString("sv-SE");
  // A lesson that has not ended yet is not «не заполнено»; the time of day is the computer's.
  const wall = new Date();
  const readAt = {
    date: today,
    time: `${String(wall.getHours()).padStart(2, "0")}:${String(wall.getMinutes()).padStart(2, "0")}`,
  };
  const ready = listed.day === day;
  const lessons = ready ? listed.items : [];
  const admin = !!options?.admin,
    query = search.trim();
  // Every class of the school, also those without a lesson on the day.
  const known = [
    ...new Set(
      (options?.students ?? []).map((s) => s.className).filter(Boolean),
    ),
  ] as string[];
  const summaries = admin ? classSummaries(lessons, known, readAt) : [];
  const classNames = [
    ...new Set([...summaries.map((c) => c.name), classFilter].filter(Boolean)),
  ].sort(
    (a, b) =>
      (a === NO_CLASS ? 1 : 0) - (b === NO_CLASS ? 1 : 0) || naturalOrder(a, b),
  );
  // The administrator sees the classes first; a class, or a search over the whole school, shows lessons.
  const classesView = admin && !classFilter && !query;
  const found =
    admin && !classFilter && query
      ? searchLessons(lessons, query)
      : { rows: [], more: 0 };
  const shown = classesView
    ? []
    : admin && !classFilter
      ? found.rows
      : (classFilter ? lessonsOfClass(lessons, classFilter) : lessons).filter(
          (l) => matchesQuery(l, query),
        );
  const profile = dayProfile(lessons, options?.profiles);
  const slots = profile && day ? slotsFor(profile, day) : [];
  const rows = lessonRows(shown, slots);
  // A lesson opened from another screen (or from the search) puts the administrator into its class, so «← Журнал дня» lands there.
  const openKey = entry?.lessonKey;
  const moveTo =
    admin && ready
      ? (() => {
          const open = lessons.find((l) => l.lessonKey === openKey);
          return open ? classToShow(open, classFilter, query) : null;
        })()
      : null;
  useEffect(() => {
    if (moveTo) setClassFilter(moveTo);
  }, [moveTo]);
  // The opened lesson takes the place of the day: it is brought to the top and takes the focus once, when it opens; a
  // place named by the day table (the pupils, the topic of the plan) is shown after that.
  const shownLesson = useRef("");
  useEffect(() => {
    if (!openKey) return void (shownLesson.current = "");
    if (!editorPanel.current) return;
    if (shownLesson.current !== openKey) {
      shownLesson.current = openKey;
      coverageOpened.current = false;
      editorPanel.current.focus({ preventScroll: true });
      editorPanel.current.scrollIntoView({ block: "start" });
    }
    if (aim.current?.key === openKey) {
      const id = aim.current.id;
      aim.current = null;
      reveal(id);
    }
  }, [openKey, entry]);
  // The calendar colours the days of the own lessons; the administrator sees one class at a time.
  const coverage: CoverageScope | null = !options?.staff
    ? null
    : options.admin
      ? classFilter
        ? { mode: "classes", className: classFilter }
        : null
      : { mode: "teaching" };
  const unfilled = useCoverageDays(
    workspace,
    coverage,
    month,
    { start: options?.yearStart ?? "", end: options?.yearEnd ?? "" },
    epoch,
  );
  const title = day
    ? `${weekdayName(day).replace(/^./, (c) => c.toUpperCase())}, ${dayText(day)}`
    : "";
  // The launcher opens the lesson of today that is on or comes next, at the topic or at the homework; the administrator gets the day alone.
  useEffect(() => {
    if (!aimed || !options?.staff || single) return;
    const finish = () => setAimed(null);
    if (mode !== "day") {
      if (!canLeave()) return finish();
      clearEditor();
      return setMode("day");
    }
    if (day !== clampDay(today, options.yearStart, options.yearEnd)) {
      if (!canLeave()) return finish();
      clearEditor();
      return setDay(clampDay(today, options.yearStart, options.yearEnd));
    }
    if (!ready || loading) return;
    finish();
    if (options.admin) return;
    const clock = new Date(),
      now = clock.getHours() * 60 + clock.getMinutes();
    const lesson = lessonOfNow(lessons, now);
    if (lesson)
      openFromGrid(
        lesson.lessonKey,
        aimed === "homework" ? "journal-homework" : "journal-lesson-topic-text",
      );
  }, [aimed, options, single, mode, day, ready, loading]);
  // Another class (or all classes) in place; a lesson open in the editor is left first.
  function chooseClass(name: string) {
    if (name === classFilter) return;
    if (entry) {
      if (!canLeave()) return;
      clearEditor();
    }
    setClassFilter(name);
  }
  function goDay(next: string) {
    if (!options || !next) return;
    const target = clampDay(next, options.yearStart, options.yearEnd);
    if (target === day || !canLeave()) return;
    clearEditor();
    setDay(target);
  }
  return (
    <section className="journal-panel">
      {options && !options.staff ? null : !only || (options && !single) ? (
        <p>
          Посещаемость, оценки, темы и домашние задания. Записи журнала
          сохраняются отдельно от расписания; переносы и правки групп не удаляют
          прежние отметки.
        </p>
      ) : null}
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
      {options && !options.staff && (
        <FamilyJournal
          workspace={workspace}
          year={year}
          options={options}
          chosen={child}
          view={view}
          focus={focus}
        />
      )}
      {single && (
        <section
          className="journal-admin-panels"
          aria-label="Отчёты и управление журналом"
        >
          {adminPanel(single)}
        </section>
      )}
      {options?.staff && !single && (
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
                  aria-pressed={mode === "pupil"}
                  disabled={busy}
                  onClick={() => {
                    if (canLeave()) {
                      clearEditor();
                      setMode("pupil");
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
          {mode === "day" && editor()}
          {mode === "day" ? (
            <div className="teacher-day-layout" hidden={!!entry}>
              <div className="teacher-day-main">
                <header className="teacher-day-head">
                  <h2>{title}</h2>
                  <div className="pupil-actions">
                    <button
                      type="button"
                      className="pupil-arrow"
                      aria-label="Предыдущий день"
                      disabled={busy || day <= options.yearStart}
                      onClick={() => goDay(shiftDate(day, -1))}
                    >
                      ‹
                    </button>
                    <button
                      type="button"
                      className="pupil-arrow"
                      aria-label="Следующий день"
                      disabled={busy || day >= options.yearEnd}
                      onClick={() => goDay(shiftDate(day, 1))}
                    >
                      ›
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => goDay(today)}
                    >
                      Сегодня
                    </button>
                    <label>
                      <span className="sr-only">Дата журнала</span>
                      <input
                        type="date"
                        value={day}
                        min={options.yearStart}
                        max={options.yearEnd}
                        disabled={busy}
                        onChange={(e) => goDay(e.target.value)}
                      />
                    </label>
                  </div>
                </header>
                <div className="teacher-day-tools">
                  {admin && (
                    <>
                      {classFilter && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => chooseClass("")}
                        >
                          ← Все классы
                        </button>
                      )}
                      <label className="field">
                        <span>Класс</span>
                        <select
                          value={classFilter}
                          disabled={busy}
                          onChange={(e) => chooseClass(e.target.value)}
                        >
                          <option value="">Все классы</option>
                          {classNames.map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                        </select>
                      </label>
                    </>
                  )}
                  <label className="field">
                    <span>Найти урок</span>
                    <input
                      type="search"
                      placeholder="Предмет, класс или учитель"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </label>
                </div>
                {loading && !ready && <p role="status">Загружаю уроки…</p>}
                {ready && !loading && !lessons.length && (
                  <p className="empty-state">
                    Доступных уроков на эту дату нет.
                  </p>
                )}
                {ready &&
                  !loading &&
                  !!lessons.length &&
                  !classesView &&
                  !rows.length && (
                    <p className="empty-state">
                      {query
                        ? "По запросу уроков не найдено."
                        : `В классе ${classFilter} нет уроков на эту дату.`}
                    </p>
                  )}
                {ready && classesView && (
                  <ClassesTable
                    classes={summaries}
                    total={countFill(lessons, readAt)}
                    busy={busy}
                    onOpen={chooseClass}
                  />
                )}
                {!!rows.length && (
                  <LessonsTable
                    rows={rows}
                    label={`Уроки ${dayText(day)}`}
                    showClass={!classFilter}
                    now={readAt}
                    busy={busy}
                    onOpen={(key) => openFromGrid(key)}
                    onPupils={(key) => openFromGrid(key, "journal-pupil-cards")}
                    onPlan={(key) => openFromGrid(key, "journal-lesson-topic")}
                  />
                )}
                {found.more > 0 && (
                  <p className="pupil-muted">
                    Показаны первые {found.rows.length} уроков, ещё {found.more}
                    : уточните запрос или выберите класс.
                  </p>
                )}
              </div>
              <aside className="teacher-day-side">
                <MonthCalendar
                  value={day}
                  today={today}
                  min={options.yearStart}
                  max={options.yearEnd}
                  mark={(d) =>
                    unfilled.has(d) ? "month-calendar-unfilled" : undefined
                  }
                  markLabel="есть незаполненные уроки"
                  onSelect={goDay}
                  onMonth={setMonth}
                />
                {coverage && (
                  <p className="pupil-muted teacher-calendar-note">
                    Красным отмечены дни с незаполненными уроками.
                  </p>
                )}
              </aside>
            </div>
          ) : (
            <>
              <div
                role="tablist"
                aria-label="Разделы отчёта ученика"
                className="pupil-tabs"
              >
                {PUPIL_TABS.map(([id, name]) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={pupilTab === id}
                    onClick={() => setPupilTab(id)}
                  >
                    {name}
                  </button>
                ))}
              </div>
              <FamilyJournal
                workspace={workspace}
                year={year}
                options={options}
                view={
                  pupilTab === "grades"
                    ? "pupilGrades"
                    : pupilTab === "attendance"
                      ? "pupilAttendance"
                      : undefined
                }
                focus={focus}
                family={false}
              />
            </>
          )}
        </>
      )}
    </section>
  );
}

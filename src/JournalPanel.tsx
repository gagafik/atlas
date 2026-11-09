import { Fragment, useEffect, useRef, useState } from "react";
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
import FamilyJournal from "./FamilyJournal";
import PublishedRows from "./PublishedRows";
import { useLessonMarks } from "./LessonMarks";
import LessonPupilCards from "./LessonPupilCards";
import AttendancePanel from "./AttendancePanel";
import AttendanceGridPanel from "./AttendanceGridPanel";
import JournalCoveragePanel from "./JournalCoveragePanel";
import TeacherGradebook from "./TeacherGradebook";
import JournalPeriodsPanel from "./JournalPeriodsPanel";
import FinalGradesPanel from "./FinalGradesPanel";
import MonthCalendar from "./MonthCalendar";
import TeacherDayGrid from "./TeacherDayGrid";
import { dayProfile, slotsFor } from "./bells";
import { dayText, shiftDate, weekdayName } from "./pupilJournal";
import { useCoverageDays, type CoverageScope } from "./useCoverageDays";
import { useLaunch, type LaunchIntent } from "./launch";
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
  attendanceProblem,
  lessonOfNow,
  attendanceText,
  gradeLabel,
  journalCsv,
  documentRows,
  numberField,
  numberValue,
  contentProblem,
  withKind,
  hasEarlierRevisions,
  journalPanels,
  noAbsentees,
  withNoAbsentees,
  type Assessment,
  type DayLesson,
  type Grade,
  type JournalContent,
  type JournalDocument,
  type JournalEntry,
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
interface History {
  version: number;
  updated: string;
  actor: string;
  action: string;
  document?: JournalDocument;
}

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
  const [history, setHistory] = useState<History[]>([]),
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
  // A lesson named by a part of the journal (the gradebook, the coverage report) opens in the day view, and the menu stops naming the part.
  function openLesson(key: string, date: string) {
    if (!canLeave()) return;
    setOnly(undefined);
    onLeaveView?.();
    coverageOpened.current = true;
    clearEditor();
    setMode("day");
    setDay(date);
    void openEntry(key, false, date);
  }
  // A lesson of the day grid: the editor opens under its row (again to close it); `target` is the place of the editor to scroll to.
  function openFromGrid(key: string, target = "") {
    if (entry?.lessonKey === key) {
      if (target)
        document.getElementById(target)?.scrollIntoView({ block: "start" });
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
            defaultOpen
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
  // The editor of the open lesson; the day grid shows it right under the row of the lesson.
  function editor() {
    if (!entry) return null;
    return (
      <section
        className="journal-editor"
        ref={editorPanel}
        tabIndex={-1}
        aria-label="Редактор журнала урока"
      >
        <header>
          <div>
            <h2>
              {entry.document.lesson.subject} · {entry.document.lesson.group}
            </h2>
            <p>
              {entry.document.lesson.date} · {entry.document.lesson.start}–
              {entry.document.lesson.end} · {entry.document.lesson.teachers}
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
            Состав группы не подтверждён полностью. Здесь показаны только
            внесённые ученики.
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
            Урок ещё не прошёл. Тему, домашнее задание и материалы можно
            опубликовать заранее; посещаемость и оценки публикуются в день
            урока.
          </p>
        )}
        {entry.rosterFrozen && entry.hasPublished && (
          <p className="info-line">
            Состав, время и учителя урока зафиксированы при публикации:
            последующие изменения расписания и составов их не меняют.
          </p>
        )}
        {entry.needsSync && entry.writable && (
          <div className="info-line">
            <p>
              Расписание или состав группы изменились. Просмотрите обновление
              перед редактированием. Прежние ученики и их оценки сохранятся;
              новые ученики появятся без отметок.
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
            Обновлённый состав показан для проверки. Он сохранится вместе с
            записью.
          </p>
        )}
        <fieldset disabled={!editable} className="journal-content-fields">
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
          <div id="journal-lesson-topic">
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
          </div>
          <label className="field" id="journal-homework">
            <span>Домашнее задание</span>
            <textarea
              maxLength={4000}
              value={entry.document.content.homework}
              onChange={(e) =>
                content((c) => ({
                  ...c,
                  homework: e.target.value,
                  dueDate: e.target.value ? c.dueDate : null,
                  homeworkFiles: e.target.value ? c.homeworkFiles : [],
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
              value={entry.document.content.homeworkSubmission || "online"}
              onChange={(e) =>
                content((c) => ({
                  ...c,
                  homeworkSubmission: e.target.value as "online" | "offline",
                  homeworkLatePolicy:
                    e.target.value === "offline"
                      ? "allow"
                      : c.homeworkLatePolicy,
                }))
              }
            >
              <option value="online">Ученики отправляют ответ в Атласе</option>
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
                entry.document.content.homeworkSubmission === "offline"
              }
              value={entry.document.content.homeworkLatePolicy || "allow"}
              onChange={(e) =>
                content((c) => ({
                  ...c,
                  homeworkLatePolicy: e.target.value as "allow" | "block",
                }))
              }
            >
              <option value="allow">Разрешать с отметкой об опоздании</option>
              <option value="block">Закрывать приём после срока</option>
            </select>
            <small>
              Срок заканчивается в 23:59 по времени школы (Кызылорда). При
              закрытом приёме для повторной сдачи после возврата тоже
              потребуется продление срока.
            </small>
          </label>
          <LessonMaterials
            key={workspace + entry.lessonKey + entry.version}
            items={entry.document.content.materials || []}
            workspace={workspace}
            lessonKey={entry.lessonKey}
            disabled={!editable}
            onChange={(materials) => content((c) => ({ ...c, materials }))}
          />
          <AssignmentFilesPanel
            key={workspace + entry.lessonKey}
            entry={entry}
            workspace={workspace}
            disabled={!editable}
            change={(ids) => content((c) => ({ ...c, homeworkFiles: ids }))}
            onUploading={(value) => {
              saveLock.current = value;
              setBusy(value);
            }}
          />
          <div className="journal-assessments">
            <h3>Проверочные работы</h3>
            <p>
              Баллы — от 0 до выбранного максимума. Текстовые оценки (A*, зачёт
              и другие) не входят в средний процент. Вес 0 исключает работу из
              расчёта; итог периода считается по долям категорий школы.
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
                        entry.document.content.assessments.length >= 10 ||
                        entry.document.content.assessments.some(
                          (x) => x.bookingId === b.id,
                        )
                      }
                      onClick={() =>
                        content((c) => ({
                          ...c,
                          assessments: [...c.assessments, bookingDefault(b)],
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
                          x.id === a.id ? { ...x, name: e.target.value } : x,
                        ),
                      }))
                    }
                  />
                </label>
                <label className="field">
                  <span>Формат</span>
                  <select
                    value={a.kind}
                    disabled={entry.document.content.records.some((p) =>
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
                            ? withKind(x, e.target.value as Assessment["kind"])
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
                          k.active || k.id === categoryOf(a, categories).id,
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
                  entry.bookings?.some((b) => b.id === a.bookingId) && (
                    <p className="muted category-note">
                      Из календаря:{" "}
                      {entry.bookings.find((b) => b.id === a.bookingId)?.short}
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
                                    maxScore: numberField(e.target.value),
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
                            (g.score !== null || g.label || g.comment),
                        ),
                      ) &&
                      !window.confirm(
                        "Удалить работу и её оценки из текущей редакции? Сохранённые редакции останутся в истории.",
                      )
                    )
                      return;
                    content((c) => ({
                      ...c,
                      assessments: c.assessments.filter((x) => x.id !== a.id),
                      records: c.records.map((p) => ({
                        ...p,
                        grades: p.grades.filter((g) => g.assessmentId !== a.id),
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
              disabled={entry.document.content.assessments.length >= 10}
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
          <h3 id="journal-pupil-cards">Ученики и посещаемость</h3>
          <p>
            Комментарии к ученику и оценкам будут видны этому ученику и его
            родителю после публикации.
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
                Отметит присутствующими всех, кто не отмечен, и сразу сохранит
                урок для семьи.
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
                <button disabled={!editable} onClick={() => void save(false)}>
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
              <button disabled={!editable} onClick={() => void save(true)}>
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
                  setMessage("Запись скрыта от учеников и родителей.");
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
                  № {h.version} · {new Date(h.updated).toLocaleString("ru-RU")}{" "}
                  · {h.actor} ·{" "}
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
                onClick={() => void historyPage(history.at(-1)!.version)}
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
              {historical.document.content.homeworkLatePolicy === "block"
                ? "приём закрывается после срока"
                : "разрешена с отметкой"}
              .
            </p>
            {historical.document.content.homeworkSubmission === "offline" && (
              <p>Сдача: без онлайн-сдачи.</p>
            )}
            <SavedPlanTopic value={historical.document.content.curriculum} />
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
    );
  }
  const panels = options ? journalPanels(options) : [];
  const today = options?.today ?? new Date().toLocaleDateString("sv-SE");
  const ready = listed.day === day;
  const lessons = ready ? listed.items : [];
  // A lesson of another day opens before the list of that day is read; the editor mounts with the list, and only then can it take the focus.
  useEffect(() => {
    if (!entry || !editorPanel.current) return;
    if (coverageOpened.current) {
      coverageOpened.current = false;
      editorPanel.current.focus({ preventScroll: true });
      editorPanel.current.scrollIntoView({ block: "start" });
    }
    if (aim.current?.key === entry.lessonKey) {
      const id = aim.current.id;
      aim.current = null;
      document.getElementById(id)?.scrollIntoView({ block: "start" });
    }
  }, [entry, ready]);
  const classNames = [
    ...new Set(
      [...lessons.flatMap((l) => l.classes), classFilter].filter(Boolean),
    ),
  ].sort((a, b) => a.localeCompare(b, "ru", { numeric: true }));
  const shown = lessons.filter(
    (l) =>
      (!classFilter || l.classes.includes(classFilter)) &&
      `${l.lesson.subject} ${l.lesson.group} ${l.lesson.teachers} ${l.classes.join(" ")}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
  );
  const profile = dayProfile(lessons, options?.profiles);
  const slots = profile && day ? slotsFor(profile, day) : [];
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
        aimed === "homework" ? "journal-homework" : "journal-lesson-topic",
      );
  }, [aimed, options, single, mode, day, ready, loading]);
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
          {mode === "day" ? (
            <div className="teacher-day-layout">
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
                  {options.admin && (
                    <label className="field">
                      <span>Класс</span>
                      <select
                        value={classFilter}
                        onChange={(e) => setClassFilter(e.target.value)}
                      >
                        <option value="">Все классы</option>
                        {classNames.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
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
                {loading && !ready && <p role="status">Загружаю уроки…</p>}
                {ready && !loading && !shown.length && (
                  <p className="empty-state">
                    Доступных уроков на эту дату нет.
                  </p>
                )}
                <TeacherDayGrid
                  day={day}
                  lessons={shown}
                  slots={slots}
                  selectedKey={entry?.lessonKey}
                  busy={busy}
                  onOpen={(key) => openFromGrid(key)}
                  onPupils={(key) => openFromGrid(key, "journal-pupil-cards")}
                  onPlan={(key) => openFromGrid(key, "journal-lesson-topic")}
                  renderEditor={editor}
                />
                {ready &&
                  entry &&
                  !shown.some((l) => l.lessonKey === entry.lessonKey) &&
                  editor()}
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

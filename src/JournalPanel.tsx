import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import HomeworkPanel from "./HomeworkPanel";
import LessonMaterials from "./LessonMaterials";
import LessonTopic, { SavedPlanTopic } from "./LessonTopic";
import AssignmentFilesPanel, {
  SavedMaterialLinks,
} from "./AssignmentFilesPanel";
import AbsencePanel from "./AbsencePanel";
import AttendancePanel from "./AttendancePanel";
import JournalCoveragePanel from "./JournalCoveragePanel";
import JournalPeriodsPanel from "./JournalPeriodsPanel";
import FinalGradesPanel from "./FinalGradesPanel";
import {
  attendanceNames,
  gradeLabel,
  journalCsv,
  documentRows,
  type Assessment,
  type Grade,
  type JournalContent,
  type JournalDocument,
  type JournalEntry,
  type JournalLesson,
  type JournalReport,
  type JournalRow,
  type PupilRecord,
} from "./journal";

interface Options {
  staff: boolean;
  absenceNotices?: boolean;
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
}: {
  rows: JournalRow[];
  workspace?: string;
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
            <strong>{attendanceNames[row.record.attendance]}</strong>
            {row.record.lateMinutes > 0 && ` · ${row.record.lateMinutes} мин`}
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
                <strong>
                  {a.name}: {gradeLabel(g, a)}
                </strong>
                {a.kind === "points" && ` · вес ${a.weight}`}
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

export default function JournalPanel({ workspace }: { workspace: string }) {
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
    [historical, setHistorical] = useState<History | null>(null);
  const [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false),
    [epoch, setEpoch] = useState(0);
  const sequence = useRef(0),
    saveLock = useRef(false);
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
        setStart(r.yearStart);
        setEnd(r.yearEnd);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
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
    setLoading(true);
    setReport(null);
    setError("");
    const params = new URLSearchParams({ studentId: student, start, end });
    api(`journal/report?${params}`, workspace)
      .then((r) => {
        if (live) setReport(r);
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
    setEntry(null);
    setDirty(false);
    setSyncRoster(false);
    setHistory([]);
    setHistorical(null);
    setMessage("");
  }
  async function openEntry(key: string, sync = false, entryDay = day) {
    const current = ++sequence.current;
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
  async function save(publish: boolean) {
    if (!entry || saveLock.current) return;
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
        content: entry.document.content,
        publish,
        syncRoster,
      });
      setEntry(r);
      setDirty(false);
      setSyncRoster(false);
      setEpoch((n) => n + 1);
      setHistory([]);
      setHistorical(null);
      setMessage(
        publish
          ? "Запись опубликована. Ученики видят каждый свои оценки и отметки."
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
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const editable = !!entry?.writable && !entry.needsSync && !busy;
  return (
    <section className="journal-panel">
      {options && (
        <FinalGradesPanel
          key={`final-grades-${workspace}`}
          workspace={workspace}
        />
      )}
      {options?.absenceNotices && (
        <AbsencePanel key={workspace} workspace={workspace} />
      )}
      {options?.staff && (
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
      )}
      {options?.staff && (
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
      )}
      {options?.staff && (
        <AttendancePanel
          key={`attendance-${workspace}`}
          workspace={workspace}
        />
      )}
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
                    {entry.hasPublished
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
                    <label className="field">
                      <span>Приём после срока</span>
                      <select
                        disabled={!entry.document.content.dueDate}
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
                        исключает работу из расчёта.
                      </p>
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
                                      ? {
                                          ...x,
                                          kind: e.target
                                            .value as Assessment["kind"],
                                        }
                                      : x,
                                  ),
                                }))
                              }
                            >
                              <option value="points">Баллы</option>
                              <option value="label">Текстовая оценка</option>
                            </select>
                          </label>
                          {a.kind === "points" && (
                            <>
                              <label className="field">
                                <span>Максимум баллов</span>
                                <input
                                  type="number"
                                  min="0.01"
                                  max="10000"
                                  step="0.01"
                                  value={a.maxScore}
                                  onChange={(e) =>
                                    content((c) => ({
                                      ...c,
                                      assessments: c.assessments.map((x) =>
                                        x.id === a.id
                                          ? {
                                              ...x,
                                              maxScore: Number(e.target.value),
                                            }
                                          : x,
                                      ),
                                    }))
                                  }
                                />
                              </label>
                              <label className="field">
                                <span>Вес работы</span>
                                <input
                                  type="number"
                                  min="0"
                                  max="100"
                                  step="0.01"
                                  value={a.weight}
                                  onChange={(e) =>
                                    content((c) => ({
                                      ...c,
                                      assessments: c.assessments.map((x) =>
                                        x.id === a.id
                                          ? {
                                              ...x,
                                              weight: Number(e.target.value),
                                            }
                                          : x,
                                      ),
                                    }))
                                  }
                                />
                              </label>
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
                    <button
                      type="button"
                      onClick={() =>
                        content((c) => ({
                          ...c,
                          records: c.records.map((p) =>
                            p.attendance === "unmarked" &&
                            entry.document.lesson.students.find(
                              (s) => s.id === p.studentId,
                            )?.enrolled
                              ? { ...p, attendance: "present" }
                              : p,
                          ),
                        }))
                      }
                    >
                      Неотмеченные присутствуют
                    </button>
                    {!entry.document.lesson.students.length && (
                      <p className="empty-state">
                        В составе нет учеников. Добавьте их в справочник и
                        группу.
                      </p>
                    )}
                    <div className="journal-pupils">
                      {entry.document.lesson.students.map((s) => {
                        const record = entry.document.content.records.find(
                          (p) => p.studentId === s.id,
                        )!;
                        return (
                          <article className="journal-pupil" key={s.id}>
                            {entry.attendanceApprovals?.[s.id] && (
                              <p className="info-line">
                                На этот урок принята объяснительная. Отметка
                                «Отсутствует» учитывается как уважительная
                                причина; исходная отметка сохраняется.
                              </p>
                            )}
                            <h4>
                              {s.name}{" "}
                              <small>
                                {s.className}
                                {!s.enrolled ? " · прежний состав" : ""}
                              </small>
                            </h4>
                            <div className="journal-pupil-fields">
                              <label className="field">
                                <span>Посещаемость</span>
                                <select
                                  value={record.attendance}
                                  onChange={(e) =>
                                    pupil(s.id, (p) => ({
                                      ...p,
                                      attendance: e.target.value,
                                      lateMinutes:
                                        e.target.value === "late"
                                          ? p.lateMinutes
                                          : 0,
                                    }))
                                  }
                                >
                                  {Object.entries(attendanceNames).map(
                                    ([id, name]) => (
                                      <option key={id} value={id}>
                                        {name}
                                      </option>
                                    ),
                                  )}
                                </select>
                              </label>
                              {record.attendance === "late" && (
                                <label className="field">
                                  <span>Минуты опоздания</span>
                                  <input
                                    type="number"
                                    min="0"
                                    max="600"
                                    value={record.lateMinutes}
                                    onChange={(e) =>
                                      pupil(s.id, (p) => ({
                                        ...p,
                                        lateMinutes: Number(e.target.value),
                                      }))
                                    }
                                  />
                                </label>
                              )}
                              <label className="field">
                                <span>Комментарий ученику</span>
                                <input
                                  maxLength={1000}
                                  value={record.comment}
                                  onChange={(e) =>
                                    pupil(s.id, (p) => ({
                                      ...p,
                                      comment: e.target.value,
                                    }))
                                  }
                                />
                              </label>
                            </div>
                            {entry.document.content.assessments.map((a) => {
                              const g = record.grades.find(
                                (g) => g.assessmentId === a.id,
                              );
                              return (
                                <div className="journal-grade" key={a.id}>
                                  <label className="field">
                                    <span>
                                      {a.name}
                                      {a.kind === "points"
                                        ? ` (из ${a.maxScore})`
                                        : ""}
                                    </span>
                                    {a.kind === "points" ? (
                                      <input
                                        type="number"
                                        min="0"
                                        max={a.maxScore}
                                        step="0.01"
                                        value={g?.score ?? ""}
                                        onChange={(e) =>
                                          grade(s.id, a, {
                                            score:
                                              e.target.value === ""
                                                ? null
                                                : Number(e.target.value),
                                          })
                                        }
                                      />
                                    ) : (
                                      <input
                                        maxLength={30}
                                        value={g?.label || ""}
                                        onChange={(e) =>
                                          grade(s.id, a, {
                                            label: e.target.value,
                                          })
                                        }
                                      />
                                    )}
                                  </label>
                                  <label className="field">
                                    <span>Комментарий к оценке</span>
                                    <input
                                      maxLength={1000}
                                      value={g?.comment || ""}
                                      onChange={(e) =>
                                        grade(s.id, a, {
                                          comment: e.target.value,
                                        })
                                      }
                                    />
                                  </label>
                                </div>
                              );
                            })}
                          </article>
                        );
                      })}
                    </div>
                  </fieldset>
                  <div className="dated-toolbar journal-savebar">
                    <button
                      disabled={!editable}
                      onClick={() => void save(false)}
                    >
                      Сохранить черновик
                    </button>
                    <button
                      className="primary"
                      disabled={!editable}
                      onClick={() => void save(true)}
                    >
                      {entry.hasPublished
                        ? "Обновить для учеников"
                        : "Опубликовать ученикам"}
                    </button>
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
                      {history.length % 50 === 0 && (
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
              </div>
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
                      disabled={!report.rows.length}
                      onClick={() =>
                        journalCsv(report.rows, "Личный журнал.csv")
                      }
                    >
                      Скачать журнал CSV
                    </button>
                    <button onClick={() => window.print()}>
                      Печать журнала
                    </button>
                  </div>
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
                      <strong>{report.summary.lateMinutes} мин</strong>
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
                        </article>
                      ))}
                    </div>
                  )}
                  {!report.rows.length && (
                    <p className="empty-state">
                      Опубликованных записей за этот период нет.
                    </p>
                  )}
                  <PublishedRows rows={report.rows} workspace={workspace} />
                </>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

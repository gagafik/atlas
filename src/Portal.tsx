import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { type AuthStatus, type AccessView } from "./auth";
import { csvCell, download } from "./utils";
import { CHANGE_TYPES } from "./substitutions";
import type { ChangeType } from "./types";
import JournalPanel, { type JournalFocus } from "./JournalPanel";
import HomeworkQueuePanel from "./HomeworkQueuePanel";
import QuizzesPanel from "./QuizzesLoader";
import TeachingMaterialsPanel from "./TeachingMaterialsLoader";
import CurriculumPanel from "./CurriculumLoader";
import { canLeaveHomework } from "./homeworkNavigation";
import { focusRequest, spendFocus, type FocusRequest } from "./focusRequest";
import MessageFeed from "./MessageFeed";
import MessagesPanel from "./MessagesPanel";
import Shell, { type ShellFrame } from "./Shell";
import { useJournalOptions, useMessageUnread } from "./shellData";
import { currentChild, recallChoice, rememberChoice } from "./familyChild";
import { eventPart, eventViews, schoolYear } from "./navigation";
import { bellState, minutesNow } from "./startPage";
import { journalPanels as panelsOf, type JournalView } from "./journal";
import { IntakePortal } from "./IntakePanel";
import { FamilyPortal } from "./FamilyPanel";
import ClassStaffWorkspace from "./ClassStaffWorkspace";
import MyDuties from "./MyDuties";
interface Space {
  id: string;
  name: string;
  revision: number;
  yearStart: string;
  yearEnd: string;
  cycles: { id: string; name: string }[];
  records: { id: string; view: AccessView; name: string }[];
}
interface Row {
  day: number;
  dayName?: string;
  date?: string;
  start: string;
  end: string;
  subject: string;
  group: string;
  teachers: string;
  rooms: string;
  status: string;
  activity: string;
  // Sent for a teacher's own timetable only; pupils and parents never get it.
  changeType?: string | null;
}
interface Schedule {
  revision: number;
  name: string;
  school: string;
  rows: Row[];
  classTeacher?: string | null;
}
const statusLabel = (r: Row) =>
  r.status === "cancelled"
    ? "Отменено"
    : r.activity === "duty"
      ? "Дежурство"
      : r.activity === "booking"
        ? "Бронирование"
        : r.changeType
          ? CHANGE_TYPES[r.changeType as ChangeType] || r.changeType
          : r.status === "changed"
            ? "Изменено"
            : r.status === "added"
              ? "Разовый урок"
              : "По расписанию";
const days = [
  "Понедельник",
  "Вторник",
  "Среда",
  "Четверг",
  "Пятница",
  "Суббота",
  "Воскресенье",
];
// «Начало» of a pupil, a parent or a teacher: the lessons of today for a record of the workspace (the first one, as the schedule opens with it).
// `named` puts the name of the record in the heading (a parent or a viewer looks at someone else's day).
function TodayLessons({
  workspace,
  space,
  named,
  child,
}: {
  workspace: string;
  space?: Space;
  named: boolean;
  // The child the family portal looks at: their lessons are shown and no other record is offered.
  child?: string;
}) {
  const records = space?.records ?? [],
    [chosen, setChosen] = useState(0);
  const bound = child
    ? records.find((r) => r.view === "student" && r.id === child)
    : undefined;
  const record = bound ?? records[chosen];
  const [now, setNow] = useState(() => new Date());
  const [rows, setRows] = useState<Row[] | null>(null),
    [error, setError] = useState("");
  const today = now.toLocaleDateString("sv-SE");
  const day =
    space && today >= space.yearStart && today <= space.yearEnd ? today : "";
  // The countdown moves by the minute; half a minute keeps it from lagging behind the bell.
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    setRows(null);
    setError("");
    if (!record || !day) return;
    let live = true;
    const params = new URLSearchParams({
      view: record.view,
      entityId: record.id,
      day,
    });
    api(`portal/schedule?${params}`, workspace)
      .then((r: Schedule) => {
        if (live)
          setRows([...r.rows].sort((a, b) => a.start.localeCompare(b.start)));
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace, record?.view, record?.id, day]);
  // Duties, bookings and cancelled lessons do not ring a bell of their own.
  const lessons = (rows ?? []).filter(
    (r) =>
      r.status !== "cancelled" &&
      r.activity !== "duty" &&
      r.activity !== "booking",
  );
  const stage = bellState(lessons, minutesNow(now));
  const current = stage?.phase === "lesson" ? lessons[stage.index] : undefined;
  const status = !stage
    ? ""
    : stage.phase === "lesson"
      ? `${current!.subject} · до звонка ${stage.minutes} мин`
      : stage.phase === "break"
        ? `перемена · до звонка ${stage.minutes} мин`
        : stage.phase === "before"
          ? `до первого урока ${stage.minutes} мин`
          : "уроки на сегодня закончились";
  return (
    <section className="today-lessons">
      <header>
        <h2>
          Уроки сегодня
          {named && record && (bound || records.length < 2)
            ? ` · ${record.name}`
            : ""}
        </h2>
        {!bound && records.length > 1 && (
          <select
            aria-label="Чьё расписание"
            value={chosen}
            onChange={(e) => setChosen(Number(e.target.value))}
          >
            {records.map((r, i) => (
              <option key={r.view + ":" + r.id} value={i}>
                {r.name}
              </option>
            ))}
          </select>
        )}
        {status && <strong role="status">{status}</strong>}
      </header>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {!record ? (
        <p className="info-line">
          Расписание появится, когда администратор назначит вам записи.
        </p>
      ) : !day ? (
        <p className="info-line">Сегодня вне учебного года.</p>
      ) : (
        rows &&
        (rows.length ? (
          <div className="portal-day">
            {rows.map((r, i) => (
              <article
                className={`dated-lesson ${r.status === "cancelled" ? "cancelled" : ""} ${r === current ? "current" : ""}`}
                key={i}
              >
                <strong>
                  {r.start}–{r.end}
                </strong>
                <span>{statusLabel(r)}</span>
                <h4>
                  {r.subject} · {r.group}
                </h4>
                <p>
                  {r.teachers || "Учитель не назначен"}
                  {r.rooms ? " · " + r.rooms : ""}
                </p>
              </article>
            ))}
          </div>
        ) : (
          <p className="info-line">На сегодня уроков нет.</p>
        ))
      )}
    </section>
  );
}
export default function Portal({
  session,
  frame,
}: {
  session: AuthStatus;
  frame: ShellFrame;
}) {
  const [section, setSectionValue] = useState("home");
  // A notification can point the journal at one pupil's day, and the schedule at one pupil's timetable;
  // a menu entry can open one part of the journal.
  const [journalFocus, setJournalFocus] = useState<JournalFocus | undefined>(),
    [messageFocus, setMessageFocus] = useState<FocusRequest | undefined>(),
    [journalView, setJournalView] = useState<JournalView | undefined>(),
    [wantedRecord, setWantedRecord] = useState("");
  function setSection(value: string, journal?: JournalView) {
    if (canLeaveHomework()) {
      setSectionValue(value);
      setJournalView(journal);
      setJournalFocus(undefined);
      setMessageFocus(undefined);
    }
  }
  const [spaces, setSpaces] = useState<Space[]>([]),
    [workspace, setWorkspaceValue] = useState("");
  function setWorkspace(value: string) {
    if (canLeaveHomework()) setWorkspaceValue(value);
  }
  const [record, setRecord] = useState(""),
    [cycle, setCycle] = useState(""),
    [day, setDay] = useState("");
  const [mode, setMode] = useState("day"),
    [version, setVersion] = useState(0),
    [loading, setLoading] = useState(true);
  const [result, setResult] = useState<Schedule | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api("portal/options", "school")
      .then((r: Space[]) => {
        if (live) {
          setSpaces(r);
          setWorkspace(r[0]?.id || "");
        }
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
  }, []);
  const space = spaces.find((x) => x.id === workspace);
  const messageUnread = useMessageUnread(workspace);
  const role = session.user!.role;
  const { options: journal, failed } = useJournalOptions(
    workspace,
    ["teacher", "student", "parent"].includes(role),
  );
  const journalPanels = journal ? panelsOf(journal) : failed ? [] : null;
  // A pupil's or parent's portal looks at one child: the one chosen in the account menu while the account still has them.
  const withChild = role === "parent" || role === "student";
  const children = withChild ? (journal?.students ?? []) : [];
  const [chosenChild, setChosenChild] = useState<string | undefined>(),
    [wantedChild, setWantedChild] = useState("");
  const remembered = useMemo(() => recallChoice(workspace), [workspace]);
  const child = withChild
    ? currentChild(children, chosenChild ?? remembered)
    : "";
  function chooseChild(id: string) {
    if (id === child || !children.some((c) => c.id === id)) return;
    if (!canLeaveHomework()) return;
    setChosenChild(id);
    rememberChoice(workspace, id);
  }
  useEffect(() => setChosenChild(undefined), [workspace]);
  // An opened notification about another child switches the portal to that child, once the account's children are known.
  useEffect(() => {
    if (!wantedChild || !journal) return;
    if (children.some((c) => c.id === wantedChild)) {
      setChosenChild(wantedChild);
      rememberChoice(workspace, wantedChild);
    }
    setWantedChild("");
  }, [wantedChild, journal]);
  useEffect(() => {
    if (space) {
      const r = space.records[0];
      setRecord(r ? JSON.stringify([r.view, r.id]) : "");
      setCycle(space.cycles[0]?.id || "");
      const today = new Date().toLocaleDateString("sv-SE");
      setDay(
        today >= space.yearStart && today <= space.yearEnd
          ? today
          : space.yearStart,
      );
    }
  }, [workspace, spaces]);
  // The timetable is the child's own while the portal looks at a child who has one.
  const childRecord = child
    ? space?.records.find((r) => r.view === "student" && r.id === child)
    : undefined;
  useEffect(() => {
    if (childRecord)
      setRecord(JSON.stringify([childRecord.view, childRecord.id]));
  }, [childRecord?.id, space]);
  useEffect(() => {
    if (!workspace || !record || (mode === "day" ? !day : !cycle)) return;
    let live = true;
    setLoading(true);
    setError("");
    setResult(null);
    const [view, entityId] = JSON.parse(record);
    const params = new URLSearchParams({
      view,
      entityId,
      ...(mode === "day" ? { day } : { cycleId: cycle }),
    });
    api(`portal/schedule?${params}`, workspace)
      .then((r) => {
        if (live) setResult(r);
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
  }, [workspace, record, cycle, day, mode, version]);
  useEffect(() => {
    if (!wantedRecord || !space) return;
    if (
      space.records.some((r) => JSON.stringify([r.view, r.id]) === wantedRecord)
    )
      setRecord(wantedRecord);
    setWantedRecord("");
  }, [wantedRecord, space]);
  // Opened from a notification after the server re-checked access to the record.
  useEffect(() => {
    const sections = eventViews("portal", session.user?.role ?? null);
    const open = (event: Event) => {
      const detail = (event as CustomEvent).detail || {};
      if (!canLeaveHomework()) return;
      if (detail.workspace) setWorkspaceValue(detail.workspace);
      if (detail.studentId) setWantedChild(detail.studentId);
      setSectionValue(sections[event.type]);
      setJournalView(eventPart(session.user?.role ?? null, event.type, detail));
      setMessageFocus(
        focusRequest(
          event.type === "atlas-open-messages" ? detail.threadId : undefined,
        ),
      );
      setJournalFocus(
        event.type === "atlas-open-absences"
          ? { panel: "absences" }
          : event.type === "atlas-open-journal" &&
              detail.studentId &&
              (detail.day || detail.finalsPeriodId)
            ? {
                studentId: detail.studentId,
                day: detail.day,
                lessonKey: detail.lessonKey,
                finalsPeriodId: detail.finalsPeriodId,
              }
            : undefined,
      );
      if (event.type === "atlas-open-schedule" && detail.studentId)
        setWantedRecord(JSON.stringify(["student", detail.studentId]));
    };
    for (const name of Object.keys(sections))
      window.addEventListener(name, open);
    return () => {
      for (const name of Object.keys(sections))
        window.removeEventListener(name, open);
    };
  }, [session.user?.role]);
  function csv() {
    if (!result) return;
    const rows = [
      [
        "День",
        "Начало",
        "Конец",
        "Предмет / событие",
        "Группа",
        "Учителя",
        "Кабинеты",
        "Статус",
      ],
      ...result.rows.map((r) => [
        r.date || days[r.day],
        r.start,
        r.end,
        r.subject,
        r.group,
        r.teachers,
        r.rooms,
        statusLabel(r),
      ]),
    ];
    download(
      "Моё расписание.csv",
      "\uFEFF" + rows.map((r) => r.map(csvCell).join(";")).join("\r\n"),
      "text/csv;charset=utf-8",
    );
  }
  return (
    <Shell
      frame={frame}
      journalPanels={journalPanels}
      view={section}
      journalView={journalView}
      onView={setSection}
      school={space?.name}
      year={space && schoolYear(space.yearStart, space.yearEnd)}
      unread={messageUnread}
      family={
        role === "parent" && children.length
          ? { children, current: child, onChoose: chooseChild }
          : undefined
      }
    >
      {({ title }) => (
        <main className="portal">
          <div className="page-heading">
            <h1>{title}</h1>
          </div>
          {section === "home" ? (
            <>
              <label className="field">
                <span>Пространство</span>
                <select
                  value={workspace}
                  onChange={(e) => setWorkspace(e.target.value)}
                >
                  {spaces.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              {workspace ? (
                <>
                  <TodayLessons
                    key={workspace}
                    workspace={workspace}
                    space={space}
                    named={["parent", "viewer"].includes(session.user!.role)}
                    child={child}
                  />
                  <section className="start-feed">
                    <h2>Уведомления</h2>
                    <MessageFeed key={workspace} workspace={workspace} />
                  </section>
                </>
              ) : (
                !loading && (
                  <p className="info-line">
                    Для вашей учётной записи пока нет рабочих пространств.
                    Объявления появятся после настройки доступа.
                  </p>
                )
              )}
            </>
          ) : section === "class-staff" ? (
            <ClassStaffWorkspace />
          ) : section === "family" ? (
            <FamilyPortal />
          ) : section === "duties" ? (
            <>
              <label className="field">
                <span>Рабочее пространство</span>
                <select
                  value={workspace}
                  onChange={(e) => setWorkspace(e.target.value)}
                >
                  {spaces.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              {workspace && <MyDuties key={workspace} workspace={workspace} />}
            </>
          ) : section === "messages" ? (
            <>
              <label className="field">
                <span>Пространство сообщений</span>
                <select
                  value={workspace}
                  onChange={(e) => setWorkspace(e.target.value)}
                >
                  {spaces.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              {workspace && (
                <MessagesPanel
                  key={workspace}
                  workspace={workspace}
                  child={child}
                  focusThread={messageFocus}
                  onFocusUsed={(r) =>
                    setMessageFocus((current) => spendFocus(current, r))
                  }
                />
              )}
            </>
          ) : section === "courses" ? (
            <>
              <label className="field">
                <span>Рабочее пространство</span>
                <select
                  value={workspace}
                  onChange={(e) => setWorkspace(e.target.value)}
                >
                  {spaces.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              {workspace && (
                <IntakePortal
                  key={workspace}
                  workspace={workspace}
                  child={child}
                />
              )}
            </>
          ) : section === "journal" ||
            section === "materials" ||
            section === "curriculum" ||
            section === "homework" ||
            section === "quizzes" ? (
            <>
              <label className="field workspace-field">
                <span>Рабочее пространство</span>
                <select
                  value={workspace}
                  onChange={(e) => setWorkspace(e.target.value)}
                >
                  {spaces.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              {workspace &&
                (section === "curriculum" ? (
                  <CurriculumPanel key={workspace} workspace={workspace} />
                ) : section === "materials" ? (
                  <TeachingMaterialsPanel
                    key={workspace}
                    workspace={workspace}
                  />
                ) : section === "quizzes" ? (
                  <QuizzesPanel key={workspace} workspace={workspace} />
                ) : section === "homework" ? (
                  <HomeworkQueuePanel key={workspace} workspace={workspace} />
                ) : (
                  <JournalPanel
                    key={workspace}
                    workspace={workspace}
                    child={child}
                    focus={journalFocus}
                    view={journalView}
                    onLeaveView={() => setJournalView(undefined)}
                  />
                ))}
            </>
          ) : (
            <>
              <p className="info-line">
                Здесь показана текущая рабочая версия расписания. Доступны
                только записи, назначенные администратором. Опубликованные
                снимки по отдельным ссылкам могут относиться к другой версии.
              </p>
              {error && (
                <p role="alert" className="error-banner">
                  {error}
                </p>
              )}
              <div className="dated-fields">
                <label className="field">
                  <span>Рабочее пространство</span>
                  <select
                    value={workspace}
                    onChange={(e) => setWorkspace(e.target.value)}
                  >
                    {spaces.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                {childRecord ? (
                  <p className="info-line">
                    Расписание: <strong>{childRecord.name}</strong>
                  </p>
                ) : (
                  <label className="field">
                    <span>Разрешённое расписание</span>
                    <select
                      value={record}
                      onChange={(e) => setRecord(e.target.value)}
                    >
                      {space?.records.map((r) => (
                        <option
                          key={r.view + ":" + r.id}
                          value={JSON.stringify([r.view, r.id])}
                        >
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="field">
                  <span>Режим просмотра</span>
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    <option value="day">На дату</option>
                    <option value="week">Неделя</option>
                  </select>
                </label>
                {mode === "day" ? (
                  <label className="field">
                    <span>Дата расписания</span>
                    <input
                      type="date"
                      min={space?.yearStart}
                      max={space?.yearEnd}
                      value={day}
                      onChange={(e) => setDay(e.target.value)}
                    />
                  </label>
                ) : (
                  <label className="field">
                    <span>Учебная неделя</span>
                    <select
                      value={cycle}
                      onChange={(e) => setCycle(e.target.value)}
                    >
                      {space?.cycles.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
              <div className="dated-toolbar">
                <button
                  disabled={loading}
                  onClick={() => setVersion((n) => n + 1)}
                >
                  Обновить расписание
                </button>
                <button disabled={!result} onClick={csv}>
                  Скачать CSV
                </button>
                <button disabled={!result} onClick={() => window.print()}>
                  Печать
                </button>
              </div>
              {loading && <p role="status">Загружаю расписание…</p>}
              {!loading && !space?.records.length && (
                <p className="empty-state">
                  Доступных записей нет. Обратитесь к администратору.
                </p>
              )}
              {result && (
                <section>
                  <h2>{result.name}</h2>
                  {result.classTeacher && (
                    <p className="muted">
                      Классный руководитель: {result.classTeacher}
                    </p>
                  )}
                  <p className="muted">
                    {result.school} · версия {result.revision}
                  </p>
                  {!result.rows.length && (
                    <p className="empty-state">Занятий не найдено.</p>
                  )}
                  {days.map((name, index) => {
                    const rows = result.rows.filter((r) => r.day === index);
                    return (
                      !!rows.length && (
                        <section className="portal-day" key={name}>
                          <h3>
                            {name}
                            {mode === "day" ? " · " + day : ""}
                          </h3>
                          {rows.map((r, i) => (
                            <article
                              className={`dated-lesson ${r.status === "cancelled" ? "cancelled" : ""}`}
                              key={i}
                            >
                              <strong>
                                {r.start}–{r.end}
                              </strong>
                              <span>{statusLabel(r)}</span>
                              <h4>
                                {r.subject} · {r.group}
                              </h4>
                              <p>
                                {r.teachers || "Учитель не назначен"}
                                {r.rooms ? " · " + r.rooms : ""}
                              </p>
                            </article>
                          ))}
                        </section>
                      )
                    );
                  })}
                </section>
              )}
            </>
          )}
        </main>
      )}
    </Shell>
  );
}

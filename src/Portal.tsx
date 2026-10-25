import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { roleNames, type AuthStatus, type AccessView } from "./auth";
import { csvCell, download } from "./utils";
import JournalPanel from "./JournalPanel";
import HomeworkQueuePanel from "./HomeworkQueuePanel";
import QuizzesPanel from "./QuizzesLoader";
import TeachingMaterialsPanel from "./TeachingMaterialsLoader";
import CurriculumPanel from "./CurriculumLoader";
import { canLeaveHomework } from "./homeworkNavigation";
import MessagesPanel from "./MessagesPanel";
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
}
interface Schedule {
  revision: number;
  name: string;
  school: string;
  rows: Row[];
}
const days = [
  "Понедельник",
  "Вторник",
  "Среда",
  "Четверг",
  "Пятница",
  "Суббота",
  "Воскресенье",
];
export default function Portal({
  session,
  controls,
}: {
  session: AuthStatus;
  controls: ReactNode;
}) {
  const [section, setSectionValue] = useState("schedule");
  function setSection(value: string) {
    if (canLeaveHomework()) setSectionValue(value);
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
        r.status,
      ]),
    ];
    download(
      "Моё расписание.csv",
      "\uFEFF" + rows.map((r) => r.map(csvCell).join(";")).join("\r\n"),
      "text/csv;charset=utf-8",
    );
  }
  return (
    <main className="portal">
      <header>
        <div>
          <strong>атлас•</strong>
          <h1>
            {section === "class-staff"
              ? "Работа с классами"
              : section === "duties"
                ? "Мои дежурства"
                : section === "curriculum"
                  ? "Учебные планы"
                  : section === "materials"
                    ? "Учебные материалы"
                    : section === "family"
                      ? "Мои дети и доступ"
                      : section === "messages"
                        ? "Сообщения"
                        : section === "quizzes"
                          ? "Контрольные и тесты"
                          : section === "homework"
                            ? "Домашние задания"
                            : section === "journal"
                              ? "Учебный журнал"
                              : section === "courses"
                                ? "Выбор курсов"
                                : "Моё расписание"}
          </h1>
          <p>{roleNames[session.user!.role]}</p>
        </div>
        {controls}
      </header>
      {
        <div className="dated-toolbar portal-tabs">
          {session.user!.role === "teacher" && (
            <button
              aria-pressed={section === "class-staff"}
              onClick={() => setSection("class-staff")}
            >
              Работа с классами
            </button>
          )}
          {session.user!.role === "parent" && (
            <button
              aria-pressed={section === "family"}
              onClick={() => setSection("family")}
            >
              Мои дети и доступ
            </button>
          )}
          <button
            aria-pressed={section === "schedule"}
            onClick={() => setSection("schedule")}
          >
            Расписание
          </button>
          {session.user!.role === "teacher" && (
            <button
              aria-pressed={section === "duties"}
              onClick={() => setSection("duties")}
            >
              Мои дежурства
            </button>
          )}
          <button
            aria-pressed={section === "messages"}
            onClick={() => setSection("messages")}
          >
            Сообщения
          </button>
          {session.user!.role === "teacher" && (
            <button
              aria-pressed={section === "curriculum"}
              onClick={() => setSection("curriculum")}
            >
              Учебные планы
            </button>
          )}
          {session.user!.role === "teacher" && (
            <button
              aria-pressed={section === "materials"}
              onClick={() => setSection("materials")}
            >
              Учебные материалы
            </button>
          )}
          {["teacher", "student", "parent"].includes(session.user!.role) && (
            <button
              aria-pressed={section === "quizzes"}
              onClick={() => setSection("quizzes")}
            >
              Контрольные и тесты
            </button>
          )}
          {["teacher", "student", "parent"].includes(session.user!.role) && (
            <button
              aria-pressed={section === "homework"}
              onClick={() => setSection("homework")}
            >
              Домашние задания
            </button>
          )}
          {["teacher", "student", "parent"].includes(session.user!.role) && (
            <button
              aria-pressed={section === "journal"}
              onClick={() => setSection("journal")}
            >
              Учебный журнал
            </button>
          )}
          {["student", "parent"].includes(session.user!.role) && (
            <button
              aria-pressed={section === "courses"}
              onClick={() => setSection("courses")}
            >
              Выбор курсов
            </button>
          )}
        </div>
      }
      {section === "class-staff" ? (
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
          {workspace && <MessagesPanel key={workspace} workspace={workspace} />}
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
          {workspace && <IntakePortal key={workspace} workspace={workspace} />}
        </>
      ) : section === "journal" ||
        section === "materials" ||
        section === "curriculum" ||
        section === "homework" ||
        section === "quizzes" ? (
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
          {workspace &&
            (section === "curriculum" ? (
              <CurriculumPanel key={workspace} workspace={workspace} />
            ) : section === "materials" ? (
              <TeachingMaterialsPanel key={workspace} workspace={workspace} />
            ) : section === "quizzes" ? (
              <QuizzesPanel key={workspace} workspace={workspace} />
            ) : section === "homework" ? (
              <HomeworkQueuePanel key={workspace} workspace={workspace} />
            ) : (
              <JournalPanel key={workspace} workspace={workspace} />
            ))}
        </>
      ) : (
        <>
          <p className="info-line">
            Здесь показана текущая рабочая версия расписания. Доступны только
            записи, назначенные администратором. Опубликованные снимки по
            отдельным ссылкам могут относиться к другой версии.
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
            <label className="field">
              <span>Режим просмотра</span>
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
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
            <button disabled={loading} onClick={() => setVersion((n) => n + 1)}>
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
                          <span>
                            {r.status === "cancelled"
                              ? "Отменено"
                              : r.activity === "duty"
                                ? "Дежурство"
                                : r.activity === "booking"
                                  ? "Бронирование"
                                  : r.status === "changed"
                                    ? "Изменено"
                                    : r.status === "added"
                                      ? "Разовый урок"
                                      : "По расписанию"}
                          </span>
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
  );
}

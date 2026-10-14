import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { attendanceNames } from "./journal";
import { download } from "./utils";

type Counts = {
  records: number;
  attendance: Record<string, number>;
  lateMinutes: number;
};
type Student = Counts & {
  id: string;
  name: string;
  classId: string | null;
  className: string;
  active: boolean | null;
};
type Filters = {
  start: string;
  end: string;
  classId: string;
  studentId: string;
  subjectId: string;
  historicalOnly: boolean;
};
type Options = {
  available: boolean;
  admin: boolean;
  yearStart: string;
  yearEnd: string;
  classes: { id: string; name: string }[];
  students: { id: string; name: string; classId: string; active: boolean }[];
  subjects: { id: string; name: string }[];
};
type Report = {
  token: string;
  generatedAt: string;
  totals: Counts;
  studentCount: number;
  withoutRecords: number;
  classes: (Counts & {
    id: string | null;
    name: string;
    students: number;
    withoutRecords: number;
  })[];
  subjects: (Counts & { id: string; name: string })[];
  students: Student[];
  nextOffset: number | null;
};
type Lesson = {
  lessonKey: string;
  date: string;
  start: string;
  end: string;
  subject: string;
  group: string;
  teachers: string;
  attendance: string;
  rawAttendance: string;
  lateMinutes: number;
  excusedByNotice: boolean;
  version: number;
  publishedAt: string;
};
type Detail = {
  student: { id: string; name: string };
  items: Lesson[];
  nextOffset: number | null;
};

function Totals({ value }: { value: Counts }) {
  return (
    <div className="attendance-counts">
      <span>
        Записей: <strong>{value.records}</strong>
      </span>
      {Object.entries(attendanceNames).map(([id, label]) => (
        <span key={id}>
          {label}: <strong>{value.attendance[id]}</strong>
        </span>
      ))}
      <span>
        Опоздания: <strong>{value.lateMinutes} мин</strong>
      </span>
    </div>
  );
}

export default function AttendancePanel({ workspace }: { workspace: string }) {
  const [expanded, setExpanded] = useState(false),
    [options, setOptions] = useState<Options | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [classChoice, setClassChoice] = useState("all"),
    [student, setStudent] = useState(""),
    [subject, setSubject] = useState("");
  const [report, setReport] = useState<Report | null>(null),
    [applied, setApplied] = useState<Filters | null>(null);
  const [view, setView] = useState("students"),
    [detail, setDetail] = useState<Detail | null>(null);
  const [detailStudent, setDetailStudent] = useState<Student | null>(null),
    [attendance, setAttendance] = useState("all");
  const detailPanel = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (detail) {
      detailPanel.current?.focus({ preventScroll: true });
      detailPanel.current?.scrollIntoView({ block: "start" });
    }
  }, [detail?.student.id]);
  const [knownSubjects, setKnownSubjects] = useState<
    { id: string; name: string }[]
  >([]);
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
  const invalidate = () => {
    setReport(null);
    setApplied(null);
    setDetail(null);
    setDetailStudent(null);
    setError("");
  };
  const filters = (): Filters => ({
    start,
    end,
    classId: classChoice.startsWith("class:") ? classChoice.slice(6) : "",
    historicalOnly: classChoice === "historical",
    studentId: student,
    subjectId: subject,
  });
  useEffect(() => {
    if (!expanded) return;
    let live = true;
    setBusy(true);
    setError("");
    api("journal/attendance/options", workspace)
      .then((value: Options) => {
        if (!live) return;
        setOptions(value);
        setStart((old) => old || value.yearStart);
        setEnd((old) => old || value.yearEnd);
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
  }, [expanded, workspace]);
  const load = async (offset?: number) => {
    const query = offset && applied ? applied : filters();
    const result: Report = await api(
      "journal/attendance/report",
      workspace,
      "POST",
      {
        ...query,
        offset: offset || 0,
        token: offset ? report?.token || "" : "",
      },
    );
    setKnownSubjects((old) => [
      ...new Map(
        [...old, ...result.subjects].map((s) => [
          s.id,
          { id: s.id, name: s.name },
        ]),
      ).values(),
    ]);
    setReport((old) =>
      offset && old
        ? { ...result, students: [...old.students, ...result.students] }
        : result,
    );
    setApplied(query);
    if (!offset) {
      setDetail(null);
      setDetailStudent(null);
    }
  };
  const open = async (pupil: Student, status = "all", offset = 0) => {
    if (!applied || !report) return;
    if (!offset) {
      setDetail(null);
      setDetailStudent(pupil);
      setAttendance(status);
    }
    const value: Detail = await api(
      "journal/attendance/details",
      workspace,
      "POST",
      {
        ...applied,
        studentId: pupil.id,
        attendance: status,
        offset,
        token: report.token,
      },
    );
    setDetail((old) =>
      offset && old
        ? { ...value, items: [...old.items, ...value.items] }
        : value,
    );
  };
  const csv = async (kind: string) => {
    if (!applied || !report) return;
    const value = {
      ...applied,
      view: kind,
      token: report.token,
      ...(kind === "lessons"
        ? { studentId: detailStudent?.id, attendance }
        : {}),
    };
    const response = await fetch(
      `/api/journal/attendance/export?workspace=${encodeURIComponent(workspace)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Atlas-Request": "1" },
        body: JSON.stringify(value),
      },
    );
    if (!response.ok) {
      if (response.status === 401)
        window.dispatchEvent(new Event("atlas-auth-required"));
      const failure = await response.json();
      throw new Error(
        typeof failure.detail === "string"
          ? failure.detail
          : "Не удалось выгрузить посещаемость.",
      );
    }
    download(
      `Посещаемость-${applied.start}-${applied.end}-${kind}.csv`,
      "\ufeff" + (await response.text()).replace(/^\ufeff/, ""),
      "text/csv;charset=utf-8",
    );
  };
  const currentClass = classChoice.startsWith("class:")
    ? classChoice.slice(6)
    : "";
  const subjects = [
    ...new Map(
      [...(options?.subjects || []), ...knownSubjects].map((s) => [s.id, s]),
    ).values(),
  ];
  return (
    <details
      className="absence-panel attendance-panel"
      open={expanded}
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Сводная посещаемость</summary>
      {expanded && (
        <>
          <p>
            Опубликованные отметки за выбранный период, до 366 дней. Классы
            показаны по текущему составу, включая неактивных учеников.
            Неотмечено — опубликованные записи без отметки; уроки без
            опубликованного журнала в эти числа не входят. Процент посещаемости
            по ним не рассчитывается.
          </p>
          {error && (
            <p role="alert" className="error-banner">
              {error}
            </p>
          )}
          {busy && <p role="status">Загружаю посещаемость…</p>}
          {options && !options.available && (
            <p>
              Для сводки нужно назначение ответственным за класс. Администратор
              задаёт его в разделе объяснительных.
            </p>
          )}
          {options?.available && (
            <>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(() => load());
                }}
              >
                <fieldset disabled={busy} className="access-fields">
                  <div className="dated-toolbar">
                    <label className="field">
                      <span>Начало сводки</span>
                      <input
                        type="date"
                        value={start}
                        required
                        onChange={(e) => {
                          setStart(e.target.value);
                          invalidate();
                        }}
                      />
                    </label>
                    <label className="field">
                      <span>Конец сводки</span>
                      <input
                        type="date"
                        value={end}
                        required
                        onChange={(e) => {
                          setEnd(e.target.value);
                          invalidate();
                        }}
                      />
                    </label>
                  </div>
                  <label className="field">
                    <span>Класс сводки</span>
                    <select
                      value={classChoice}
                      onChange={(e) => {
                        setClassChoice(e.target.value);
                        setStudent("");
                        invalidate();
                      }}
                    >
                      <option value="all">
                        {options.admin ? "Вся школа" : "Мои классы"}
                      </option>
                      {options.classes.map((c) => (
                        <option key={c.id} value={`class:${c.id}`}>
                          {c.name}
                        </option>
                      ))}
                      {options.admin && (
                        <option value="historical">Вне текущего состава</option>
                      )}
                    </select>
                  </label>
                  <label className="field">
                    <span>Ученик сводки</span>
                    <select
                      value={student}
                      onChange={(e) => {
                        setStudent(e.target.value);
                        invalidate();
                      }}
                    >
                      <option value="">Все выбранные ученики</option>
                      {classChoice !== "historical" &&
                        options.students
                          .filter(
                            (s) => !currentClass || s.classId === currentClass,
                          )
                          .map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.name}
                              {s.active ? "" : " · неактивен"}
                            </option>
                          ))}
                    </select>
                  </label>
                  <label className="field">
                    <span>Предмет сводки</span>
                    <select
                      value={subject}
                      onChange={(e) => {
                        setSubject(e.target.value);
                        invalidate();
                      }}
                    >
                      <option value="">Все предметы</option>
                      {subjects.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button type="submit">Показать сводку</button>
                </fieldset>
              </form>
              {report && applied && (
                <section aria-label="Результат сводки посещаемости">
                  <h3>
                    Посещаемость: {applied.start} — {applied.end}
                  </h3>
                  <p>
                    Учеников: {report.studentCount}. Без опубликованных записей:{" "}
                    {report.withoutRecords}. Сформировано:{" "}
                    {new Date(report.generatedAt).toLocaleString("ru-RU")}.
                  </p>
                  <Totals value={report.totals} />
                  <div className="dated-toolbar">
                    {[
                      ["students", "По ученикам"],
                      ["classes", "По классам"],
                      ["subjects", "По предметам"],
                    ].map(([id, name]) => (
                      <button
                        key={id}
                        disabled={busy}
                        aria-pressed={view === id}
                        onClick={() => setView(id)}
                      >
                        {name}
                      </button>
                    ))}
                    <button
                      disabled={busy}
                      onClick={() => void run(() => csv(view))}
                    >
                      Скачать сводку CSV
                    </button>
                  </div>
                  <p>
                    CSV включает все строки выбранной сводки, включая следующие
                    страницы. Оценки, комментарии и частные причины отсутствия в
                    отчёт не входят.
                  </p>
                  <div className="attendance-cards">
                    {view === "students" &&
                      report.students.map((s) => (
                        <article className="absence-card" key={s.id}>
                          <h4>{s.name}</h4>
                          <p>
                            {s.className}
                            {s.active === false ? " · неактивен" : ""}
                          </p>
                          {s.records ? (
                            <Totals value={s} />
                          ) : (
                            <p>
                              Нет опубликованных записей за этот период. Это не
                              отметка о присутствии.
                            </p>
                          )}
                          <button
                            disabled={busy || !s.records}
                            onClick={() => void run(() => open(s))}
                          >
                            Уроки ученика
                          </button>
                        </article>
                      ))}
                    {view === "classes" &&
                      report.classes.map((c) => (
                        <article
                          className="absence-card"
                          key={c.id || "archive"}
                        >
                          <h4>{c.name}</h4>
                          <p>
                            Учеников: {c.students}. Без записей:{" "}
                            {c.withoutRecords}.
                          </p>
                          <Totals value={c} />
                        </article>
                      ))}
                    {view === "subjects" &&
                      report.subjects.map((s) => (
                        <article className="absence-card" key={s.id}>
                          <h4>{s.name}</h4>
                          <Totals value={s} />
                        </article>
                      ))}
                  </div>
                  {view === "students" && report.nextOffset !== null && (
                    <button
                      disabled={busy}
                      onClick={() => void run(() => load(report.nextOffset!))}
                    >
                      Следующие ученики
                    </button>
                  )}
                  {detailStudent && (
                    <section
                      className="absence-card"
                      aria-label="Уроки выбранного ученика"
                      tabIndex={-1}
                      ref={detailPanel}
                    >
                      <h3>Уроки: {detailStudent.name}</h3>
                      <label className="field">
                        <span>Отметки в списке уроков</span>
                        <select
                          disabled={busy}
                          value={attendance}
                          onChange={(e) =>
                            void run(() => open(detailStudent, e.target.value))
                          }
                        >
                          <option value="all">Все отметки</option>
                          {Object.entries(attendanceNames).map(([id, name]) => (
                            <option key={id} value={id}>
                              {name}
                            </option>
                          ))}
                        </select>
                      </label>
                      {detail && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() => void run(() => csv("lessons"))}
                          >
                            Скачать уроки ученика CSV
                          </button>
                          {!detail.items.length && (
                            <p>Опубликованных записей с такой отметкой нет.</p>
                          )}
                          {detail.items.map((l) => (
                            <article className="absence-card" key={l.lessonKey}>
                              <h4>
                                {l.date} · {l.start}–{l.end} · {l.subject}
                              </h4>
                              <p>
                                {l.group} · {l.teachers}
                              </p>
                              <p>
                                <strong>{attendanceNames[l.attendance]}</strong>
                                {l.lateMinutes > 0 && ` · ${l.lateMinutes} мин`}
                              </p>
                              {l.excusedByNotice && (
                                <p>
                                  Объяснительная принята школой. Исходная
                                  отметка учителя:{" "}
                                  {attendanceNames[l.rawAttendance]}.
                                </p>
                              )}
                              <p>
                                Опубликованная редакция {l.version} ·{" "}
                                {new Date(l.publishedAt).toLocaleString(
                                  "ru-RU",
                                )}
                              </p>
                            </article>
                          ))}
                          {detail.nextOffset !== null && (
                            <button
                              disabled={busy}
                              onClick={() =>
                                void run(() =>
                                  open(
                                    detailStudent,
                                    attendance,
                                    detail.nextOffset!,
                                  ),
                                )
                              }
                            >
                              Следующие уроки
                            </button>
                          )}
                        </>
                      )}
                    </section>
                  )}
                </section>
              )}
            </>
          )}
        </>
      )}
    </details>
  );
}

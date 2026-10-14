import { useEffect, useState } from "react";
import type { Course, CourseRequest, State, ScheduleIssue } from "./types";
import { api } from "./api";
import { uid, named, rosterComplete, csvCell, download } from "./utils";
import ChangePreview from "./ChangePreview";
import type { Preview } from "./ChangePreview";
import { IntakeAdmin } from "./IntakePanel";

interface Allocation {
  status: string;
  message?: string;
  preview?: Preview;
  assigned: number;
  unassigned: number;
  firstChoices: number;
  changedStudents: number;
  unplacedHours: number;
  optimal: boolean;
  issues?: ScheduleIssue[];
  reasons?: Record<string, string[]>;
  rows?: {
    requestId: string;
    studentId: string;
    courseId: string | null;
    groupId: string | null;
    rank: number | null;
    previousGroupIds: string[];
    reasons: string[];
  }[];
  sections?: {
    groupId: string;
    courseId: string;
    count: number;
    minSize: number;
    maxSize: number;
  }[];
}

function CourseForm({
  state: s,
  value,
  onChange,
  onSave,
  onCancel,
  busy,
}: {
  state: State;
  value: Course;
  onChange: (c: Course) => void;
  onSave: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const taken = new Set(
    (s.courses || [])
      .filter((c) => c.id !== value.id)
      .flatMap((c) => c.sections.map((x) => x.groupId)),
  );
  const eligible = s.groups.filter(
    (g) => !["whole", "staff"].includes(g.kind) && !taken.has(g.id),
  );
  return (
    <form
      className="course-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
    >
      <h3>
        {s.courses?.some((c) => c.id === value.id)
          ? "Изменить курс"
          : "Новый курс"}
      </h3>
      <label className="field">
        <span>Название курса</span>
        <input
          required
          maxLength={160}
          value={value.name}
          onChange={(e) => onChange({ ...value, name: e.target.value })}
        />
      </label>
      <p className="muted">
        Секции — существующие группы одного курса. Ученику назначается одна
        секция; он посещает все её уроки. Классы для приёма задаются в составе
        группы.
      </p>
      {value.sections.map((section, i) => (
        <fieldset key={i}>
          <legend>Секция {i + 1}</legend>
          <div className="course-section-fields">
            <label className="field">
              <span>Учебная группа</span>
              <select
                required
                value={section.groupId}
                onChange={(e) =>
                  onChange({
                    ...value,
                    sections: value.sections.map((x, n) =>
                      n === i ? { ...x, groupId: e.target.value } : x,
                    ),
                  })
                }
              >
                <option value="">Выберите группу</option>
                {eligible.map((g) => (
                  <option
                    key={g.id}
                    value={g.id}
                    disabled={value.sections.some(
                      (x, n) => n !== i && x.groupId === g.id,
                    )}
                  >
                    {g.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Минимум учеников</span>
              <input
                type="number"
                required
                min={0}
                max={5000}
                value={section.minSize}
                onChange={(e) =>
                  onChange({
                    ...value,
                    sections: value.sections.map((x, n) =>
                      n === i ? { ...x, minSize: Number(e.target.value) } : x,
                    ),
                  })
                }
              />
            </label>
            <label className="field">
              <span>Максимум учеников</span>
              <input
                type="number"
                required
                min={Math.max(1, section.minSize)}
                max={5000}
                value={section.maxSize}
                onChange={(e) =>
                  onChange({
                    ...value,
                    sections: value.sections.map((x, n) =>
                      n === i ? { ...x, maxSize: Number(e.target.value) } : x,
                    ),
                  })
                }
              />
            </label>
          </div>
          <button
            type="button"
            disabled={value.sections.length === 1 || busy}
            onClick={() =>
              onChange({
                ...value,
                sections: value.sections.filter((_, n) => n !== i),
              })
            }
          >
            Убрать секцию из курса
          </button>
        </fieldset>
      ))}
      <p className="muted">
        Минимум применяется к каждой секции, даже если она пока пустая. Укажите
        0, если заполнение не обязательно. Архивные ученики не занимают места.
      </p>
      <button
        type="button"
        disabled={busy || value.sections.length >= eligible.length}
        onClick={() =>
          onChange({
            ...value,
            sections: [
              ...value.sections,
              { groupId: "", minSize: 0, maxSize: 25 },
            ],
          })
        }
      >
        Добавить секцию
      </button>
      {!eligible.length && (
        <p role="status">
          Сначала создайте учебную группу в разделе «Классы и группы».
        </p>
      )}
      <div className="modal-actions">
        <button type="button" disabled={busy} onClick={onCancel}>
          Отмена
        </button>
        <button className="primary" disabled={busy}>
          Проверить курс
        </button>
      </div>
    </form>
  );
}

function RequestForm({
  state: s,
  value,
  onChange,
  onSave,
  onCancel,
  busy,
}: {
  state: State;
  value: CourseRequest;
  onChange: (r: CourseRequest) => void;
  onSave: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [search, setSearch] = useState("");
  const courses = s.courses || [];
  const student = s.students.find((x) => x.id === value.studentId);
  const options = s.students.filter(
    (x) =>
      x.id === value.studentId ||
      (x.active !== false &&
        `${x.name} ${named(s.classes, x.classId)}`
          .toLocaleLowerCase()
          .includes(search.toLocaleLowerCase())),
  );
  const sections = courses
    .filter((c) => value.courseIds.includes(c.id))
    .flatMap((c) => c.sections)
    .filter((x) =>
      s.groups
        .find((g) => g.id === x.groupId)
        ?.classIds.includes(student?.classId || ""),
    );
  function move(i: number, delta: number) {
    const ids = [...value.courseIds];
    [ids[i], ids[i + delta]] = [ids[i + delta], ids[i]];
    onChange({ ...value, courseIds: ids });
  }
  return (
    <form
      className="course-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
    >
      <h3>
        {s.courseRequests?.some((r) => r.id === value.id)
          ? "Изменить заявку"
          : "Новая заявка"}
      </h3>
      <label className="field">
        <span>Поиск ученика для заявки</span>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Имя или класс"
        />
      </label>
      <label className="field">
        <span>Ученик</span>
        <select
          required
          value={value.studentId}
          onChange={(e) =>
            onChange({
              ...value,
              studentId: e.target.value,
              lockedGroupId: null,
            })
          }
        >
          <option value="">Выберите ученика</option>
          {options.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name} · {named(s.classes, x.classId)}
              {x.active === false ? " · архив" : ""}
            </option>
          ))}
        </select>
      </label>
      <p>
        Одна заявка — один курс. Добавьте альтернативы в порядке предпочтения.
        Для второго независимого курса создайте ещё одну заявку.
      </p>
      {value.courseIds.map((id, i) => (
        <div className="course-preference" key={i}>
          <label className="field">
            <span>Приоритет {i + 1}</span>
            <select
              required
              value={id}
              onChange={(e) =>
                onChange({
                  ...value,
                  courseIds: value.courseIds.map((x, n) =>
                    n === i ? e.target.value : x,
                  ),
                  lockedGroupId: null,
                })
              }
            >
              <option value="">Выберите курс</option>
              {courses.map((c) => (
                <option
                  key={c.id}
                  value={c.id}
                  disabled={value.courseIds.some(
                    (x, n) => n !== i && x === c.id,
                  )}
                >
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            aria-label={`Поднять приоритет ${i + 1}`}
            disabled={i === 0 || busy}
            onClick={() => move(i, -1)}
          >
            ↑
          </button>
          <button
            type="button"
            aria-label={`Опустить приоритет ${i + 1}`}
            disabled={i === value.courseIds.length - 1 || busy}
            onClick={() => move(i, 1)}
          >
            ↓
          </button>
          <button
            type="button"
            disabled={value.courseIds.length === 1 || busy}
            onClick={() =>
              onChange({
                ...value,
                courseIds: value.courseIds.filter((_, n) => n !== i),
                lockedGroupId: null,
              })
            }
          >
            Убрать
          </button>
        </div>
      ))}
      <button
        type="button"
        disabled={
          busy || value.courseIds.length >= Math.min(20, courses.length)
        }
        onClick={() =>
          onChange({ ...value, courseIds: [...value.courseIds, ""] })
        }
      >
        Добавить альтернативу
      </button>
      <label className="check-line">
        <input
          type="checkbox"
          checked={value.required}
          onChange={(e) => onChange({ ...value, required: e.target.checked })}
        />
        Обязательно назначить один из курсов
      </label>
      <label className="field">
        <span>Закрепить секцию</span>
        <select
          value={value.lockedGroupId || ""}
          onChange={(e) =>
            onChange({ ...value, lockedGroupId: e.target.value || null })
          }
        >
          <option value="">Автоматический выбор</option>
          {sections.map((x) => (
            <option key={x.groupId} value={x.groupId}>
              {named(s.groups, x.groupId)}
            </option>
          ))}
        </select>
        <small>Закрепление обязательно даже для необязательной заявки.</small>
      </label>
      <div className="modal-actions">
        <button type="button" disabled={busy} onClick={onCancel}>
          Отмена
        </button>
        <button className="primary" disabled={busy}>
          Проверить заявку
        </button>
      </div>
    </form>
  );
}

export default function CoursesPanel({
  state: s,
  workspace,
  onState,
  onGroup,
}: {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
  onGroup: (id: string) => void;
}) {
  const courses = s.courses || [],
    requests = s.courseRequests || [];
  const [tab, setTab] = useState<"catalog" | "requests" | "intake">("catalog");
  const [course, setCourse] = useState<Course | null>(null),
    [request, setRequest] = useState<CourseRequest | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null),
    [result, setResult] = useState<Allocation | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [query, setQuery] = useState(""),
    [classId, setClassId] = useState("");
  const [seconds, setSeconds] = useState(10),
    [limit, setLimit] = useState(60);
  useEffect(() => {
    setCourse(null);
    setRequest(null);
    setPreview(null);
    setResult(null);
    setError("");
  }, [s.revision, workspace]);
  useEffect(() => setLimit(60), [query, classId, tab]);
  const filtered = requests.filter((r) => {
    const student = s.students.find((x) => x.id === r.studentId);
    return (
      student &&
      (!classId || student.classId === classId) &&
      `${student.name} ${r.courseIds.map((id) => named(courses, id)).join(" ")}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase())
    );
  });
  const allocatable = filtered.filter(
    (r) => s.students.find((x) => x.id === r.studentId)?.active !== false,
  );
  const count = (id: string) =>
    s.groups
      .find((g) => g.id === id)
      ?.studentIds.filter(
        (id) => s.students.find((x) => x.id === id)?.active !== false,
      ).length || 0;
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function stage(body: Record<string, unknown>) {
    void run(async () => {
      const p = await api("editor/preview", workspace, "POST", {
        revision: s.revision,
        ...body,
      });
      setPreview(p);
      setResult(null);
      setCourse(null);
      setRequest(null);
    });
  }
  function clear() {
    setPreview(null);
    setResult(null);
    setCourse(null);
    setRequest(null);
    setError("");
  }
  function exportRows() {
    const rows = [
      [
        "Ученик",
        "Класс",
        "Курсы по приоритету",
        "Обязательная",
        "Закреплённая секция",
      ],
      ...filtered.map((r) => [
        named(s.students, r.studentId),
        named(s.classes, s.students.find((x) => x.id === r.studentId)?.classId),
        r.courseIds.map((id) => named(courses, id)).join(" → "),
        r.required ? "Да" : "Нет",
        named(s.groups, r.lockedGroupId),
      ]),
    ];
    download(
      "course-requests.csv",
      "\uFEFF" + rows.map((row) => row.map(csvCell).join(";")).join("\r\n"),
      "text/csv;charset=utf-8",
    );
  }
  return (
    <div className="courses-panel">
      <div className="planner-tabs">
        <button
          className={tab === "catalog" ? "active" : ""}
          disabled={busy}
          onClick={() => {
            setTab("catalog");
            clear();
          }}
        >
          Курсы и секции ({courses.length})
        </button>
        <button
          className={tab === "requests" ? "active" : ""}
          disabled={busy}
          onClick={() => {
            setTab("requests");
            clear();
          }}
        >
          Заявки учеников ({requests.length})
        </button>
        <button
          className={tab === "intake" ? "active" : ""}
          disabled={busy}
          onClick={() => {
            setTab("intake");
            clear();
          }}
        >
          Приём из личных кабинетов
        </button>
      </div>
      {tab === "intake" ? (
        <IntakeAdmin state={s} workspace={workspace} onState={onState} />
      ) : (
        <>
          <p className="muted">
            Составы едины для всех недель и учебных периодов. Распределение
            проверяет уроки во всех шаблонах; оно не перемещает занятия.
          </p>
          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {course && !preview && (
            <CourseForm
              state={s}
              value={course}
              onChange={setCourse}
              onSave={() => stage({ action: "course", course })}
              onCancel={clear}
              busy={busy}
            />
          )}
          {request && !preview && (
            <RequestForm
              state={s}
              value={request}
              onChange={setRequest}
              onSave={() =>
                stage({ action: "courseRequest", courseRequest: request })
              }
              onCancel={clear}
              busy={busy}
            />
          )}
          {result && (
            <section className="allocation-result" aria-live="polite">
              <h2>Результат распределения</h2>
              {result.status === "success" ? (
                <>
                  <p>
                    Назначено: <strong>{result.assigned}</strong> · без
                    назначения: <strong>{result.unassigned}</strong> · первый
                    выбор: <strong>{result.firstChoices}</strong>. Изменяются
                    составы для {result.changedStudents} учеников.
                  </p>
                  <p className="muted">
                    {result.optimal
                      ? "Найден лучший результат по числу назначений и приоритетам в пределах заданных условий."
                      : "Найден допустимый результат; лучший вариант за лимит не доказан."}{" "}
                    При равных приоритетах сохраняется прежняя секция.
                  </p>
                  {!!result.unplacedHours && (
                    <p className="info-line">
                      У секций ещё не размещено {result.unplacedHours} уроков во
                      всех шаблонах недель. Их будущие пересечения пока нельзя
                      проверить; после распределения составьте расписание.
                    </p>
                  )}
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Ученик</th>
                          <th>Курс</th>
                          <th>Секция</th>
                          <th>Выбор</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.rows?.slice(0, limit).map((row) => (
                          <tr key={row.requestId}>
                            <td>{named(s.students, row.studentId)}</td>
                            <td>{named(courses, row.courseId)}</td>
                            <td>
                              {row.groupId ? (
                                named(s.groups, row.groupId)
                              ) : (
                                <>
                                  <strong>Без назначения</strong>
                                  <small>
                                    {row.reasons.length
                                      ? row.reasons.join("; ")
                                      : "Места и ограничения распределения; можно изменить заявку или лимиты секций."}
                                  </small>
                                </>
                              )}
                            </td>
                            <td>{row.rank || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {!!result.rows && result.rows.length > limit && (
                    <button onClick={() => setLimit(limit + 100)}>
                      Ещё результаты
                    </button>
                  )}
                  <details>
                    <summary>Численность секций после применения</summary>
                    {result.sections?.map((x) => (
                      <p key={x.groupId}>
                        {named(s.groups, x.groupId)}: {x.count}, допустимо{" "}
                        {x.minSize}–{x.maxSize}
                      </p>
                    ))}
                  </details>
                </>
              ) : (
                <>
                  <p role="alert">{result.message}</p>
                  {result.issues?.slice(0, 15).map((x, i) => (
                    <p key={i}>{x.message}</p>
                  ))}
                  <details>
                    <summary>Недоступные варианты заявок</summary>
                    {Object.entries(result.reasons || {})
                      .filter(([, values]) => values.length)
                      .slice(0, 60)
                      .map(([id, values]) => (
                        <p key={id}>
                          {named(
                            s.students,
                            requests.find((r) => r.id === id)?.studentId,
                          )}
                          : {values.join("; ")}
                        </p>
                      ))}
                  </details>
                </>
              )}
            </section>
          )}
          {preview && (
            <ChangePreview
              preview={preview}
              busy={busy}
              onCancel={clear}
              onApply={() =>
                void run(async () => {
                  await onState(
                    await api("editor/apply", workspace, "POST", {
                      revision: preview.revision,
                      token: preview.token,
                    }),
                  );
                })
              }
              title={
                result
                  ? "Составы до и после распределения"
                  : "Изменения курсов и заявок"
              }
            />
          )}
          {!course && !request && !preview && tab === "catalog" && (
            <>
              <button
                className="primary"
                disabled={busy}
                onClick={() => {
                  clear();
                  setCourse({
                    id: uid(),
                    name: "",
                    sections: [{ groupId: "", minSize: 0, maxSize: 25 }],
                  });
                }}
              >
                Добавить курс
              </button>
              {!courses.length && (
                <section className="empty">
                  <h3>Курсы пока не настроены</h3>
                  <p>
                    Объедините существующие core, math или elective-группы в
                    секции одного курса. Затем внесите заявки учеников.
                  </p>
                </section>
              )}
              <div className="course-cards">
                {courses.map((c) => (
                  <article key={c.id} className="course-card">
                    <header>
                      <h2>{c.name}</h2>
                      <button
                        disabled={busy}
                        onClick={() => {
                          clear();
                          setCourse(structuredClone(c));
                        }}
                      >
                        Изменить курс
                      </button>
                    </header>
                    {c.sections.map((x) => (
                      <div className="course-section-summary" key={x.groupId}>
                        <button
                          className="link-button"
                          onClick={() => onGroup(x.groupId)}
                        >
                          {named(s.groups, x.groupId)}
                        </button>
                        <span
                          className={
                            count(x.groupId) > x.maxSize ||
                            count(x.groupId) < x.minSize
                              ? "warning-text"
                              : ""
                          }
                        >
                          {count(x.groupId)} учеников · предел {x.minSize}–
                          {x.maxSize}
                        </span>
                        {!rosterComplete(
                          s.groups.find((g) => g.id === x.groupId)!,
                        ) && (
                          <span className="warning-text">
                            Состав не подтверждён
                          </span>
                        )}
                      </div>
                    ))}
                    <p className="muted">
                      Заявок с этим курсом:{" "}
                      {
                        requests.filter((r) => r.courseIds.includes(c.id))
                          .length
                      }
                    </p>
                    <button
                      disabled={
                        busy || requests.some((r) => r.courseIds.includes(c.id))
                      }
                      onClick={() =>
                        stage({ action: "removeCourse", ids: [c.id] })
                      }
                    >
                      Удалить курс
                    </button>
                    {requests.some((r) => r.courseIds.includes(c.id)) && (
                      <small>
                        Для удаления сначала измените связанные заявки. Группы и
                        занятия сохраняются.
                      </small>
                    )}
                  </article>
                ))}
              </div>
            </>
          )}
          {!course && !request && !preview && tab === "requests" && (
            <>
              <div className="planner-filters">
                <label className="field">
                  <span>Поиск заявок</span>
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Ученик или курс"
                  />
                </label>
                <label className="field">
                  <span>Класс заявок</span>
                  <select
                    value={classId}
                    onChange={(e) => setClassId(e.target.value)}
                  >
                    <option value="">Все классы</option>
                    {s.classes.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  disabled={busy || !courses.length}
                  onClick={() => {
                    clear();
                    setRequest({
                      id: uid(),
                      studentId: "",
                      courseIds: [courses[0]?.id || ""],
                      required: true,
                      lockedGroupId: null,
                    });
                  }}
                >
                  Добавить заявку
                </button>
                <button disabled={!filtered.length} onClick={exportRows}>
                  CSV заявок
                </button>
              </div>
              <section className="allocation-controls">
                <h3>Распределить найденные заявки ({allocatable.length})</h3>
                <p>
                  Сохраняются ученики без выбранных заявок и назначения по
                  курсам вне их альтернатив. Положительный минимум обязателен
                  для каждой секции выбранных курсов. Архивные ученики исключены
                  из поиска.
                </p>
                <label className="field">
                  <span>Лимит распределения, секунд</span>
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={seconds}
                    onChange={(e) =>
                      setSeconds(
                        Math.min(60, Math.max(1, Number(e.target.value) || 1)),
                      )
                    }
                  />
                </label>
                <button
                  className="primary"
                  disabled={busy || !allocatable.length}
                  onClick={() =>
                    void run(async () => {
                      clear();
                      const r: Allocation = await api(
                        "courses/allocate",
                        workspace,
                        "POST",
                        {
                          revision: s.revision,
                          requestIds: allocatable.map((x) => x.id),
                          seconds,
                        },
                      );
                      setResult(r);
                      setPreview(r.preview || null);
                    })
                  }
                >
                  {busy ? "Распределяем и проверяем…" : "Подобрать секции"}
                </button>
              </section>
              {!filtered.length ? (
                <p>
                  Заявки не найдены. Добавьте выбор ученика или измените
                  фильтры.
                </p>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Ученик</th>
                        <th>Курсы по приоритету</th>
                        <th>Условие</th>
                        <th>Действия</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.slice(0, limit).map((r) => (
                        <tr key={r.id}>
                          <td>
                            {named(s.students, r.studentId)}
                            <small>
                              {named(
                                s.classes,
                                s.students.find((x) => x.id === r.studentId)
                                  ?.classId,
                              )}
                              {s.students.find((x) => x.id === r.studentId)
                                ?.active === false
                                ? " · архив"
                                : ""}
                            </small>
                          </td>
                          <td>
                            {r.courseIds
                              .map((id) => named(courses, id))
                              .join(" → ")}
                          </td>
                          <td>
                            {r.required ? "Обязательно" : "По возможности"}
                            {r.lockedGroupId && (
                              <small>
                                Закреплено: {named(s.groups, r.lockedGroupId)}
                              </small>
                            )}
                          </td>
                          <td>
                            <button
                              disabled={busy}
                              onClick={() => {
                                clear();
                                setRequest(structuredClone(r));
                              }}
                            >
                              Изменить заявку
                            </button>
                            <button
                              disabled={busy}
                              onClick={() =>
                                stage({
                                  action: "removeCourseRequest",
                                  ids: [r.id],
                                })
                              }
                            >
                              Удалить заявку
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {filtered.length > limit && (
                <button onClick={() => setLimit(limit + 100)}>
                  Ещё заявки ({filtered.length - limit})
                </button>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

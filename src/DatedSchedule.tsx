import { useEffect, useState } from "react";
import { api } from "./api";
import { uid, named, csvCell, download } from "./utils";
import type {
  State,
  Named,
  DatedLesson,
  TeacherAbsence,
  CalendarEvent,
  ScheduleIssue,
} from "./types";
import ChangePreview, { type Preview } from "./ChangePreview";

interface DatedRow {
  activity?: "lesson" | "booking" | "duty";
  commitmentId?: string;
  id: string;
  date: string;
  placementId: string | null;
  sourceDate: string | null;
  changeId: string | null;
  groupId: string;
  profileId: string | null;
  group: string;
  subjectId: string;
  subject: string;
  classIds: string[];
  studentIds: string[];
  originalClassIds: string[];
  originalStudentIds: string[];
  excludedClassIds: string[];
  teacherIds: string[];
  originalTeacherIds: string[];
  roomIds: string[];
  teachers: string;
  rooms: string;
  start: string;
  end: string;
  status: "scheduled" | "cancelled" | "added" | "changed";
  reasons: string[];
}
interface DayReport {
  unfilledDuties?: number;
  warnings: ScheduleIssue[];
  warningCount: number;
  qualityPenalty: number;
  revision: number;
  date: string;
  cycleId: string | null;
  rows: DatedRow[];
  events: CalendarEvent[];
  issues: ScheduleIssue[];
  issueCount: number;
  completeChecks: boolean;
  unknownGroups: number;
  unknownCapacity: number;
  missingTeachers: number;
  missingRooms: number;
}
interface Candidate {
  id: string;
  name: string;
  available: boolean;
  reasons: string[];
  lessons: number;
}
const STATUS = {
  scheduled: "По расписанию",
  changed: "Изменение",
  added: "Разовый урок",
  cancelled: "Отменён",
};
function DateField({
  label,
  value,
  onChange,
  min,
  max,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  min: string;
  max: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="date"
        required
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
export function Multi({
  label,
  items,
  values,
  onChange,
}: {
  label: string;
  items: Named[];
  values: string[];
  onChange: (v: string[]) => void;
}) {
  const [search, setSearch] = useState("");
  return (
    <fieldset className="dated-multi">
      <legend>{label}</legend>
      <input
        aria-label={`Поиск: ${label}`}
        placeholder="Поиск"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div>
        {items
          .filter(
            (x) =>
              !search ||
              x.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
          )
          .map((x) => (
            <label className="check-line" key={x.id}>
              <input
                type="checkbox"
                checked={values.includes(x.id)}
                onChange={(e) =>
                  onChange(
                    e.target.checked
                      ? [...values, x.id]
                      : values.filter((v) => v !== x.id),
                  )
                }
              />
              {x.name}
            </label>
          ))}
      </div>
      <small>
        Выбрано: {values.length}.{" "}
        {values.map((id) => named(items, id)).join(", ")}
      </small>
    </fieldset>
  );
}

export default function DatedSchedule({
  state: s,
  workspace,
  onState,
  initialDate,
  onCommitment,
}: {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
  initialDate?: string;
  onCommitment?: (id: string) => void;
}) {
  const today = new Date().toLocaleDateString("sv-SE");
  const [day, setDay] = useState(
    initialDate ||
      (today < s.settings.yearStart || today > s.settings.yearEnd
        ? s.settings.yearStart
        : today),
  );
  const [tab, setTab] = useState<"day" | "absences" | "changes">("day");
  const [mode, setMode] = useState<
    "class" | "teacher" | "room" | "student" | "all"
  >(initialDate ? "all" : "class");
  const [selected, setSelected] = useState(s.classes[0]?.id || ""),
    [query, setQuery] = useState("");
  const [report, setReport] = useState<DayReport | null>(null),
    [loading, setLoading] = useState(false);
  const [lesson, setLesson] = useState<DatedLesson | null>(null),
    [absence, setAbsence] = useState<TeacherAbsence | null>(null);
  const [selectedRow, setSelectedRow] = useState<DatedRow | null>(null),
    [replaceTeacher, setReplaceTeacher] = useState("");
  const [candidates, setCandidates] = useState<Candidate[] | null>(null),
    [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [history, setHistory] = useState({ undo: 0, redo: 0 });
  useEffect(() => {
    let live = true;
    api("history", workspace)
      .then((r) => {
        if (live) setHistory(r);
      })
      .catch(() => {
        if (live) setHistory({ undo: 0, redo: 0 });
      });
    return () => {
      live = false;
    };
  }, [workspace, s.revision]);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setReport(null);
    api(`calendar/day?day=${encodeURIComponent(day)}`, workspace)
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
  }, [day, workspace, s.revision]);
  useEffect(() => {
    setPreview(null);
    setLesson(null);
    setAbsence(null);
    setCandidates(null);
    setSelectedRow(null);
  }, [workspace, s.revision]);
  const fresh = report?.revision === s.revision;
  const entities =
    mode === "class"
      ? s.classes
      : mode === "teacher"
        ? s.teachers
        : mode === "room"
          ? s.rooms
          : mode === "student"
            ? s.students.filter((x) => x.active !== false)
            : [];
  const shown = (fresh ? report.rows : []).filter((r) => {
    if (
      query &&
      !`${r.group} ${r.subject} ${r.teachers} ${r.rooms}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase())
    )
      return false;
    if (!selected || mode === "all") return true;
    return mode === "class"
      ? r.originalClassIds.includes(selected)
      : mode === "student"
        ? r.originalStudentIds.includes(selected)
        : mode === "teacher"
          ? r.teacherIds.includes(selected) ||
            r.originalTeacherIds.includes(selected)
          : r.roomIds.includes(selected);
  });
  function excluded(r: DatedRow) {
    return mode === "class"
      ? r.excludedClassIds.includes(selected)
      : mode === "student"
        ? r.originalStudentIds.includes(selected) &&
          !r.studentIds.includes(selected)
        : false;
  }
  function statusLabel(r: DatedRow) {
    return r.activity === "booking"
      ? "Бронирование"
      : r.activity === "duty"
        ? "Дежурство"
        : STATUS[excluded(r) ? "cancelled" : r.status];
  }
  function reset() {
    setLesson(null);
    setAbsence(null);
    setPreview(null);
    setCandidates(null);
    setSelectedRow(null);
    setError("");
  }
  function editRow(r: DatedRow, kind: "change" | "cancel") {
    reset();
    const existing = s.datedLessons?.find((c) => c.id === r.changeId);
    if (kind === "cancel" && existing?.kind === "add") {
      void check("datedLessons", undefined, existing.id);
      return;
    }
    setLesson(
      kind === "cancel"
        ? {
            id: existing?.id || uid(),
            name: "Отмена урока",
            kind,
            onDate: r.sourceDate!,
            sourceDate: r.sourceDate,
            placementId: r.placementId,
            groupId: null,
            subjectId: null,
            startTime: null,
            endTime: null,
            teacherIds: null,
            roomIds: null,
          }
        : {
            id: existing?.id || uid(),
            name: existing?.name || "Замена / перенос",
            kind: existing?.kind === "add" ? "add" : "change",
            onDate: r.date,
            sourceDate: r.sourceDate,
            placementId: r.placementId,
            groupId: existing?.kind === "add" ? r.groupId : null,
            profileId: existing?.kind === "add" ? r.profileId : null,
            subjectId: r.subjectId,
            startTime: r.start,
            endTime: r.end,
            teacherIds: r.teacherIds,
            roomIds: r.roomIds,
          },
    );
    setSelectedRow(r);
    setReplaceTeacher(r.teacherIds[0] || "");
  }
  function newLesson() {
    reset();
    setLesson({
      id: uid(),
      name: "Дополнительный урок",
      kind: "add",
      onDate: day,
      sourceDate: null,
      placementId: null,
      groupId: "",
      profileId: s.profiles[0]?.id || "",
      subjectId: "",
      startTime: "09:00",
      endTime: "09:40",
      teacherIds: [],
      roomIds: [],
    });
  }
  function newAbsence() {
    reset();
    setAbsence({
      id: uid(),
      name: "Отсутствие",
      teacherId: "",
      start: day,
      end: day,
      startTime: "00:00",
      endTime: "23:59",
    });
  }
  async function check(
    table: "datedLessons" | "absences",
    value?: DatedLesson | TeacherAbsence,
    removeId?: string,
  ) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setPreview(
        await api("calendar/preview", workspace, "POST", {
          revision: s.revision,
          table,
          value: value || null,
          removeId: removeId || null,
        }),
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!preview || busy) return;
    setBusy(true);
    setError("");
    try {
      const saved = await api("editor/apply", workspace, "POST", {
        revision: preview.revision,
        token: preview.token,
      });
      await onState(saved);
      reset();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function historyAction(action: "undo" | "redo") {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await onState(
        await api(action, workspace, "POST", { revision: s.revision }),
      );
      reset();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function findSubstitutes() {
    if (!selectedRow || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api("calendar/substitutes", workspace, "POST", {
        revision: s.revision,
        day: selectedRow.date,
        rowId: selectedRow.id,
        teacherId: replaceTeacher || null,
      });
      setCandidates(r.candidates);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function openChange(c: DatedLesson) {
    if (busy) return;
    reset();
    setBusy(true);
    try {
      const data: DayReport = await api(
        `calendar/day?day=${c.onDate}`,
        workspace,
      );
      const row = data.rows.find((r) => r.changeId === c.id);
      if (c.kind === "cancel") {
        setLesson(c);
        setSelectedRow(row || null);
      } else if (row) editRow(row, "change");
      else
        setError(
          "Исходный урок больше не найден. Уберите устаревшее изменение и создайте новое для нужного урока.",
        );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const dateBounds = { min: s.settings.yearStart, max: s.settings.yearEnd };
  return (
    <div className="dated-panel">
      <p className="info-line">
        Здесь — фактические занятия на дату. Замены, переносы и отмены
        сохраняются отдельно от повторяющейся недели. Влияние каникул включается
        явно в «Учебном календаре».
      </p>
      <div
        className="exchange-tabs"
        role="group"
        aria-label="Раздел расписания по датам"
      >
        {(
          [
            ["day", "Занятия на дату"],
            ["absences", "Отсутствия учителей"],
            ["changes", "Все изменения"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "selected" : ""}
            onClick={() => {
              reset();
              setTab(id);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="dated-toolbar">
        <DateField
          label="Дата расписания"
          value={day}
          onChange={(v) => {
            if (v) {
              setDay(v);
              reset();
            }
          }}
          {...dateBounds}
        />
        <button disabled={busy} onClick={newLesson}>
          Добавить разовый урок
        </button>
        <button disabled={busy} onClick={newAbsence}>
          Указать отсутствие
        </button>
        <button
          disabled={busy || !history.undo}
          onClick={() => historyAction("undo")}
        >
          Отменить действие
        </button>
        <button
          disabled={busy || !history.redo}
          onClick={() => historyAction("redo")}
        >
          Повторить действие
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {preview ? (
        <ChangePreview
          preview={preview}
          busy={busy}
          onApply={apply}
          onCancel={() => setPreview(null)}
          title="Проверка изменения по датам"
        />
      ) : (
        <>
          {lesson && (
            <form
              className="dated-form"
              onSubmit={(e) => {
                e.preventDefault();
                void check("datedLessons", lesson);
              }}
            >
              <h3>
                {lesson.kind === "cancel"
                  ? "Отмена урока"
                  : lesson.kind === "add"
                    ? "Разовое занятие"
                    : "Замена или перенос"}
              </h3>
              {selectedRow && (
                <p>
                  {selectedRow.group} · {selectedRow.subject} ·{" "}
                  {selectedRow.sourceDate || selectedRow.date} ·{" "}
                  {selectedRow.start}–{selectedRow.end}
                </p>
              )}
              <label className="field">
                <span>Причина / примечание</span>
                <input
                  required
                  maxLength={160}
                  value={lesson.name}
                  onChange={(e) =>
                    setLesson({ ...lesson, name: e.target.value })
                  }
                />
              </label>
              {lesson.kind !== "cancel" && (
                <>
                  <div className="dated-fields">
                    <DateField
                      label="Дата занятия"
                      value={lesson.onDate}
                      onChange={(v) => setLesson({ ...lesson, onDate: v })}
                      {...dateBounds}
                    />
                    <label className="field">
                      <span>Начало урока</span>
                      <input
                        type="time"
                        required
                        value={lesson.startTime || ""}
                        onChange={(e) =>
                          setLesson({ ...lesson, startTime: e.target.value })
                        }
                      />
                    </label>
                    <label className="field">
                      <span>Конец урока</span>
                      <input
                        type="time"
                        required
                        value={lesson.endTime || ""}
                        onChange={(e) =>
                          setLesson({ ...lesson, endTime: e.target.value })
                        }
                      />
                    </label>
                  </div>
                  {lesson.kind === "add" && (
                    <label className="field">
                      <span>Профиль для запретов по номерам уроков</span>
                      <select
                        required
                        value={lesson.profileId || ""}
                        onChange={(e) =>
                          setLesson({ ...lesson, profileId: e.target.value })
                        }
                      >
                        {s.profiles.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                      </select>
                      <small>
                        Время разового урока задаётся выше; профиль связывает
                        его с запретами учителей по номерам периодов.
                      </small>
                    </label>
                  )}
                  {lesson.kind === "add" && (
                    <label className="field">
                      <span>Группа занятия</span>
                      <select
                        required
                        value={lesson.groupId || ""}
                        onChange={(e) =>
                          setLesson({ ...lesson, groupId: e.target.value })
                        }
                      >
                        <option value="">Выберите группу</option>
                        {s.groups.map((g) => (
                          <option key={g.id} value={g.id}>
                            {g.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <label className="field">
                    <span>Предмет занятия</span>
                    <select
                      required
                      value={lesson.subjectId || ""}
                      onChange={(e) =>
                        setLesson({ ...lesson, subjectId: e.target.value })
                      }
                    >
                      <option value="">Выберите предмет</option>
                      {s.subjects.map((x) => (
                        <option key={x.id} value={x.id}>
                          {x.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {selectedRow && (
                    <details className="dated-substitutes">
                      <summary>Подобрать заменяющего учителя</summary>
                      <p>
                        Подбор по сохранённому времени урока, занятости и
                        отсутствиям. Квалификация не задана; выбор предмета и
                        преподавателя подтверждает сотрудник. После ручной
                        правки времени или состава выполните общую проверку
                        формы.
                      </p>
                      <label className="field">
                        <span>Кого заменить</span>
                        <select
                          value={replaceTeacher}
                          onChange={(e) => {
                            setReplaceTeacher(e.target.value);
                            setCandidates(null);
                          }}
                        >
                          <option value="">Добавить преподавателя</option>
                          {selectedRow.teacherIds.map((id) => (
                            <option key={id} value={id}>
                              {named(s.teachers, id)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={findSubstitutes}
                      >
                        Найти свободных учителей
                      </button>
                      {candidates && (
                        <div className="dated-candidates">
                          {candidates.map((c) => (
                            <div key={c.id}>
                              <span>
                                <strong>{c.name}</strong> · уроков в этот день:{" "}
                                {c.lessons}
                                <small>
                                  {c.available
                                    ? "Нет обнаруженных препятствий"
                                    : c.reasons.join("; ")}
                                </small>
                              </span>
                              <button
                                type="button"
                                disabled={!c.available || busy}
                                onClick={() =>
                                  setLesson({
                                    ...lesson,
                                    teacherIds: Array.from(
                                      new Set([
                                        ...(lesson.teacherIds || []).filter(
                                          (t) => t !== replaceTeacher,
                                        ),
                                        c.id,
                                      ]),
                                    ),
                                  })
                                }
                              >
                                Выбрать
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </details>
                  )}
                  <div className="dated-fields">
                    <Multi
                      label="Учителя занятия"
                      items={s.teachers}
                      values={lesson.teacherIds || []}
                      onChange={(v) => setLesson({ ...lesson, teacherIds: v })}
                    />
                    <Multi
                      label="Все кабинеты занятия"
                      items={s.rooms}
                      values={lesson.roomIds || []}
                      onChange={(v) => setLesson({ ...lesson, roomIds: v })}
                    />
                  </div>
                  <p className="muted">
                    Наборы учителей и кабинетов заменяют назначения только этого
                    урока на дату. Пустой набор означает незаполненное
                    назначение, если исходный урок требует ресурс. Перенос
                    сохраняет исходное время, пока вы его не измените.
                  </p>
                </>
              )}
              <div className="modal-actions">
                <button type="button" disabled={busy} onClick={reset}>
                  Закрыть форму
                </button>
                <button className="primary" disabled={busy}>
                  Проверить изменение
                </button>
              </div>
            </form>
          )}
          {absence && (
            <form
              className="dated-form"
              onSubmit={(e) => {
                e.preventDefault();
                void check("absences", absence);
              }}
            >
              <h3>Отсутствие учителя</h3>
              <label className="field">
                <span>Учитель</span>
                <select
                  required
                  value={absence.teacherId}
                  onChange={(e) =>
                    setAbsence({ ...absence, teacherId: e.target.value })
                  }
                >
                  <option value="">Выберите учителя</option>
                  {s.teachers.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Причина отсутствия</span>
                <input
                  required
                  maxLength={160}
                  value={absence.name}
                  onChange={(e) =>
                    setAbsence({ ...absence, name: e.target.value })
                  }
                />
              </label>
              <div className="dated-fields">
                <DateField
                  label="Первый день отсутствия"
                  value={absence.start}
                  onChange={(v) => setAbsence({ ...absence, start: v })}
                  {...dateBounds}
                />
                <DateField
                  label="Последний день отсутствия"
                  value={absence.end}
                  onChange={(v) => setAbsence({ ...absence, end: v })}
                  {...dateBounds}
                />
              </div>
              <div className="dated-fields">
                <label className="field">
                  <span>Ежедневно с</span>
                  <input
                    type="time"
                    required
                    value={absence.startTime}
                    onChange={(e) =>
                      setAbsence({ ...absence, startTime: e.target.value })
                    }
                  />
                </label>
                <label className="field">
                  <span>Ежедневно до</span>
                  <input
                    type="time"
                    required
                    value={absence.endTime}
                    onChange={(e) =>
                      setAbsence({ ...absence, endTime: e.target.value })
                    }
                  />
                </label>
              </div>
              <p className="info-line">
                Отсутствие можно сохранить с незакрытыми уроками: они появятся в
                проверке даты как требующие замены. Запись сама не отменяет
                занятия.
              </p>
              <div className="modal-actions">
                <button type="button" disabled={busy} onClick={reset}>
                  Закрыть форму
                </button>
                <button className="primary" disabled={busy}>
                  Проверить отсутствие
                </button>
              </div>
            </form>
          )}
        </>
      )}
      {tab === "day" && (
        <>
          <div className="dated-fields">
            <label className="field">
              <span>Показать расписание</span>
              <select
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value as typeof mode);
                  setSelected("");
                }}
              >
                <option value="class">Класса</option>
                <option value="teacher">Учителя</option>
                <option value="room">Кабинета</option>
                <option value="student">Ученика</option>
                <option value="all">Всей школы</option>
              </select>
            </label>
            {mode !== "all" && (
              <label className="field">
                <span>Выбранная запись</span>
                <select
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  <option value="">Все записи</option>
                  {entities.map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name}
                      {mode === "student"
                        ? ` · ${named(s.classes, (x as State["students"][number]).classId)}`
                        : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="field">
              <span>Поиск занятия</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Группа, предмет, учитель…"
              />
            </label>
          </div>
          {loading ? (
            <p role="status">Загружаю занятия…</p>
          ) : !fresh ? (
            <p>Данные изменились в другом окне. Обновите страницу.</p>
          ) : (
            <>
              <div className="dated-summary">
                <strong>
                  {day} ·{" "}
                  {report.cycleId
                    ? named(
                        s.cycles?.patterns || [
                          { id: "weekly", name: "Каждая неделя" },
                        ],
                        report.cycleId,
                      )
                    : "Вне учебных периодов"}
                </strong>
                <span>Показано занятий: {shown.length}</span>
                <button
                  disabled={!shown.length}
                  onClick={() =>
                    download(
                      `Расписание-${day}.csv`,
                      "\uFEFF" +
                        [
                          [
                            "Дата",
                            "Группа",
                            "Предмет",
                            "Начало",
                            "Конец",
                            "Учителя",
                            "Кабинеты",
                            "Статус",
                            "Примечание",
                          ],
                          ...shown.map((r) => [
                            r.date,
                            r.group,
                            r.subject,
                            r.start,
                            r.end,
                            r.teachers,
                            r.rooms,
                            statusLabel(r),
                            r.reasons.join("; "),
                          ]),
                        ]
                          .map((row) => row.map(csvCell).join(";"))
                          .join("\r\n"),
                      "text/csv;charset=utf-8",
                    )
                  }
                >
                  Скачать CSV
                </button>
              </div>
              {report.events.length > 0 && (
                <p className="info-line">
                  {report.events
                    .map(
                      (e) =>
                        `${e.name} · ${e.effect === "cancel" ? "отмена для " + (e.classIds?.length ? e.classIds.map((id) => named(s.classes, id)).join(", ") : "всех классов") : "информационная отметка"}`,
                    )
                    .join("; ")}
                </p>
              )}
              {!report.completeChecks && (
                <p className="muted">
                  Весь день: без учителя {report.missingTeachers}, без кабинета{" "}
                  {report.missingRooms}, неполных составов{" "}
                  {report.unknownGroups}, непроверенной вместимости{" "}
                  {report.unknownCapacity}, незаполненных мест дежурных{" "}
                  {report.unfilledDuties || 0}. Эти пробелы не считаются
                  успешной проверкой.
                </p>
              )}
              <details className="dated-issues" open={report.issueCount > 0}>
                <summary>
                  Проверка всей школы на дату: {report.issueCount} проблем
                </summary>
                <p>
                  Проверяются пересечения, отсутствие и недоступность ресурсов,
                  вместимость и переходы, дневные пределы, окна, обед и связи
                  предметов. Недельные нормы часов, распределение дней, сумма
                  окон за неделю и синхронные блоки остаются правилами
                  недельного шаблона.
                </p>
                <ul>
                  {report.issues.map((i, n) => (
                    <li key={n}>
                      {i.message}
                      {!!i.affectedStudentIds?.length && (
                        <small>
                          {i.affectedStudentIds
                            .map((id) => named(s.students, id))
                            .join(", ")}
                        </small>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
              {!!report.warningCount && (
                <details className="dated-issues">
                  <summary>
                    Пожелания: {report.warningCount} · штраф{" "}
                    {report.qualityPenalty}
                  </summary>
                  <ul>
                    {report.warnings.map((i, n) => (
                      <li key={n}>
                        {i.message}
                        {!!i.affectedStudentIds?.length && (
                          <small>
                            {i.affectedStudentIds
                              .map((id) => named(s.students, id))
                              .join(", ")}
                          </small>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {!shown.length && (
                <p className="empty-state">
                  На эту дату по выбранным условиям занятий нет.
                </p>
              )}
              <div className="dated-lessons">
                {shown.map((r) => (
                  <article
                    key={r.id}
                    className={`dated-lesson ${r.status === "cancelled" || excluded(r) ? "cancelled" : ""}`}
                  >
                    <div className="dated-time">
                      {r.start}–{r.end}
                      <small>{statusLabel(r)}</small>
                    </div>
                    <div>
                      <h3>
                        {r.subject} · {r.group}
                      </h3>
                      <p>
                        {r.teachers || "Учитель не назначен"} ·{" "}
                        {r.rooms || "Кабинет не назначен"}
                      </p>
                      {r.reasons.length > 0 && (
                        <small>{r.reasons.join("; ")}</small>
                      )}
                      {!!r.excludedClassIds.length && (
                        <small>
                          Не посещают:{" "}
                          {r.excludedClassIds
                            .map((id) => named(s.classes, id))
                            .join(", ")}
                        </small>
                      )}
                    </div>
                    <div className="dated-actions">
                      {r.commitmentId && (
                        <button onClick={() => onCommitment?.(r.commitmentId!)}>
                          Открыть запись события
                        </button>
                      )}
                      {r.status !== "cancelled" && !r.commitmentId && (
                        <>
                          <button
                            disabled={busy}
                            onClick={() => editRow(r, "change")}
                          >
                            Заменить / перенести
                          </button>
                          <button
                            disabled={busy}
                            onClick={() => editRow(r, "cancel")}
                          >
                            Отменить урок
                          </button>
                        </>
                      )}
                      {r.changeId && (
                        <button
                          disabled={busy}
                          onClick={() => {
                            reset();
                            void check("datedLessons", undefined, r.changeId!);
                          }}
                        >
                          Убрать изменение
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
        </>
      )}
      {tab === "absences" && (
        <div className="dated-records">
          {!(s.absences || []).length && <p>Отсутствия пока не внесены.</p>}
          {(s.absences || [])
            .slice()
            .sort((a, b) => b.start.localeCompare(a.start))
            .map((a) => (
              <article key={a.id}>
                <div>
                  <strong>
                    {named(s.teachers, a.teacherId)} · {a.name}
                  </strong>
                  <p>
                    {a.start} — {a.end} · каждый день {a.startTime}–{a.endTime}
                  </p>
                </div>
                <button
                  disabled={busy}
                  onClick={() => {
                    reset();
                    setAbsence(a);
                  }}
                >
                  Изменить отсутствие
                </button>
                <button
                  disabled={busy}
                  onClick={() => {
                    reset();
                    void check("absences", undefined, a.id);
                  }}
                >
                  Удалить отсутствие
                </button>
                <button
                  onClick={() => {
                    setDay(a.start);
                    setTab("day");
                    setMode("teacher");
                    setSelected(a.teacherId);
                    reset();
                  }}
                >
                  К урокам
                </button>
              </article>
            ))}
        </div>
      )}
      {tab === "changes" && (
        <div className="dated-records">
          {!(s.datedLessons || []).length && <p>Разовых изменений пока нет.</p>}
          {(s.datedLessons || [])
            .slice()
            .sort((a, b) => b.onDate.localeCompare(a.onDate))
            .map((c) => (
              <article key={c.id}>
                <div>
                  <strong>{c.name}</strong>
                  <p>
                    {c.onDate} ·{" "}
                    {c.kind === "cancel"
                      ? "Отмена"
                      : c.kind === "add"
                        ? "Разовый урок"
                        : "Замена / перенос"}
                    {c.sourceDate &&
                      c.sourceDate !== c.onDate &&
                      ` · исходная дата ${c.sourceDate}`}
                  </p>
                </div>
                <button
                  onClick={() => {
                    setDay(c.onDate);
                    setTab("day");
                    setMode("all");
                    reset();
                  }}
                >
                  Открыть дату
                </button>
                <button
                  disabled={busy}
                  onClick={() => {
                    reset();
                    void openChange(c);
                  }}
                >
                  Изменить запись
                </button>
                <button
                  disabled={busy}
                  onClick={() => {
                    reset();
                    void check("datedLessons", undefined, c.id);
                  }}
                >
                  Убрать изменение
                </button>
              </article>
            ))}
        </div>
      )}
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { uid, named, csvCell, download } from "./utils";
import type {
  State,
  DatedLesson,
  TeacherAbsence,
  ScheduleDay,
  ChangeType,
  CalendarEvent,
  ScheduleIssue,
} from "./types";
import ChangePreview, { type Preview } from "./ChangePreview";
import { DateField, Multi } from "./DatedFields";
import AbsenceFeed from "./AbsenceFeed";
import SubstitutionPlan from "./SubstitutionPlan";
import SubstitutionReport from "./SubstitutionReport";
import ScheduleDays from "./ScheduleDays";
import DatedChanges from "./DatedChanges";
import { absenceRows } from "./datedLists";
import {
  DAY_TABS,
  dateInWords,
  dayRowView,
  reasonText,
  nextDayTab,
  stateText,
  type DayTabId,
  type RowAction,
} from "./datedDay";
import "./DatedDay.css";
import "./DatedLists.css";
import {
  CHANGE_TYPES,
  effectiveType,
  factorLine,
  lessonProblem,
  type MergeOption,
  type RankedTeacher,
} from "./substitutions";

export { Multi };

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
  changeType?: ChangeType | null;
  mergeWith?: string | null;
  movedTo?: string | null;
}
interface DayReport {
  unfilledDuties?: number;
  warnings: ScheduleIssue[];
  warningCount: number;
  qualityPenalty: number;
  revision: number;
  date: string;
  cycleId: string | null;
  scheduleDay?: ScheduleDay | null;
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
  const [tab, setTab] = useState<DayTabId>("day");
  const [planTeacher, setPlanTeacher] = useState("");
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
  const [candidates, setCandidates] = useState<RankedTeacher[] | null>(null),
    [mergeOptions, setMergeOptions] = useState<MergeOption[]>([]),
    [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [history, setHistory] = useState({ undo: 0, redo: 0 });
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]),
    panel = useRef<HTMLDivElement>(null),
    opener = useRef<{ row: string; action: RowAction } | null>(null);
  // After a form or the preview closes, focus goes back to the button that opened it (or to its row) while that row is still listed.
  useEffect(() => {
    const o = opener.current;
    if (!o || tab !== "day" || busy || loading || lesson || absence || preview)
      return;
    if (report?.revision !== s.revision) return;
    opener.current = null;
    const own = Array.from(
      panel.current?.querySelectorAll<HTMLButtonElement>("[data-dd-row]") ?? [],
    ).filter((b) => b.dataset.ddRow === o.row);
    (own.find((b) => b.dataset.ddAction === o.action) ?? own[0])?.focus();
  });
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
    setMergeOptions([]);
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
    return stateText(r, excluded(r));
  }
  function switchTab(id: DayTabId) {
    opener.current = null;
    reset();
    setTab(id);
  }
  function keyTab(e: React.KeyboardEvent, index: number) {
    const to = nextDayTab(index, e.key);
    if (to === null) return;
    e.preventDefault();
    switchTab(DAY_TABS[to].id);
    tabButtons.current[to]?.focus();
  }
  function runRowAction(r: DatedRow, action: RowAction) {
    if (action === "commitment") {
      onCommitment?.(r.commitmentId!);
      return;
    }
    opener.current = { row: r.id, action };
    if (action === "remove") {
      reset();
      void check("datedLessons", undefined, r.changeId!);
    } else editRow(r, action);
  }
  function cycleName(rep: DayReport) {
    return rep.cycleId
      ? named(
          s.cycles?.patterns || [{ id: "weekly", name: "Каждая неделя" }],
          rep.cycleId,
        )
      : "Вне учебных периодов";
  }
  function downloadCsv() {
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
            "Тип изменения",
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
            r.changeType ? CHANGE_TYPES[r.changeType] : "",
            r.reasons.join("; "),
          ]),
        ]
          .map((row) => row.map(csvCell).join(";"))
          .join("\r\n"),
      "text/csv;charset=utf-8",
    );
  }
  function reset() {
    setLesson(null);
    setAbsence(null);
    setPreview(null);
    setCandidates(null);
    setMergeOptions([]);
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
            changeType: "cancel",
            mergeWith: null,
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
            changeType:
              existing?.kind === "add"
                ? "extra"
                : (existing?.changeType ??
                  (r.date !== r.sourceDate ? "move" : "substitution")),
            mergeWith: existing?.mergeWith ?? null,
          },
    );
    setSelectedRow(r);
    setReplaceTeacher(r.teacherIds[0] || "");
  }
  function newLesson() {
    opener.current = null;
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
      changeType: "extra",
      mergeWith: null,
    });
  }
  function newAbsence() {
    opener.current = null;
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
    table: "datedLessons" | "absences" | "scheduleDays",
    value?: DatedLesson | TeacherAbsence | ScheduleDay,
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
    opener.current = null;
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
      setMergeOptions(r.mergeOptions || []);
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
  function pickType(l: DatedLesson, type: ChangeType) {
    setLesson({
      ...l,
      changeType: type,
      mergeWith: type === "merge" ? (l.mergeWith ?? null) : null,
    });
  }
  function pickMerge(l: DatedLesson, rowId: string) {
    const option = mergeOptions.find((o) => o.rowId === rowId);
    setLesson({
      ...l,
      mergeWith: rowId || null,
      teacherIds: option ? option.teacherIds : l.teacherIds,
      roomIds: option ? option.roomIds : l.roomIds,
    });
  }
  const dateBounds = { min: s.settings.yearStart, max: s.settings.yearEnd };
  const mergeChoice = mergeOptions.find((o) => o.rowId === lesson?.mergeWith);
  return (
    <div className="dd" ref={panel}>
      <div
        className="dd-tabs"
        role="tablist"
        aria-label="Раздел расписания по датам"
      >
        {DAY_TABS.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              tabButtons.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`dd-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="dd-panel"
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => switchTab(t.id)}
            onKeyDown={(e) => keyTab(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <details className="dd-help">
        <summary>Как устроены замены</summary>
        <p>
          Здесь — фактические занятия на дату. Замены, переносы и отмены
          сохраняются отдельно от повторяющейся недели. Влияние каникул
          включается явно в «Учебном календаре».
        </p>
      </details>
      <div className="dd-controls">
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
        {tab === "day" && (
          <>
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
          </>
        )}
      </div>
      <div className="dd-line">
        <p className="dd-summary">
          <strong>
            {dateInWords(day)}
            {tab === "day" && fresh && ` · ${cycleName(report)}`}
            {tab === "day" &&
              fresh &&
              report.scheduleDay &&
              ` · работает как «${s.settings.days[report.scheduleDay.day] ?? report.scheduleDay.day}»` +
                (report.scheduleDay.classIds.length
                  ? ` для классов: ${report.scheduleDay.classIds.map((id) => named(s.classes, id)).join(", ")}`
                  : "")}
          </strong>
          {tab === "day" && fresh && (
            <span>Показано занятий: {shown.length}</span>
          )}
        </p>
        <div className="dd-tools">
          <button className="dd-btn" disabled={busy} onClick={newLesson}>
            Добавить разовый урок
          </button>
          <button className="dd-btn" disabled={busy} onClick={newAbsence}>
            Указать отсутствие
          </button>
          <button
            className="dd-btn"
            disabled={busy || !history.undo}
            onClick={() => historyAction("undo")}
          >
            Отменить действие
          </button>
          <button
            className="dd-btn"
            disabled={busy || !history.redo}
            onClick={() => historyAction("redo")}
          >
            Повторить действие
          </button>
          {tab === "day" && fresh && (
            <button
              className="dd-btn"
              disabled={!shown.length}
              onClick={downloadCsv}
            >
              Скачать CSV
            </button>
          )}
        </div>
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
                const problem = lessonProblem(
                  lesson,
                  selectedRow,
                  s.datedLessons?.find((c) => c.id === lesson.id),
                );
                if (problem) setError(problem);
                else void check("datedLessons", lesson);
              }}
            >
              <h3>
                {lesson.kind === "cancel"
                  ? "Отмена урока"
                  : lesson.kind === "add"
                    ? "Разовое занятие"
                    : CHANGE_TYPES[effectiveType(lesson)]}
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
              {lesson.kind === "change" && (
                <label className="field">
                  <span>Тип изменения</span>
                  <select
                    value={effectiveType(lesson)}
                    onChange={(e) =>
                      pickType(lesson, e.target.value as ChangeType)
                    }
                  >
                    {(
                      ["substitution", "move", "merge", "self_study"] as const
                    ).map((t) => (
                      <option key={t} value={t}>
                        {CHANGE_TYPES[t]}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {lesson.kind === "change" && lesson.changeType === "merge" && (
                <label className="field">
                  <span>Присоединить к уроку</span>
                  <select
                    required
                    value={lesson.mergeWith || ""}
                    onChange={(e) => pickMerge(lesson, e.target.value)}
                  >
                    <option value="">
                      {mergeOptions.length
                        ? "Выберите урок"
                        : "Нажмите «Подобрать заменяющего», чтобы увидеть уроки"}
                    </option>
                    {lesson.mergeWith && !mergeChoice && (
                      <option value={lesson.mergeWith}>
                        Текущий урок объединения
                      </option>
                    )}
                    {mergeOptions.map((o) => (
                      <option key={o.rowId} value={o.rowId}>
                        {o.start}–{o.end} · {o.group} · {o.subject} ·{" "}
                        {o.teachers || "без учителя"}
                        {o.fits === false ? " · не вмещает" : ""}
                      </option>
                    ))}
                  </select>
                  {mergeChoice && (
                    <small>
                      Вместимость: {mergeChoice.capacity ?? "не задана"},
                      участников {mergeChoice.attendance ?? "неизвестно"}
                      {mergeChoice.fits === false ? " — не вмещает" : ""}.
                      Учителя и кабинеты берутся у выбранного урока.
                    </small>
                  )}
                </label>
              )}
              {lesson.kind !== "cancel" && (
                <>
                  <div className="dated-fields">
                    <DateField
                      label="Дата занятия"
                      value={lesson.onDate}
                      onChange={(v) =>
                        setLesson({
                          ...lesson,
                          onDate: v,
                          changeType:
                            lesson.kind === "change" &&
                            (lesson.changeType === "substitution" ||
                              lesson.changeType === "move")
                              ? v !== lesson.sourceDate
                                ? "move"
                                : "substitution"
                              : lesson.changeType,
                        })
                      }
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
                        Список упорядочен по баллам: свободен в это время
                        (обязательно), ведёт предмет, знает учеников, уже в
                        школе, равномерность замен за месяц, здание. Веса
                        настраиваются на вкладке «Итоги замен». После ручной
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
                        Подобрать заменяющего
                      </button>
                      {candidates && (
                        <div className="dated-candidates">
                          {candidates.map((c) => (
                            <div key={c.id}>
                              <span>
                                <strong>{c.name}</strong> · {c.score} балл. ·
                                уроков в этот день: {c.lessons}
                                <small>
                                  {c.factors.map(factorLine).join(" · ")}
                                </small>
                                {!c.available && (
                                  <small>{c.reasons.join("; ")}</small>
                                )}
                              </span>
                              <button
                                type="button"
                                disabled={!c.available || busy}
                                onClick={() =>
                                  setLesson({
                                    ...lesson,
                                    teacherIds:
                                      lesson.changeType === "self_study"
                                        ? [c.id]
                                        : Array.from(
                                            new Set([
                                              ...(
                                                lesson.teacherIds || []
                                              ).filter(
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
                      label={
                        lesson.changeType === "self_study"
                          ? "Ответственный сотрудник"
                          : "Учителя занятия"
                      }
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
      <div
        className="dd-panel"
        role="tabpanel"
        id="dd-panel"
        aria-labelledby={`dd-tab-${tab}`}
      >
        {tab === "day" &&
          (loading ? (
            <p role="status">Загружаю занятия…</p>
          ) : !fresh ? (
            <p>Данные изменились в другом окне. Обновите страницу.</p>
          ) : (
            <>
              {report.events.length > 0 && (
                <p className="info-line dd-note">
                  {report.events
                    .map(
                      (e) =>
                        `${e.name} · ${e.effect === "cancel" ? "отмена для " + (e.classIds?.length ? e.classIds.map((id) => named(s.classes, id)).join(", ") : "всех классов") : "информационная отметка"}`,
                    )
                    .join("; ")}
                </p>
              )}
              {!report.completeChecks && (
                <p className="muted dd-note">
                  Весь день: без учителя {report.missingTeachers}, без кабинета{" "}
                  {report.missingRooms}, неполных составов{" "}
                  {report.unknownGroups}, непроверенной вместимости{" "}
                  {report.unknownCapacity}, незаполненных мест дежурных{" "}
                  {report.unfilledDuties || 0}. Эти пробелы не считаются
                  успешной проверкой.
                </p>
              )}
              <details
                className="dated-issues dd-check"
                open={report.issueCount > 0}
              >
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
                <details className="dated-issues dd-check">
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
              {!shown.length ? (
                <p className="empty-state">
                  На эту дату по выбранным условиям занятий нет.
                </p>
              ) : (
                <div className="table-card">
                  <table className="data-table dd-table">
                    <caption className="sr-only">
                      Занятия: {dateInWords(day)}
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col" className="dd-time">
                          Время
                        </th>
                        <th scope="col" className="dd-lesson">
                          Занятие
                        </th>
                        <th scope="col" className="dd-teacher">
                          Учитель
                        </th>
                        <th scope="col" className="dd-room">
                          Кабинет
                        </th>
                        <th scope="col" className="dd-state">
                          Состояние
                        </th>
                        <th scope="col" className="dd-act">
                          <span className="sr-only">Действия</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((r) => {
                        const v = dayRowView(r, excluded(r));
                        const away = r.excludedClassIds.length
                          ? `Не посещают: ${r.excludedClassIds.map((id) => named(s.classes, id)).join(", ")}`
                          : "";
                        const reasons = reasonText(r.reasons, v.state);
                        return (
                          <tr
                            key={r.id}
                            className={v.struck ? "cancelled" : ""}
                          >
                            <td className="dd-time">{v.time}</td>
                            <td
                              className="dd-lesson"
                              title={away ? `${v.lesson} · ${away}` : v.lesson}
                            >
                              <strong className="dd-name">{v.lesson}</strong>
                              {away && (
                                <span className="dd-note-away">{away}</span>
                              )}
                            </td>
                            <td className="dd-teacher" title={v.teacher}>
                              {v.teacherMissing ? (
                                <span className="dd-missing">{v.teacher}</span>
                              ) : (
                                v.teacher
                              )}
                            </td>
                            <td className="dd-room" title={v.room}>
                              {v.roomMissing ? (
                                <span className="dd-missing">{v.room}</span>
                              ) : (
                                v.room
                              )}
                            </td>
                            <td
                              className="dd-state"
                              title={
                                reasons ? `${v.state}: ${reasons}` : v.state
                              }
                            >
                              <span className={`dd-badge ${v.tone}`}>
                                {v.state}
                              </span>
                              {reasons && (
                                <span className="dd-reason">{reasons}</span>
                              )}
                            </td>
                            <td className="dd-act">
                              <div className="dd-acts">
                                {v.actions.map((a) => (
                                  <button
                                    key={a.action}
                                    type="button"
                                    className="dd-btn"
                                    data-dd-row={r.id}
                                    data-dd-action={a.action}
                                    aria-label={a.label}
                                    disabled={a.action !== "commitment" && busy}
                                    onClick={() => runRowAction(r, a.action)}
                                  >
                                    {a.text}
                                  </button>
                                ))}
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ))}
        {tab === "absences" && (
          <div className="dated-records">
            <AbsenceFeed
              workspace={workspace}
              revision={s.revision}
              busy={busy}
              onAdd={(value) => {
                reset();
                void check("absences", value);
              }}
              onRemove={(id) => {
                reset();
                void check("absences", undefined, id);
              }}
              onPlan={(date, teacherId) => {
                reset();
                setDay(date);
                setPlanTeacher(teacherId);
                setTab("plan");
              }}
            />
            {!(s.absences || []).length ? (
              <p>Отсутствия пока не внесены.</p>
            ) : (
              <div className="table-card">
                <table className="data-table dl-table dl-blocks">
                  <caption className="sr-only">Отсутствия учителей</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="dl-cell">
                        Учитель
                      </th>
                      <th scope="col" className="dl-cell">
                        Причина
                      </th>
                      <th scope="col" className="dl-cell dl-fit">
                        Период
                      </th>
                      <th scope="col" className="dl-cell dl-act">
                        <span className="sr-only">Действия</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {absenceRows(s.absences || [], (id) =>
                      named(s.teachers, id),
                    ).map(({ absence: a, view: v }) => (
                      <tr key={a.id}>
                        <td
                          className="dl-cell dl-text dl-main"
                          title={v.teacher}
                        >
                          <strong>{v.teacher}</strong>
                        </td>
                        <td
                          className="dl-cell dl-text dl-muted"
                          title={v.reason}
                        >
                          {v.reason}
                        </td>
                        <td className="dl-cell dl-fit">
                          {v.period} · {v.time}
                        </td>
                        <td className="dl-cell dl-act">
                          <div className="dl-acts">
                            <button
                              type="button"
                              className="dd-btn"
                              aria-label={v.labels.plan}
                              disabled={busy}
                              onClick={() => {
                                reset();
                                setDay(a.start);
                                setPlanTeacher(a.teacherId);
                                setTab("plan");
                              }}
                            >
                              Замены
                            </button>
                            <button
                              type="button"
                              className="dd-btn"
                              aria-label={v.labels.lessons}
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
                            <button
                              type="button"
                              className="dd-btn"
                              aria-label={v.labels.edit}
                              disabled={busy}
                              onClick={() => {
                                reset();
                                setAbsence(a);
                              }}
                            >
                              Изменить
                            </button>
                            <button
                              type="button"
                              className="dd-btn"
                              aria-label={v.labels.remove}
                              disabled={busy}
                              onClick={() => {
                                reset();
                                void check("absences", undefined, a.id);
                              }}
                            >
                              Удалить
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
        {tab === "changes" && (
          <DatedChanges
            state={s}
            busy={busy}
            onOpenDate={(c) => {
              setDay(c.onDate);
              setTab("day");
              setMode("all");
              reset();
            }}
            onEdit={(c) => {
              reset();
              void openChange(c);
            }}
            onRemove={(c) => {
              reset();
              void check("datedLessons", undefined, c.id);
            }}
          />
        )}
        {tab === "plan" && (
          <SubstitutionPlan
            state={s}
            workspace={workspace}
            onState={onState}
            day={day}
            initialTeacher={planTeacher}
          />
        )}
        {tab === "days" && (
          <ScheduleDays
            state={s}
            busy={busy}
            onCheck={(value, removeId) => {
              reset();
              void check("scheduleDays", value, removeId);
            }}
            onOpenDate={(d) => {
              reset();
              setDay(d);
              setTab("day");
              setMode("all");
            }}
          />
        )}
        {tab === "report" && (
          <SubstitutionReport
            state={s}
            workspace={workspace}
            onState={onState}
          />
        )}
      </div>
    </div>
  );
}

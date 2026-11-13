import { useEffect, useMemo, useRef, useState } from "react";
import type { ClipboardEvent, KeyboardEvent } from "react";
import { ExternalLink, EyeOff, Lock } from "lucide-react";
import { api } from "./api";
import CategoryBadge from "./CategoryBadge";
import CourseAssignmentDialog, { type Created } from "./CourseAssignmentDialog";
import {
  afterSave,
  applyPaste,
  cellText,
  chunks,
  commitCell,
  courseCsv,
  defaultCoursePeriod,
  editKey,
  editable,
  firstOpenRow,
  lessonBodies,
  markText,
  nextCell,
  openCell,
  parsePaste,
  putEdit,
  recallScale,
  rememberScale,
  sameAsSaved,
  spread,
  type CourseList,
  type CourseTable,
  type Edit,
  type Edits,
  type OpenCell,
  type Position,
  type SaveResponse,
} from "./courseTable";
import { finalLabel } from "./finalGrades";
import { ruDate } from "./journalCoverage";
import { useLaunch } from "./launch";
import "./CourseTable.css";

const LEAVE = "В таблице есть несохранённые оценки. Перейти без сохранения?";
const HIDDEN =
  "Урок скрыт от учеников и родителей. «Опубликовать» не вернёт его, пока в нём нет правки из таблицы.";
const scopeNotes = {
  course:
    "Вы видите черновики своих уроков. Точка в ячейке — оценка пока не опубликована для учеников и родителей.",
  admin:
    "Администратор видит и правит черновики уроков любого курса. Точка в ячейке — оценка пока не опубликована для учеников и родителей.",
  class:
    "Классный руководитель видит опубликованные оценки учеников своего класса.",
};

// A grading scale the final-grade rule can compute a grade on (the options of the final grades).
interface ScaleOption {
  id: string;
  name: string;
  kind: string;
  active: boolean;
}

export default function CourseTablePanel({
  workspace,
  onOpen,
  defaultOpen = false,
  course: chosen,
  embedded = false,
  onBack,
}: {
  workspace: string;
  onOpen: (lessonKey: string, day: string) => void;
  // Opened from the menu: the panel starts expanded.
  defaultOpen?: boolean;
  // The course of a card of the gradebook, «groupId|subjectId». The table then fills the page: no fold, no choice of the course.
  course?: string;
  embedded?: boolean;
  onBack?: () => void;
}) {
  const [folded, setExpanded] = useState(defaultOpen),
    [list, setList] = useState<CourseList | null>(null);
  const expanded = embedded || folded;
  const [course, setCourse] = useState(chosen ?? ""),
    [filter, setFilter] = useState("");
  // The scales the computed grade can use; `null` until the options arrived, whatever they held.
  const [scales, setScales] = useState<ScaleOption[] | null>(null),
    [scaleId, setScaleId] = useState(""),
    [notes, setNotes] = useState<Record<
      string,
      { praise: number; remark: number }
    > | null>(null),
    [selected, setSelected] = useState<Set<string>>(new Set());
  const [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [table, setTable] = useState<CourseTable | null>(null),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false);
  const [edits, setEditsState] = useState<Edits>({}),
    [active, setActive] = useState<OpenCell | null>(null),
    [text, setText] = useState("");
  // `error` is a failed load; the outcome of a save, a failed one included, is the note under the buttons.
  const [error, setError] = useState(""),
    [note, setNote] = useState<{ text: string; alert?: boolean } | null>(null),
    [problems, setProblems] = useState<{ day: string; message: string }[]>([]);
  const [reload, setReload] = useState(0);
  const editsRef = useRef<Edits>({});
  const loadSeq = useRef(0);
  const input = useRef<HTMLInputElement | null>(null);
  // A request id per request body: a retry after a lost answer replays the stored result instead of saving twice.
  const requestIds = useRef(new Map<string, string>());
  const dirty = Object.keys(edits).length > 0;
  const period = list?.periods.find((p) => p.start === start && p.end === end);
  const periodId = period?.id ?? "custom";
  // Nothing is typed while a save is on its way or the table is being replaced by fresh data.
  const frozen = busy || loading;
  // The cell being typed in; closed once the table on screen is no longer the one it was opened on.
  const cursor = openCell(active, table);

  function setEdits(next: Edits) {
    editsRef.current = next;
    setEditsState(next);
  }
  const leave = () =>
    !Object.keys(editsRef.current).length || window.confirm(LEAVE);
  useEffect(() => {
    if (!dirty) return;
    const unload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const navigate = (e: Event) => {
      if (!window.confirm(LEAVE)) e.preventDefault();
    };
    window.addEventListener("beforeunload", unload);
    window.addEventListener("atlas-homework-leave", navigate);
    return () => {
      window.removeEventListener("beforeunload", unload);
      window.removeEventListener("atlas-homework-leave", navigate);
    };
  }, [dirty]);

  // «Новая оценка / работа» of the launcher: once the table of the course is on screen, «Новое назначение» is pressed for the teacher.
  const [newWorkAsked, setNewWorkAsked] = useState(false),
    [assigning, setAssigning] = useState(false),
    // The work just added: the cursor goes to the first cell of its column once the reloaded table holds it.
    [added, setAdded] = useState<Created | null>(null);
  useLaunch(["newWork"], () => setNewWorkAsked(true));
  useEffect(() => {
    if (!newWorkAsked || !table) return;
    setNewWorkAsked(false);
    if (table.canEdit) assign();
  }, [newWorkAsked, table]);
  useEffect(() => {
    if (!added || !table || frozen) return;
    const col = table.works.findIndex(
      (w) =>
        w.id === added.assessmentId &&
        table.lessons[w.lesson].lessonKey === added.lessonKey,
    );
    // The table on screen may be the one from before the work was added: wait for the reload.
    if (col < 0) return;
    setAdded(null);
    document
      .querySelector(`th[data-col="${col}"]`)
      ?.scrollIntoView({ block: "nearest", inline: "center" });
    const row = firstOpenRow(table, col);
    if (row >= 0) activate({ row, col });
  }, [added, table, frozen]);
  useEffect(() => {
    if (!expanded || list) return;
    let live = true;
    api("journal/course-table/courses", workspace)
      .then((value: CourseList) => {
        if (!live) return;
        setList(value);
        setError("");
        const range = defaultCoursePeriod(
          value.today,
          value.yearStart,
          value.yearEnd,
          value.periods,
        );
        setStart(range.start);
        setEnd(range.end);
        if (value.courses.length === 1)
          setCourse(
            `${value.courses[0].groupId}|${value.courses[0].subjectId}`,
          );
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [expanded, workspace, list]);

  useEffect(() => {
    if (!expanded) return;
    let live = true;
    api("final-grades/options", workspace)
      .then((r: { scales?: ScaleOption[] }) => {
        if (!live) return;
        const usable = (r.scales ?? []).filter(
          (x) => x.active && x.kind === "thresholds",
        );
        setScales(usable);
        const kept = recallScale(workspace);
        setScaleId(
          usable.find((x) => x.id === kept)?.id ?? usable[0]?.id ?? "",
        );
      })
      // An account without final grades has no scale, and the column stays out.
      .catch(() => live && setScales([]));
    return () => {
      live = false;
    };
  }, [expanded, workspace]);
  useEffect(() => {
    setTable(null);
    setActive(null);
    setSelected(new Set());
  }, [workspace, course, start, end]);
  useEffect(() => {
    if (!expanded || !course || !start || !end || end < start || !scales)
      return;
    const [groupId, subjectId] = course.split("|");
    const made = ++loadSeq.current;
    setLoading(true);
    const asked = new URLSearchParams({ groupId, subjectId, start, end });
    if (scaleId) asked.set("scaleId", scaleId);
    if (periodId !== "custom") asked.set("periodId", periodId);
    api(`journal/course-table?${asked}`, workspace)
      .then((value: CourseTable) => {
        if (loadSeq.current !== made) return;
        setTable(value);
        setError("");
        // The open cell belongs to the old table: its input shows a value of data that is gone, and rows or columns may have moved.
        setActive(null);
        // Typed values that the fresh data already holds are done; the rest stay on top of it.
        const kept: Edits = {};
        for (const [key, e] of Object.entries(editsRef.current)) {
          const found = lookup(value, e);
          if (!found || !sameAsSaved(e, found.pupil.marks[found.col]))
            kept[key] = e;
        }
        if (Object.keys(kept).length !== Object.keys(editsRef.current).length)
          setEdits(kept);
      })
      .catch((e) => loadSeq.current === made && setError(e.message))
      .finally(() => loadSeq.current === made && setLoading(false));
  }, [
    expanded,
    workspace,
    course,
    start,
    end,
    reload,
    scales,
    scaleId,
    periodId,
  ]);
  // A note written in the form this table opened (or anywhere else) is counted as soon as the form closes.
  const [noteEpoch, setNoteEpoch] = useState(0),
    notesFor = useRef("");
  useEffect(() => {
    const bump = () => setNoteEpoch((n) => n + 1);
    window.addEventListener("atlas-behaviour-closed", bump);
    return () => window.removeEventListener("atlas-behaviour-closed", bump);
  }, []);
  // The praise and remarks of the period in this subject; an account without them has no such column.
  useEffect(() => {
    // Another table starts without the column; the same one keeps its counts until the fresh ones arrive.
    const shown = table
      ? `${workspace}|${table.groupId}|${table.subjectId}|${table.start}|${table.end}`
      : "";
    if (shown !== notesFor.current) {
      notesFor.current = shown;
      setNotes(null);
    }
    if (!table) return;
    let live = true;
    api(
      `behaviour/summary?${new URLSearchParams({ start: table.start, end: table.end, subjectId: table.subjectId })}`,
      workspace,
    )
      .then(
        (r: {
          items: { studentId: string; praise: number; remark: number }[];
        }) => {
          if (live)
            setNotes(Object.fromEntries(r.items.map((i) => [i.studentId, i])));
        },
      )
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [
    workspace,
    table?.groupId,
    table?.subjectId,
    table?.start,
    table?.end,
    table?.revision,
    noteEpoch,
  ]);

  const index = useMemo(() => {
    const works = new Map<string, number>();
    table?.works.forEach((w, i) =>
      works.set(`${table.lessons[w.lesson].lessonKey}|${w.id}`, i),
    );
    return { works, pupils: new Map(table?.pupils.map((p, i) => [p.id, i])) };
  }, [table]);
  // The pupil and work column an edit belongs to, or null when the table no longer has them.
  function lookup(value: CourseTable, e: Edit) {
    const col = value.works.findIndex(
      (w) =>
        value.lessons[w.lesson].lessonKey === e.lessonKey &&
        w.id === e.assessmentId,
    );
    const pupil = value.pupils.find((p) => p.id === e.studentId);
    return col < 0 || !pupil ? null : { col, pupil };
  }
  function commit(value: string): Edits {
    // Nothing is typed while a save is on its way: the edits it carries are decided when the answer comes.
    if (frozen || !table) return editsRef.current;
    const next = commitCell(active, table, value, editsRef.current);
    if (next !== editsRef.current) setEdits(next);
    return next;
  }
  function activate(pos: Position, from: Edits = editsRef.current) {
    if (!table) return;
    setActive({ ...pos, table });
    setText(cellText(table, pos, from));
  }
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, [cursor?.row, cursor?.col]);

  function move(key: string, e: KeyboardEvent<HTMLInputElement>) {
    if (!cursor || !table) return;
    const target = nextCell(cursor, key, table);
    const next = commit(text);
    if (target.row === cursor.row && target.col === cursor.col) {
      // Tab at the edge leaves the table; the other keys stay put with the value selected for the next one.
      if (!key.endsWith("Tab")) {
        e.preventDefault();
        e.currentTarget.select();
      }
      return;
    }
    e.preventDefault();
    activate(target, next);
  }
  function keyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (frozen) return;
    const box = e.currentTarget;
    const whole =
      box.selectionStart === 0 && box.selectionEnd === box.value.length;
    const caret = (at: number) =>
      whole || (box.selectionStart === at && box.selectionEnd === at);
    if (["Enter", "Tab"].includes(e.key))
      move((e.shiftKey ? "Shift+" : "") + e.key, e);
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") move(e.key, e);
    else if (e.key === "ArrowLeft" && caret(0)) move(e.key, e);
    else if (e.key === "ArrowRight" && caret(box.value.length)) move(e.key, e);
    else if (e.key === "Escape" && cursor && table) {
      e.preventDefault();
      setText(cellText(table, cursor, edits));
    } else if (
      (e.key === "Delete" || e.key === "Backspace") &&
      whole &&
      cursor
    ) {
      // Clearing is always explicit: a blank paste never clears.
      e.preventDefault();
      setText("");
      commit("");
    }
  }
  function paste(e: ClipboardEvent<HTMLInputElement>) {
    if (!cursor || !table) return;
    e.preventDefault();
    if (frozen) return;
    const { edits: pasted, skipped } = applyPaste(
      parsePaste(e.clipboardData.getData("text")),
      cursor,
      table,
    );
    let next = { ...editsRef.current };
    for (const edit of pasted) {
      const at = {
        row: index.pupils.get(edit.studentId)!,
        col: index.works.get(`${edit.lessonKey}|${edit.assessmentId}`)!,
      };
      next = putEdit(table, next, at, edit.raw);
    }
    setEdits(next);
    setText(cellText(table, cursor, next));
    setNote(skipped ? { text: `Пропущено ячеек: ${skipped}` } : null);
  }

  async function submit(publish: boolean) {
    if (!table || busy) return;
    commit(text);
    // What the requests carry; whatever is typed afterwards is not theirs to settle.
    const sent = Object.values(editsRef.current);
    const bodies = lessonBodies(table, sent, publish);
    if (!bodies.length) return;
    setBusy(true);
    setError("");
    setNote(null);
    setProblems([]);
    const [groupId, subjectId] = course.split("|");
    const responses: SaveResponse[] = [];
    let failure = "";
    try {
      for (const part of chunks(bodies)) {
        const body = {
          groupId,
          subjectId,
          revision: table.revision,
          publish,
          lessons: part,
        };
        const signature = JSON.stringify(body);
        const requestId =
          requestIds.current.get(signature) ?? crypto.randomUUID();
        requestIds.current.set(signature, requestId);
        responses.push(
          await api("journal/course-table/save", workspace, "POST", {
            requestId,
            ...body,
          }),
        );
        requestIds.current.delete(signature);
      }
    } catch (e) {
      failure = (e as Error).message;
    }
    const outcome = afterSave(Object.values(editsRef.current), responses, sent);
    setEdits(Object.fromEntries(outcome.edits.map((e) => [editKey(e), e])));
    const invalid = outcome.edits.filter((e) => e.error).length;
    setActive(null);
    setProblems(
      outcome.conflicts.map((c) => ({ day: c.day, message: c.message })),
    );
    // A failed request is told once, with the summary: the table reloads at once, so the failure is no banner that would outlive it.
    const summary =
      `Сохранено уроков: ${outcome.saved}, без изменений: ${outcome.unchanged}, не сохранено: ${outcome.conflicts.length}.` +
      (invalid ? ` Исправьте ячейки с ошибками: ${invalid}.` : "");
    setNote({
      text: failure ? `Ошибка: ${failure} ${summary}` : summary,
      alert: !!failure,
    });
    setBusy(false);
    setReload((n) => n + 1);
  }
  // «Новое назначение»: the dialog adds a draft work to a lesson of the course. Typed marks stay: the table reloads under them.
  function assign() {
    if (!table || busy) return;
    commit(text);
    setActive(null);
    setAssigning(true);
  }
  function assigned(made: Created) {
    setAssigning(false);
    setAdded(made);
    setProblems([]);
    setNote({
      text: `Назначение «${made.title}» добавлено черновиком. Поставьте оценки и нажмите «Опубликовать»: до этого ученики и родители его не видят.`,
    });
    // A day the widened search found may lie outside the period of the table: the period grows to hold it.
    if (made.day < start) setStart(made.day);
    if (made.day > end) setEnd(made.day);
    setReload((n) => n + 1);
  }
  function refresh() {
    setError("");
    setNote((n) => (n?.alert ? null : n));
    setReload((n) => n + 1);
  }
  function print() {
    setActive(null);
    document.body.classList.add("course-table-printing");
    const done = () => {
      document.body.classList.remove("course-table-printing");
      window.removeEventListener("afterprint", done);
    };
    window.addEventListener("afterprint", done);
    window.print();
  }
  function discard() {
    setEdits({});
    setActive(null);
    setNote(null);
    setProblems([]);
  }
  function choose(change: () => void) {
    if (!leave()) return;
    setEdits({});
    setNote(null);
    setProblems([]);
    setError("");
    change();
  }

  const courses = (list?.courses ?? []).filter((c) =>
    `${c.group} ${c.subject} ${c.classes.join(" ")} ${c.teachers.join(" ")}`
      .toLocaleLowerCase()
      .includes(filter.trim().toLocaleLowerCase()),
  );
  const pending = table
    ? lessonBodies(table, Object.values(edits), true).length > 0
    : false;
  const sendable = table
    ? lessonBodies(table, Object.values(edits), false).length
    : 0;
  const orphans = table
    ? Object.values(edits).filter((e) => !lookup(table, e)).length
    : 0;
  const firstEditable = useMemo(() => {
    if (!table) return null;
    for (let row = 0; row < table.pupils.length; row++)
      for (let col = 0; col < table.works.length; col++)
        if (editable(table.pupils[row], table.works[col], table))
          return { row, col };
    return null;
  }, [table]);

  const scale = scales?.find((x) => x.id === scaleId);
  const picked = table?.pupils.filter((p) => selected.has(p.id)) ?? [];
  const openBehaviour = (measure: boolean) =>
    window.dispatchEvent(
      new CustomEvent("atlas-open-behaviour", {
        detail: {
          workspace,
          prefill: {
            studentIds: picked.map((p) => p.id),
            measure,
            subjectId: table?.subjectId,
            subject: table?.subject,
          },
        },
      }),
    );
  const body = (
    <>
      {!embedded && (
        <p className="ct-noprint">
          Ученики и работы одного курса за период. Оценки вводятся с клавиатуры
          или вставкой из таблицы; темы, домашние задания и посещаемость здесь
          не меняются.
        </p>
      )}
      {embedded && (
        <div className="ct-noprint">
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => leave() && onBack?.()}
          >
            ‹ Все курсы
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {!list && !error && <p role="status">Загружаю курсы…</p>}
      {list && !list.courses.length && (
        <p className="empty-state">
          Курсов, для которых доступна таблица, нет.
        </p>
      )}
      {!!list?.courses.length && (
        <div className="dated-toolbar ct-toolbar ct-noprint">
          {!embedded && (
            <>
              <label className="field">
                <span>Найти курс</span>
                <input
                  type="search"
                  value={filter}
                  placeholder="Группа, предмет, учитель"
                  onChange={(e) => setFilter(e.target.value)}
                />
              </label>
              <label className="field">
                <span>Курс</span>
                <select
                  value={course}
                  disabled={busy}
                  onChange={(e) => choose(() => setCourse(e.target.value))}
                >
                  <option value="">Выберите курс</option>
                  {courses.map((c) => (
                    <option
                      key={`${c.groupId}|${c.subjectId}`}
                      value={`${c.groupId}|${c.subjectId}`}
                    >
                      {c.group} · {c.subject}
                    </option>
                  ))}
                  {course &&
                    !courses.some(
                      (c) => `${c.groupId}|${c.subjectId}` === course,
                    ) && <option value={course}>Выбранный курс</option>}
                </select>
              </label>
            </>
          )}
          <label className="field">
            <span>Период</span>
            <select
              value={periodId}
              disabled={busy}
              onChange={(e) => {
                const found = list.periods.find((p) => p.id === e.target.value);
                if (found)
                  choose(() => {
                    setStart(found.start);
                    setEnd(found.end);
                  });
              }}
            >
              {list.periods.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({ruDate(p.start)}–{ruDate(p.end)})
                </option>
              ))}
              <option value="custom">Другой период</option>
            </select>
          </label>
          {!!scales?.length && (
            <label className="field">
              <span>Шкала</span>
              <select
                value={scaleId}
                disabled={busy}
                onChange={(e) => {
                  rememberScale(workspace, e.target.value);
                  setScaleId(e.target.value);
                }}
              >
                {scales.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            <span>С</span>
            <input
              type="date"
              value={start}
              min={list.yearStart}
              max={list.yearEnd}
              onChange={(e) => choose(() => setStart(e.target.value))}
            />
          </label>
          <label className="field">
            <span>По</span>
            <input
              type="date"
              value={end}
              min={list.yearStart}
              max={list.yearEnd}
              onChange={(e) => choose(() => setEnd(e.target.value))}
            />
          </label>
        </div>
      )}
      {!!list?.courses.length && end < start && (
        <p role="alert" className="error-banner">
          Конец периода раньше начала.
        </p>
      )}
      {!!list?.courses.length && !course && (
        <p className="empty-state">Выберите курс, чтобы увидеть таблицу.</p>
      )}
      {loading && <p role="status">Загружаю таблицу…</p>}
      {table && (
        <>
          <header className="ct-head">
            <h3>
              {table.group} · {table.subject}
            </h3>
            <span>
              {ruDate(table.start)}–{ruDate(table.end)} · {table.pupils.length}{" "}
              уч.
            </span>
          </header>
          <p className="info-line ct-noprint">{table.scope === "admin" && !table.canEdit
              ? "Администратор видит только опубликованные оценки."
              : scopeNotes[table.scope]}</p>
          <div className="ct-actions ct-noprint">
            {table.canEdit && (
              <>
                <button
                  disabled={busy}
                  title="Добавить в таблицу работу для ФО, СОР или СОЧ"
                  onClick={assign}
                >
                  Новое назначение
                </button>
                <button
                  disabled={busy || !sendable}
                  onClick={() => void submit(false)}
                >
                  Сохранить черновик
                </button>
                <button
                  disabled={busy || !pending}
                  onClick={() => void submit(true)}
                >
                  Опубликовать
                </button>
                <button disabled={busy || !dirty} onClick={discard}>
                  Сбросить правки
                </button>
              </>
            )}
            <button
              disabled={!picked.length}
              title={picked.length ? undefined : "Отметьте учеников в таблице"}
              onClick={() => openBehaviour(false)}
            >
              Новая заметка
            </button>
            <button
              disabled={!picked.length}
              title={picked.length ? undefined : "Отметьте учеников в таблице"}
              onClick={() => openBehaviour(true)}
            >
              Воспитательная мера
            </button>
            <button disabled={frozen} onClick={refresh}>
              Обновить
            </button>
            <button
              disabled={!table.works.length}
              onClick={() => courseCsv(table)}
            >
              CSV
            </button>
            <button disabled={!table.works.length} onClick={print}>
              Печать
            </button>
          </div>
          {assigning && table.canEdit && (
            <CourseAssignmentDialog
              workspace={workspace}
              groupId={table.groupId}
              subjectId={table.subjectId}
              start={table.start}
              end={table.end}
              year={{
                start: list?.yearStart ?? start,
                end: list?.yearEnd ?? end,
              }}
              onClose={() => setAssigning(false)}
              onCreated={assigned}
            />
          )}
          {note && (
            <p
              role={note.alert ? "alert" : "status"}
              className={`${note.alert ? "error-banner" : "info-line"} ct-noprint`}
            >
              {note.text}
            </p>
          )}
          {!!problems.length && (
            <ul className="ct-problems ct-noprint">
              {problems.map((p, i) => (
                <li key={i}>
                  {ruDate(p.day)}: {p.message}
                </li>
              ))}
            </ul>
          )}
          {dirty && (
            <p className="info-line ct-noprint">
              Итоги и доли показывают сохранённые оценки и пересчитаются после
              сохранения.
              {!!orphans &&
                ` Правок, которых больше нет в таблице: ${orphans}; их можно сбросить.`}
            </p>
          )}
          {!table.works.length ? (
            <p className="empty-state">
              В этом курсе за период нет работ.
              {table.canEdit && " Добавьте первую кнопкой «Новое назначение»."}
            </p>
          ) : (
            <div className="course-table-scroll">
              <table className="course-table">
                <thead>
                  <tr>
                    <th className="ct-pick ct-noprint" scope="col">
                      <input
                        type="checkbox"
                        aria-label="Выбрать всех учеников"
                        checked={
                          !!table.pupils.length &&
                          picked.length === table.pupils.length
                        }
                        onChange={(e) =>
                          setSelected(
                            e.target.checked
                              ? new Set(table.pupils.map((p) => p.id))
                              : new Set(),
                          )
                        }
                      />
                    </th>
                    <th className="ct-pupil" scope="col">
                      Ученик
                    </th>
                    {table.works.map((w, col) => {
                      const lesson = table.lessons[w.lesson];
                      const category = table.categories.find(
                        (c) => c.id === w.category,
                      );
                      return (
                        <th
                          key={`${lesson.lessonKey}|${w.id}`}
                          scope="col"
                          className={
                            lesson.editable || !table.canEdit ? "" : "ct-locked"
                          }
                          data-col={col}
                        >
                          <span className="ct-date">
                            {ruDate(lesson.day).slice(0, 5)}
                            {!lesson.editable && table.canEdit && (
                              <span
                                role="img"
                                title={lesson.reason}
                                aria-label={lesson.reason}
                              >
                                <Lock size={12} />
                              </span>
                            )}
                            {lesson.withdrawn && (
                              <span
                                role="img"
                                title={HIDDEN}
                                aria-label={HIDDEN}
                              >
                                <EyeOff size={12} />
                              </span>
                            )}
                            {lesson.openable && (
                              <button
                                className="ct-link ct-noprint"
                                title="Открыть урок в журнале"
                                aria-label={`Открыть урок ${ruDate(lesson.day)}`}
                                disabled={busy}
                                onClick={() =>
                                  leave() &&
                                  onOpen(lesson.lessonKey, lesson.day)
                                }
                              >
                                <ExternalLink size={12} />
                              </button>
                            )}
                          </span>
                          {category && <CategoryBadge category={category} />}
                          <span className="ct-work">{w.name}</span>
                          {w.kind === "points" && (
                            <small>
                              макс {w.maxScore} · вес {w.weight}
                            </small>
                          )}
                        </th>
                      );
                    })}
                    {table.categories.map((c) => (
                      <th key={c.id} scope="col" className="ct-total">
                        {c.name} %
                      </th>
                    ))}
                    <th
                      scope="col"
                      className="ct-total"
                      title="Итог по долям категорий школы"
                    >
                      Среднее, %
                    </th>
                    <th
                      scope="col"
                      className="ct-total"
                      title="Стандартное отклонение процентов по работам с баллами. Это только статистика: на оценки и среднее она не влияет."
                    >
                      Ст. откл.
                    </th>
                    {scale && (
                      <th scope="col" className="ct-total">
                        Вычисленная оценка
                        <small>{scale.name}</small>
                      </th>
                    )}
                    {table.finals && (
                      <th scope="col" className="ct-total">
                        Итоговая оценка
                        <small>{period?.name}</small>
                      </th>
                    )}
                    {notes && (
                      <th scope="col" className="ct-total">
                        Заметки
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {table.pupils.map((p, row) => (
                    <tr key={p.id}>
                      <td className="ct-pick ct-noprint">
                        <input
                          type="checkbox"
                          aria-label={`Выбрать: ${p.name}`}
                          checked={selected.has(p.id)}
                          onChange={(e) =>
                            setSelected((old) => {
                              const next = new Set(old);
                              if (e.target.checked) next.add(p.id);
                              else next.delete(p.id);
                              return next;
                            })
                          }
                        />
                      </td>
                      <th scope="row" className="ct-pupil">
                        <strong>{p.name}</strong>
                        <small>
                          {p.className}
                          {p.from && ` · с ${ruDate(p.from).slice(0, 5)}`}
                          {p.to && ` · до ${ruDate(p.to).slice(0, 5)}`}
                        </small>
                      </th>
                      {table.works.map((w, col) => {
                        const lesson = table.lessons[w.lesson];
                        const key = `${lesson.lessonKey}|${w.id}`;
                        const mark = p.marks[col];
                        if (!p.lessons.includes(w.lesson) && !mark)
                          return (
                            <td
                              key={key}
                              className="ct-off"
                              aria-disabled="true"
                              title="Не в составе на эту дату"
                            >
                              —
                            </td>
                          );
                        const edit =
                          edits[
                            editKey({
                              studentId: p.id,
                              lessonKey: lesson.lessonKey,
                              assessmentId: w.id,
                            })
                          ];
                        const can = editable(p, w, table);
                        const here = cursor?.row === row && cursor.col === col;
                        const why = edit?.error || edit?.message;
                        const title =
                          [
                            why,
                            mark?.unpublished &&
                              !edit &&
                              "Оценка ещё не опубликована",
                            mark?.comment && `Комментарий: ${mark.comment}`,
                            !can &&
                              !p.lessons.includes(w.lesson) &&
                              "Не в составе на эту дату",
                          ]
                            .filter(Boolean)
                            .join(". ") || undefined;
                        const entry =
                          firstEditable?.row === row &&
                          firstEditable.col === col &&
                          !cursor;
                        return (
                          <td
                            key={key}
                            title={title}
                            aria-invalid={why ? true : undefined}
                            aria-disabled={can ? undefined : true}
                            tabIndex={can && (here || entry) ? 0 : undefined}
                            className={[
                              can ? "ct-open" : "ct-closed",
                              edit && "ct-edited",
                              why && "ct-invalid",
                              here && "ct-active",
                              w.kind === "label" && "ct-label",
                            ]
                              .filter(Boolean)
                              .join(" ")}
                            onFocus={() =>
                              can && !here && !frozen && activate({ row, col })
                            }
                            onClick={() =>
                              can && !here && !frozen && activate({ row, col })
                            }
                          >
                            {here ? (
                              <input
                                ref={input}
                                value={text}
                                inputMode={
                                  w.kind === "points" ? "decimal" : "text"
                                }
                                aria-label={`${p.name}: ${w.name}`}
                                readOnly={frozen}
                                onChange={(e) => setText(e.target.value)}
                                onKeyDown={keyDown}
                                onPaste={paste}
                                onBlur={() => commit(text)}
                              />
                            ) : (
                              <span>{edit ? edit.raw : markText(mark, w)}</span>
                            )}
                            {!edit && mark?.unpublished && (
                              <i className="ct-dot" aria-hidden="true" />
                            )}
                            {!!mark?.comment && (
                              <i className="ct-note" aria-hidden="true" />
                            )}
                          </td>
                        );
                      })}
                      {table.categories.map((c) => {
                        const found = p.categories.find((x) => x.id === c.id);
                        return (
                          <td
                            key={c.id}
                            className="ct-total"
                            title={
                              found ? `Оценок: ${found.numeric}` : undefined
                            }
                          >
                            {found ? found.percentage : "—"}
                          </td>
                        );
                      })}
                      <td
                        className="ct-total ct-share"
                        title={`${p.rule}${p.blocked ? `. Не хватает категорий: ${p.missing.join(", ")}` : ""}`}
                      >
                        {p.sharePercentage ?? "—"}
                        {p.blocked && <small>нужны все категории</small>}
                      </td>
                      <td className="ct-total">{spread(p, table) ?? "—"}</td>
                      {scale && (
                        <td className="ct-total">{p.computed ?? "—"}</td>
                      )}
                      {table.finals && (
                        <td className="ct-total">
                          {table.finals[p.id]
                            ? finalLabel(table.finals[p.id])
                            : "—"}
                        </td>
                      )}
                      {notes && (
                        <td className="ct-total">
                          {notes[p.id] ? (
                            <>
                              <span className="pupil-praise" title="Похвалы">
                                +{notes[p.id].praise}
                              </span>{" "}
                              <span className="pupil-remark" title="Замечания">
                                −{notes[p.id].remark}
                              </span>
                            </>
                          ) : (
                            "—"
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
  return embedded ? (
    <section className="course-table-panel course-table-embedded">
      {body}
    </section>
  ) : (
    <details
      className="absence-panel course-table-panel"
      open={expanded}
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Таблица курса</summary>
      {expanded && body}
    </details>
  );
}

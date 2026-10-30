import { useEffect, useMemo, useRef, useState } from "react";
import type { ClipboardEvent, KeyboardEvent } from "react";
import { ExternalLink, Lock } from "lucide-react";
import { api } from "./api";
import CategoryBadge from "./CategoryBadge";
import {
  afterSave,
  applyPaste,
  chunks,
  courseCsv,
  defaultCoursePeriod,
  editKey,
  editable,
  lessonBodies,
  makeEdit,
  markText,
  nextCell,
  parsePaste,
  type CourseList,
  type CourseMark,
  type CourseTable,
  type Edit,
  type Position,
  type SaveResponse,
} from "./courseTable";
import { ruDate } from "./journalCoverage";
import "./CourseTable.css";

type Edits = Record<string, Edit>;
const LEAVE = "В таблице есть несохранённые оценки. Перейти без сохранения?";
const scopeNotes = {
  course:
    "Вы видите черновики своих уроков. Точка в ячейке — оценка пока не опубликована для учеников и родителей.",
  admin: "Администратор видит только опубликованные оценки.",
  class:
    "Классный руководитель видит опубликованные оценки учеников своего класса.",
};

export default function CourseTablePanel({
  workspace,
  onOpen,
}: {
  workspace: string;
  onOpen: (lessonKey: string, day: string) => void;
}) {
  const [expanded, setExpanded] = useState(false),
    [list, setList] = useState<CourseList | null>(null);
  const [course, setCourse] = useState(""),
    [filter, setFilter] = useState("");
  const [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [table, setTable] = useState<CourseTable | null>(null),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false);
  const [edits, setEditsState] = useState<Edits>({}),
    [active, setActive] = useState<Position | null>(null),
    [text, setText] = useState("");
  const [error, setError] = useState(""),
    [note, setNote] = useState(""),
    [problems, setProblems] = useState<{ day: string; message: string }[]>([]);
  const [reload, setReload] = useState(0);
  const editsRef = useRef<Edits>({});
  const loadSeq = useRef(0);
  const input = useRef<HTMLInputElement | null>(null);
  // A request id per request body: a retry after a lost answer replays the stored result instead of saving twice.
  const requestIds = useRef(new Map<string, string>());
  const dirty = Object.keys(edits).length > 0;

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

  useEffect(() => {
    if (!expanded || list) return;
    let live = true;
    api("journal/course-table/courses", workspace)
      .then((value: CourseList) => {
        if (!live) return;
        setList(value);
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
    setTable(null);
    setActive(null);
  }, [workspace, course, start, end]);
  useEffect(() => {
    if (!expanded || !course || !start || !end || end < start) return;
    const [groupId, subjectId] = course.split("|");
    const made = ++loadSeq.current;
    setLoading(true);
    api(
      `journal/course-table?${new URLSearchParams({ groupId, subjectId, start, end })}`,
      workspace,
    )
      .then((value: CourseTable) => {
        if (loadSeq.current !== made) return;
        setTable(value);
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
  }, [expanded, workspace, course, start, end, reload]);

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
  const sameAsSaved = (e: Edit, mark: CourseMark | undefined) =>
    !e.error &&
    e.score === (mark?.score ?? null) &&
    e.label === (mark?.label ?? "");
  const cellText = (pos: Position, from: Edits = edits) => {
    if (!table) return "";
    const pupil = table.pupils[pos.row],
      work = table.works[pos.col];
    const e =
      from[
        editKey({
          studentId: pupil.id,
          lessonKey: table.lessons[work.lesson].lessonKey,
          assessmentId: work.id,
        })
      ];
    return e ? e.raw : markText(pupil.marks[pos.col], work);
  };
  // Records the typed text of a cell; text equal to the saved value is no edit at all.
  function put(from: Edits, pos: Position, raw: string): Edits {
    const pupil = table!.pupils[pos.row],
      work = table!.works[pos.col];
    const edit = makeEdit(raw, pupil, work, table!);
    const next = { ...from };
    if (sameAsSaved(edit, pupil.marks[pos.col])) delete next[editKey(edit)];
    else next[editKey(edit)] = edit;
    return next;
  }
  function commit(pos: Position, value: string): Edits {
    if (!table || !editable(table.pupils[pos.row], table.works[pos.col], table))
      return editsRef.current;
    if (value === cellText(pos, editsRef.current)) return editsRef.current;
    const next = put(editsRef.current, pos, value);
    setEdits(next);
    return next;
  }
  function activate(pos: Position, from: Edits = editsRef.current) {
    setActive(pos);
    setText(cellText(pos, from));
  }
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, [active?.row, active?.col]);

  function move(key: string, e: KeyboardEvent<HTMLInputElement>) {
    if (!active || !table) return;
    const target = nextCell(active, key, table);
    const next = commit(active, text);
    if (target.row === active.row && target.col === active.col) {
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
    else if (e.key === "Escape" && active) {
      e.preventDefault();
      setText(cellText(active));
    } else if (
      (e.key === "Delete" || e.key === "Backspace") &&
      whole &&
      active
    ) {
      // Clearing is always explicit: a blank paste never clears.
      e.preventDefault();
      setText("");
      commit(active, "");
    }
  }
  function paste(e: ClipboardEvent<HTMLInputElement>) {
    if (!active || !table) return;
    e.preventDefault();
    const { edits: pasted, skipped } = applyPaste(
      parsePaste(e.clipboardData.getData("text")),
      active,
      table,
    );
    let next = { ...editsRef.current };
    for (const edit of pasted) {
      const at = {
        row: index.pupils.get(edit.studentId)!,
        col: index.works.get(`${edit.lessonKey}|${edit.assessmentId}`)!,
      };
      next = put(next, at, edit.raw);
    }
    setEdits(next);
    setText(cellText(active, next));
    setNote(skipped ? `Пропущено ячеек: ${skipped}` : "");
  }

  async function submit(publish: boolean) {
    if (!table || busy) return;
    if (active) commit(active, text);
    const bodies = lessonBodies(
      table,
      Object.values(editsRef.current),
      publish,
    );
    if (!bodies.length) return;
    setBusy(true);
    setError("");
    setNote("");
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
    const outcome = afterSave(Object.values(editsRef.current), responses);
    setEdits(Object.fromEntries(outcome.edits.map((e) => [editKey(e), e])));
    const invalid = outcome.edits.filter((e) => e.error).length;
    setActive(null);
    setError(failure);
    setProblems(
      outcome.conflicts.map((c) => ({ day: c.day, message: c.message })),
    );
    setNote(
      `Сохранено уроков: ${outcome.saved}, без изменений: ${outcome.unchanged}, не сохранено: ${outcome.conflicts.length}.` +
        (invalid ? ` Исправьте ячейки с ошибками: ${invalid}.` : ""),
    );
    setBusy(false);
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
    setNote("");
    setProblems([]);
  }
  function choose(change: () => void) {
    if (!leave()) return;
    setEdits({});
    setNote("");
    setProblems([]);
    setError("");
    change();
  }

  const courses = (list?.courses ?? []).filter((c) =>
    `${c.group} ${c.subject} ${c.classes.join(" ")} ${c.teachers.join(" ")}`
      .toLocaleLowerCase()
      .includes(filter.trim().toLocaleLowerCase()),
  );
  const periodId =
    list?.periods.find((p) => p.start === start && p.end === end)?.id ??
    "custom";
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

  return (
    <details
      className="absence-panel course-table-panel"
      open={expanded}
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Таблица курса</summary>
      {expanded && (
        <>
          <p className="ct-noprint">
            Ученики и работы одного курса за период. Оценки вводятся с
            клавиатуры или вставкой из таблицы; темы, домашние задания и
            посещаемость здесь не меняются.
          </p>
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
              <label className="field">
                <span>Период</span>
                <select
                  value={periodId}
                  disabled={busy}
                  onChange={(e) => {
                    const found = list.periods.find(
                      (p) => p.id === e.target.value,
                    );
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
                  {ruDate(table.start)}–{ruDate(table.end)} ·{" "}
                  {table.pupils.length} уч.
                </span>
              </header>
              <p className="info-line ct-noprint">{scopeNotes[table.scope]}</p>
              <div className="ct-actions ct-noprint">
                {table.canEdit && (
                  <>
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
                  disabled={busy || loading}
                  onClick={() => setReload((n) => n + 1)}
                >
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
              {note && (
                <p role="status" className="info-line ct-noprint">
                  {note}
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
                  Итоги и доли показывают сохранённые оценки и пересчитаются
                  после сохранения.
                  {!!orphans &&
                    ` Правок, которых больше нет в таблице: ${orphans}; их можно сбросить.`}
                </p>
              )}
              {!table.works.length ? (
                <p className="empty-state">
                  В этом курсе за период нет работ. Работы добавляются в уроке в
                  журнале дня.
                </p>
              ) : (
                <div className="course-table-scroll">
                  <table className="course-table">
                    <thead>
                      <tr>
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
                                lesson.editable || !table.canEdit
                                  ? ""
                                  : "ct-locked"
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
                              </span>
                              {category && (
                                <CategoryBadge category={category} />
                              )}
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
                        <th scope="col" className="ct-total">
                          Итог по долям, %
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {table.pupils.map((p, row) => (
                        <tr key={p.id}>
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
                            const here =
                              active?.row === row && active.col === col;
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
                              !active;
                            return (
                              <td
                                key={key}
                                title={title}
                                aria-invalid={why ? true : undefined}
                                aria-disabled={can ? undefined : true}
                                tabIndex={
                                  can && (here || entry) ? 0 : undefined
                                }
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
                                  can && !here && activate({ row, col })
                                }
                                onClick={() =>
                                  can && !here && activate({ row, col })
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
                                    onChange={(e) => setText(e.target.value)}
                                    onKeyDown={keyDown}
                                    onPaste={paste}
                                    onBlur={() => commit({ row, col }, text)}
                                  />
                                ) : (
                                  <span>
                                    {edit ? edit.raw : markText(mark, w)}
                                  </span>
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
                            const found = p.categories.find(
                              (x) => x.id === c.id,
                            );
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
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </>
      )}
    </details>
  );
}

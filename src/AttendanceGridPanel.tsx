import { memo, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { attendanceNames } from "./journal";
import { ruDate } from "./journalCoverage";
import {
  candidates,
  cellId,
  clampRange,
  confirmable,
  confirmationName,
  dayTitle,
  marksPayload,
  rangeOf,
  rateText,
  selectLine,
  selectRange,
  shift,
  slots,
  statusMark,
  type Candidate,
  type Cell,
  type Grid,
  type GridDay,
  type GridLesson,
  type GridPupil,
  type Period,
  type View,
  settableMarks,
} from "./attendanceGrid";
import "./AttendanceGrid.css";

type Options = {
  available: boolean;
  admin: boolean;
  today: string;
  yearStart: string;
  yearEnd: string;
  classes: { id: string; name: string; level: string; classTeacher: boolean }[];
  defaultClassId: string;
};
type Line = { studentId?: string; lessonKey?: string; date?: string };
type Trail = { title: string; lines: string[] };
type Ask = { days: { date: string; version: number }[]; text: string };

const views: [View, string][] = [
  ["day", "День"],
  ["week", "Неделя"],
  ["month", "Месяц"],
];
const when = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

type RowProps = {
  pupil: GridPupil;
  lessons: GridLesson[];
  cells: Grid["cells"];
  editable: Set<string>;
  picked: string;
  canSelect: boolean;
  onCell: (cell: Candidate, shift: boolean) => void;
  onLine: (line: { studentId: string }) => void;
  dates: Map<string, string>;
};

// One pupil's row. It re-renders only when its own cells or selection change, so a month stays responsive.
const Row = memo(function Row({
  pupil,
  lessons,
  cells,
  editable,
  picked,
  canSelect,
  onCell,
  onLine,
  dates,
}: RowProps) {
  const chosen = useMemo(() => new Set(picked.split("|")), [picked]);
  const counts = pupil.counts;
  return (
    <tr>
      <th scope="row" className="attendance-pupil">
        {canSelect ? (
          <button
            type="button"
            className="line"
            title="Выбрать все ячейки ученика"
            onClick={() => onLine({ studentId: pupil.id })}
          >
            {pupil.name}
          </button>
        ) : (
          pupil.name
        )}
        <small>
          {rateText(pupil.rates.attendedPercent)}
          {!pupil.active && " · не в классе"}
        </small>
      </th>
      {lessons.map((lesson) => {
        const cell: Cell | undefined = cells[lesson.lessonKey]?.[pupil.id];
        if (!cell)
          return (
            <td
              key={lesson.lessonKey}
              className="attendance-outside"
              aria-label="Ученик не в составе урока"
            />
          );
        const id = cellId({ lessonKey: lesson.lessonKey, studentId: pupil.id });
        const mark = statusMark(cell[0], cell[1]);
        const open = editable.has(id);
        const note = cell[0] === "absent" && cell[1] === "excused";
        const title =
          `${pupil.name}: ${lesson.subject}, ${mark.label}` +
          (cell[1] === "late" && cell[2] ? `, ${cell[2]} мин` : "") +
          (note ? " (по записке)" : "") +
          (cell[3] ? `, тип: ${cell[3]}` : "");
        return (
          <td key={lesson.lessonKey}>
            <button
              type="button"
              className={`attendance-cell tone-${mark.tone}${chosen.has(id) ? " selected" : ""}`}
              aria-pressed={chosen.has(id)}
              aria-label={title}
              title={title}
              disabled={!open}
              onClick={(e) =>
                onCell(
                  {
                    lessonKey: lesson.lessonKey,
                    studentId: pupil.id,
                    date: dates.get(lesson.lessonKey) ?? "",
                    editable: open,
                  },
                  e.shiftKey,
                )
              }
            >
              {mark.letter}
              {cell[1] === "late" && cell[2] > 0 && <small>{cell[2]}</small>}
            </button>
          </td>
        );
      })}
      <td className="attendance-summary-cell">
        {counts.present +
          counts.absent +
          counts.late +
          counts.excused +
          counts.left}
      </td>
      <td className="attendance-summary-cell">{counts.excused}</td>
      <td className="attendance-summary-cell">{counts.absent}</td>
      <td className="attendance-summary-cell">{counts.late}</td>
      <td className="attendance-summary-cell">
        {rateText(pupil.rates.attendedPercent)}
      </td>
    </tr>
  );
});

export default function AttendanceGridPanel({
  workspace,
}: {
  workspace: string;
}) {
  const [expanded, setExpanded] = useState(false),
    [options, setOptions] = useState<Options | null>(null);
  const [classId, setClassId] = useState(""),
    [period, setPeriod] = useState<Period | null>(null);
  const [grid, setGrid] = useState<Grid | null>(null),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [selection, setSelection] = useState<string[]>([]),
    [attendance, setAttendance] = useState("absent"),
    [late, setLate] = useState(5),
    [reason, setReason] = useState("");
  const [trail, setTrail] = useState<Trail | null>(null),
    [ask, setAsk] = useState<Ask | null>(null),
    [revoking, setRevoking] = useState<{
      date: string;
      version: number;
      comment: string;
    } | null>(null);
  // Kept until the server answers: a retry of the same action must not be applied twice.
  const marksId = useRef(crypto.randomUUID()),
    daysId = useRef(crypto.randomUUID());
  const loads = useRef(0),
    anchor = useRef(""),
    tableWrap = useRef<HTMLDivElement | null>(null),
    legend = useRef<HTMLParagraphElement | null>(null),
    breakdown = useRef<HTMLUListElement | null>(null);

  useEffect(() => {
    if (!expanded) return;
    let live = true;
    api("journal/attendance/grid/options", workspace)
      .then((value: Options) => {
        if (!live) return;
        setOptions(value);
        setClassId((old) => old || value.defaultClassId);
        // A phone shows one day; a computer a week.
        const view: View = window.matchMedia?.("(max-width:600px)").matches
          ? "day"
          : "week";
        const anchorDay =
          value.today < value.yearStart
            ? value.yearStart
            : value.today > value.yearEnd
              ? value.yearEnd
              : value.today;
        setPeriod((old) => old || { view, anchor: anchorDay });
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [expanded, workspace]);

  async function load() {
    if (!options || !classId || !period) return;
    const range = clampRange(
      rangeOf(period),
      options.yearStart,
      options.yearEnd,
    );
    const mine = ++loads.current;
    setBusy(true);
    try {
      const value: Grid = await api(
        `journal/attendance/grid?${new URLSearchParams({ classId, ...range })}`,
        workspace,
      );
      if (mine === loads.current) setGrid(value);
    } catch (e) {
      if (mine === loads.current) setError((e as Error).message);
    } finally {
      if (mine === loads.current) setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [options, classId, period?.view, period?.anchor]);
  useEffect(() => {
    // Another class or school starts a new selection.
    setSelection([]);
    setTrail(null);
    setAsk(null);
    setRevoking(null);
    setGrid((old) => (old && old.classId === classId ? old : null));
  }, [classId]);

  const all = useMemo(() => (grid ? candidates(grid) : []), [grid]);
  const byId = useMemo(() => new Map(all.map((c) => [cellId(c), c])), [all]);
  const editable = useMemo(
    () => new Set(all.filter((c) => c.editable).map(cellId)),
    [all],
  );
  const dates = useMemo(
    () => new Map(all.map((c) => [c.lessonKey, c.date])),
    [all],
  );
  // A cell that is no longer open for editing leaves the selection when the grid reloads.
  useEffect(() => {
    setSelection((old) => old.filter((id) => editable.has(id)));
  }, [editable]);

  const handlers = useRef({
    onCell: (_c: Candidate, _s: boolean) => {},
    onLine: (_l: Line) => {},
  });
  handlers.current.onCell = (cell, shiftKey) => {
    const id = cellId(cell);
    if (shiftKey && anchor.current) {
      const order = all.filter((c) => c.editable).map(cellId);
      setSelection((old) => [
        ...new Set([...old, ...selectRange(order, anchor.current, id)]),
      ]);
    } else {
      setSelection((old) =>
        old.includes(id) ? old.filter((x) => x !== id) : [...old, id],
      );
      anchor.current = id;
    }
  };
  handlers.current.onLine = (line) => {
    const found = selectLine(all, line).map(cellId);
    setSelection((old) =>
      found.length && found.every((id) => old.includes(id))
        ? old.filter((id) => !found.includes(id))
        : [...new Set([...old, ...found])],
    );
  };
  const onCell = useRef((c: Candidate, s: boolean) =>
    handlers.current.onCell(c, s),
  ).current;
  const onLine = useRef((l: Line) => handlers.current.onLine(l)).current;

  const lessons = useMemo(
    () =>
      grid ? grid.days.flatMap((d) => slots(d).flatMap((s) => s.lessons)) : [],
    [grid],
  );
  const picked = useMemo(() => {
    const found = new Map<string, string[]>();
    for (const id of selection) {
      const c = byId.get(id);
      if (c) found.set(c.studentId, [...(found.get(c.studentId) ?? []), id]);
    }
    return new Map([...found].map(([k, v]) => [k, v.join("|")]));
  }, [selection, byId]);

  async function apply() {
    if (!grid || !selection.length || busy) return;
    const chosen = selection.flatMap((id) => {
      const c = byId.get(id);
      return c ? [{ lessonKey: c.lessonKey, studentId: c.studentId }] : [];
    });
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api(
        "journal/attendance/grid/marks",
        workspace,
        "POST",
        marksPayload(
          chosen,
          grid,
          attendance,
          late,
          reason,
          marksId.current,
          grid.classId,
        ),
      );
      marksId.current = crypto.randomUUID();
      setSelection([]);
      setReason("");
      setNotice(
        `Изменено ячеек: ${result.changed}` +
          (result.skipped ? `, без изменений: ${result.skipped}` : "") +
          ".",
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
      // A cell changed meanwhile or the access did: show what is true now.
      if ((e as { status?: number }).status === 409) await load();
    } finally {
      setBusy(false);
    }
  }

  async function decide(
    days: { date: string; version: number }[],
    action: "confirm" | "revoke",
    comment = "",
    acknowledge = false,
  ) {
    if (!grid || !days.length || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("journal/attendance/grid/days", workspace, "POST", {
        requestId: daysId.current,
        classId: grid.classId,
        days,
        action,
        comment,
        acknowledge,
      });
      daysId.current = crypto.randomUUID();
      setAsk(null);
      setRevoking(null);
      setNotice(
        action === "confirm"
          ? "Посещаемость подтверждена."
          : "Подтверждение снято.",
      );
      await load();
    } catch (e) {
      const text = (e as Error).message;
      // Lessons without a journal: the class teacher may still confirm once he has checked.
      if (
        action === "confirm" &&
        !acknowledge &&
        (e as { status?: number }).status === 409 &&
        /не заполнено уроков/.test(text)
      )
        setAsk({ days, text });
      else {
        setError(text);
        setAsk(null);
        if ((e as { status?: number }).status === 409) await load();
      }
    } finally {
      setBusy(false);
    }
  }

  async function showCell() {
    if (!grid || selection.length !== 1) return;
    const c = byId.get(selection[0]);
    if (!c) return;
    try {
      const value = await api(
        `journal/attendance/grid/history?${new URLSearchParams({ classId: grid.classId, lessonKey: c.lessonKey, studentId: c.studentId, day: c.date })}`,
        workspace,
      );
      const pupil = grid.pupils.find((p) => p.id === c.studentId);
      setTrail({
        title: `История отметки: ${pupil?.name ?? ""}, ${dayTitle(c.date)}`,
        lines: value.items.map(
          (i: {
            updated: string;
            actor: string;
            attendance: string;
            lateMinutes: number;
          }) =>
            `${when(i.updated)} · ${i.actor}: ${attendanceNames[i.attendance] ?? i.attendance}${i.lateMinutes ? `, ${i.lateMinutes} мин` : ""}`,
        ),
      });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function showDay(day: GridDay) {
    if (!grid) return;
    try {
      const value = await api(
        `journal/attendance/grid/days/history?${new URLSearchParams({ classId: grid.classId, day: day.date })}`,
        workspace,
      );
      setTrail({
        title: `История подтверждения: ${dayTitle(day.date)}`,
        lines: value.items.length
          ? value.items.map(
              (i: {
                created: string;
                actor: string;
                action: string;
                comment: string;
              }) =>
                `${when(i.created)} · ${i.actor}: ${{ confirmed: "подтверждено", revoked: "подтверждение снято", changed: "отметки изменены после подтверждения" }[i.action] ?? i.action}${i.comment ? ` — ${i.comment}` : ""}`,
            )
          : ["Подтверждений не было."],
      });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function print() {
    if (!grid) return;
    const win = window.open("", "_blank");
    if (!win) {
      setError("Разрешите открытие окна для печати.");
      return;
    }
    win.opener = null;
    win.document.title = `Посещаемость ${grid.className}`;
    const title = win.document.createElement("h1");
    title.textContent = `Посещаемость класса ${grid.className}: ${ruDate(grid.start)} — ${ruDate(grid.end)}`;
    win.document.body.appendChild(title);
    for (const node of [tableWrap.current, legend.current, breakdown.current])
      if (node) win.document.body.appendChild(node.cloneNode(true));
    const totals = win.document.createElement("p");
    totals.textContent = `Всего отметок: ${grid.totals.rates.marked}; посещаемость ${rateText(grid.totals.rates.attendedPercent)}; не отмечено ${grid.totals.counts.unmarked}; нет записи ${grid.totals.counts.noRecord}.`;
    win.document.body.appendChild(totals);
    const style = win.document.createElement("style");
    style.textContent =
      "@page{size:landscape;margin:10mm}body{font:12px sans-serif;padding:12px;color:#111}h1{font-size:16px}table{border-collapse:collapse}th,td{border:1px solid #777;padding:2px 4px;text-align:center}th:first-child{text-align:left}small{display:block;font-weight:normal}button{all:unset}.no-print{display:none}.attendance-grid-legend span{margin-right:14px}";
    win.document.head.appendChild(style);
    win.print();
  }

  const level = grid?.level;
  const canEdit = !!grid?.canEdit;
  const range =
    period && options
      ? clampRange(rangeOf(period), options.yearStart, options.yearEnd)
      : null;
  const beyond = (direction: 1 | -1) => {
    if (!period || !options) return true;
    const next = rangeOf(shift(period, direction));
    return next.end < options.yearStart || next.start > options.yearEnd;
  };
  const sorted = grid?.days ?? [];
  const confirmableDays = confirmable(sorted);
  const lessonsOf = (day: GridDay) => slots(day).flatMap((s) => s.lessons);

  return (
    <details
      className="absence-panel attendance-grid"
      open={expanded}
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>Сетка посещаемости класса</summary>
      {expanded && (
        <>
          <p>
            Дни и уроки по ученикам. Показаны опубликованные отметки; состав
            класса берётся на дату урока. Классный руководитель отмечает и
            исправляет посещаемость и подтверждает день после последнего урока
            класса. Оценки, темы и комментарии здесь не видны и не меняются.
          </p>
          {error && (
            <p role="alert" className="error-banner">
              {error}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          {options && !options.available && (
            <p>
              Для вашей учётной записи нет классов, по которым открыта сетка:
              нужен класс с вашими уроками или назначение классным
              руководителем.
            </p>
          )}
          {options?.available && period && range && (
            <>
              <div className="attendance-grid-toolbar">
                <label className="field">
                  <span>Класс</span>
                  <select
                    value={classId}
                    onChange={(e) => setClassId(e.target.value)}
                  >
                    {options.classes.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                        {c.classTeacher ? " (классное руководство)" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <div
                  className="attendance-grid-views"
                  role="group"
                  aria-label="Период"
                >
                  {views.map(([view, name]) => (
                    <button
                      key={view}
                      type="button"
                      aria-pressed={period.view === view}
                      onClick={() => setPeriod({ ...period, view })}
                    >
                      {name}
                    </button>
                  ))}
                </div>
                <label className="field">
                  <span>Дата</span>
                  <input
                    type="date"
                    value={period.anchor}
                    min={options.yearStart}
                    max={options.yearEnd}
                    onChange={(e) =>
                      e.target.value &&
                      setPeriod({ ...period, anchor: e.target.value })
                    }
                  />
                </label>
                <button
                  type="button"
                  disabled={beyond(-1)}
                  onClick={() => setPeriod(shift(period, -1))}
                >
                  Назад
                </button>
                <button
                  type="button"
                  disabled={beyond(1)}
                  onClick={() => setPeriod(shift(period, 1))}
                >
                  Вперёд
                </button>
                <button
                  type="button"
                  disabled={!grid || !grid.days.length}
                  onClick={print}
                >
                  Печать
                </button>
              </div>
              <p role="status">
                {ruDate(range.start)}
                {range.end !== range.start && ` — ${ruDate(range.end)}`}
                {busy && " · загружаю…"}
              </p>
              {grid && level === "own" && (
                <p>
                  Показаны только ваши уроки в этом классе; правка недоступна.
                </p>
              )}
              {grid && level === "read" && (
                <p>
                  Просмотр: правят и подтверждают классный руководитель и
                  администратор.
                </p>
              )}
              {ask && (
                <div role="alert" className="attendance-grid-trail">
                  <p>{ask.text}</p>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void decide(ask.days, "confirm", "", true)}
                  >
                    Подтвердить всё равно
                  </button>{" "}
                  <button type="button" onClick={() => setAsk(null)}>
                    Отмена
                  </button>
                </div>
              )}
              {revoking && (
                <form
                  className="attendance-grid-trail"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void decide(
                      [{ date: revoking.date, version: revoking.version }],
                      "revoke",
                      revoking.comment,
                    );
                  }}
                >
                  <label className="field">
                    <span>
                      Почему снимается подтверждение за{" "}
                      {dayTitle(revoking.date)}
                    </span>
                    <input
                      required
                      maxLength={500}
                      value={revoking.comment}
                      onChange={(e) =>
                        setRevoking({ ...revoking, comment: e.target.value })
                      }
                    />
                  </label>
                  <button disabled={busy || !revoking.comment.trim()}>
                    Снять подтверждение
                  </button>{" "}
                  <button type="button" onClick={() => setRevoking(null)}>
                    Отмена
                  </button>
                </form>
              )}
              {grid && !grid.days.length && (
                <p>В выбранный период у класса нет уроков.</p>
              )}
              {grid && !!grid.days.length && (
                <>
                  {period.view === "week" && canEdit && (
                    <p>
                      <button
                        type="button"
                        disabled={busy || !confirmableDays.length}
                        onClick={() => void decide(confirmableDays, "confirm")}
                      >
                        Подтвердить неделю ({confirmableDays.length})
                      </button>
                    </p>
                  )}
                  <div className="attendance-grid-scroll" ref={tableWrap}>
                    <table
                      className="attendance-grid-table"
                      aria-busy={busy}
                      aria-label={`Посещаемость класса ${grid.className}`}
                    >
                      <thead>
                        <tr>
                          <th rowSpan={2} className="attendance-pupil">
                            Ученик
                            <small>% посещений</small>
                          </th>
                          {grid.days.map((day) => (
                            <th
                              key={day.date}
                              colSpan={lessonsOf(day).length}
                              className="attendance-day-head"
                            >
                              {canEdit ? (
                                <button
                                  type="button"
                                  className="line"
                                  title="Выбрать весь день"
                                  onClick={() => onLine({ date: day.date })}
                                >
                                  {dayTitle(day.date)}
                                </button>
                              ) : (
                                dayTitle(day.date)
                              )}
                              {level !== "own" && (
                                <span
                                  className={`state state-${day.confirmation.status}`}
                                >
                                  {confirmationName(day.confirmation.status)}
                                  {day.confirmation.status !== "none" &&
                                    day.confirmation.actor &&
                                    ` · ${day.confirmation.actor}, ${when(day.confirmation.updated)}`}
                                </span>
                              )}
                              <small>
                                {rateText(day.rates.attendedPercent)}
                              </small>
                              {!!day.locked.length && (
                                <small>
                                  Период закрыт: {day.locked.join(", ")}
                                </small>
                              )}
                              {canEdit && (
                                <span className="no-print">
                                  {day.confirmation.canConfirm &&
                                    day.confirmation.status !== "confirmed" && (
                                      <button
                                        type="button"
                                        disabled={busy}
                                        onClick={() =>
                                          void decide(
                                            [
                                              {
                                                date: day.date,
                                                version:
                                                  day.confirmation.version,
                                              },
                                            ],
                                            "confirm",
                                          )
                                        }
                                      >
                                        Подтвердить день
                                      </button>
                                    )}{" "}
                                  {["confirmed", "changed"].includes(
                                    day.confirmation.status,
                                  ) &&
                                    !day.locked.length && (
                                      <button
                                        type="button"
                                        disabled={busy}
                                        onClick={() =>
                                          setRevoking({
                                            date: day.date,
                                            version: day.confirmation.version,
                                            comment: "",
                                          })
                                        }
                                      >
                                        Снять
                                      </button>
                                    )}{" "}
                                  {day.confirmation.status !== "none" && (
                                    <button
                                      type="button"
                                      onClick={() => void showDay(day)}
                                    >
                                      История
                                    </button>
                                  )}
                                </span>
                              )}
                            </th>
                          ))}
                          <th colSpan={5}>Итого за период</th>
                        </tr>
                        <tr>
                          {lessons.map((lesson) => (
                            <th
                              key={lesson.lessonKey}
                              scope="col"
                              className={`attendance-lesson-head${lesson.status === "cancelled" ? " is-cancelled" : ""}${lesson.archived ? " is-archived" : ""}`}
                            >
                              {canEdit ? (
                                <button
                                  type="button"
                                  className="line"
                                  title={`${lesson.subject}, ${lesson.group}${lesson.teachers ? `, ${lesson.teachers}` : ""}: выбрать весь урок`}
                                  onClick={() =>
                                    onLine({ lessonKey: lesson.lessonKey })
                                  }
                                >
                                  {lesson.start}
                                  <small>{lesson.subject}</small>
                                </button>
                              ) : (
                                <>
                                  {lesson.start}
                                  <small>{lesson.subject}</small>
                                </>
                              )}
                              {lesson.status === "cancelled" && (
                                <small>отменён</small>
                              )}
                              {lesson.archived && <small>архив</small>}
                              {lesson.state !== "published" &&
                                lesson.status !== "cancelled" && (
                                  <small>нет журнала</small>
                                )}
                            </th>
                          ))}
                          <th>Всего</th>
                          <th>По ув. причине</th>
                          <th>Без ув. причины</th>
                          <th>Опоздания</th>
                          <th>%</th>
                        </tr>
                      </thead>
                      <tbody>
                        {grid.pupils.map((pupil) => (
                          <Row
                            key={pupil.id}
                            pupil={pupil}
                            lessons={lessons}
                            cells={grid.cells}
                            editable={editable}
                            picked={picked.get(pupil.id) ?? ""}
                            canSelect={canEdit}
                            onCell={onCell}
                            onLine={onLine}
                            dates={dates}
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {!grid.pupils.length && (
                    <p>В этих уроках нет учеников класса.</p>
                  )}
                  <p className="attendance-grid-legend" ref={legend}>
                    {(
                      [
                        ["present", "+"],
                        ["absent", "Н"],
                        ["late", "О"],
                        ["excused", "У"],
                        ["left", "Р"],
                        ["unmarked", "?"],
                      ] as const
                    ).map(([tone, letter]) => (
                      <span key={tone}>
                        <span className={`attendance-cell tone-${tone}`}>
                          {letter}
                        </span>
                        {attendanceNames[tone]}
                      </span>
                    ))}
                    <span>
                      <span className="attendance-cell tone-none" />
                      Нет записи
                    </span>
                  </p>
                  <ul className="attendance-grid-days" ref={breakdown}>
                    {grid.days.map((d) => (
                      <li key={d.date}>
                        {dayTitle(d.date)}: присутствуют {d.counts.present},
                        отсутствуют {d.counts.absent}, опоздания {d.counts.late}
                        , уважительные {d.counts.excused}, ушли раньше{" "}
                        {d.counts.left}, не отмечено{" "}
                        {d.counts.unmarked}, нет записи {d.counts.noRecord},
                        посещаемость {rateText(d.rates.attendedPercent)}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {trail && (
                <section className="attendance-grid-trail" aria-label="История">
                  <strong>{trail.title}</strong>
                  <ul>
                    {trail.lines.map((line, i) => (
                      <li key={i}>{line}</li>
                    ))}
                  </ul>
                  <button type="button" onClick={() => setTrail(null)}>
                    Закрыть
                  </button>
                </section>
              )}
              {canEdit && !!selection.length && (
                <div
                  className="attendance-grid-actions"
                  role="region"
                  aria-label="Действия с выбранными ячейками"
                >
                  <label className="field">
                    <span>Отметка</span>
                    <select
                      value={attendance}
                      onChange={(e) => setAttendance(e.target.value)}
                    >
                      {settableMarks.map((id) => (
                        <option key={id} value={id}>
                          {attendanceNames[id]}
                        </option>
                      ))}
                    </select>
                  </label>
                  {attendance === "late" && (
                    <label className="field">
                      <span>Минут опоздания</span>
                      <input
                        type="number"
                        min={0}
                        max={600}
                        value={late}
                        onChange={(e) =>
                          setLate(
                            Math.max(
                              0,
                              Math.min(600, Number(e.target.value) || 0),
                            ),
                          )
                        }
                      />
                    </label>
                  )}
                  <label className="field">
                    <span>Причина правки (в журнале аудита)</span>
                    <input
                      maxLength={500}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </label>
                  <button
                    type="button"
                    className="primary"
                    disabled={busy}
                    onClick={() => void apply()}
                  >
                    Применить к {selection.length}
                  </button>
                  {selection.length === 1 && (
                    <button type="button" onClick={() => void showCell()}>
                      История отметки
                    </button>
                  )}
                  <button type="button" onClick={() => setSelection([])}>
                    Снять выбор
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </details>
  );
}

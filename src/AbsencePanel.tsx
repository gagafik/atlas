import { useEffect, useRef, useState } from "react";
import { uploadFile } from "./uploads";
import { api } from "./api";
import AbsenceCodesEditor from "./AbsenceCodesEditor";
import AbsenceNoteView from "./AbsenceNoteView";
import AbsenceSchoolForm from "./AbsenceSchoolForm";
import {
  BulkPreviewTable,
  ClassesTable,
  LessonsTable,
  NotesTable,
} from "./AbsenceTables";
import {
  type AbsenceCode,
  type PreviewLesson,
  type SchoolPupil,
  type Subject,
  defaultChild,
  formLocks,
  lessonEdge,
  noticeQuery,
  notesOff as closed,
} from "./absenceForm";
import {
  ACTIONS,
  BULK_LIMIT,
  STATUS_LABELS,
  allPicked,
  composeLabel,
  factsLine,
  filedBy,
  pickEligible,
  tabsFor,
  type AbsenceNote,
  type ClassSetting,
  type NoteFile,
  type Tab,
} from "./absenceNotes";
import "./AbsenceNotes.css";

type FileItem = NoteFile;
type Item = AbsenceNote;
type Options = {
  parent: boolean;
  pupil?: boolean;
  admin: boolean;
  canReview: boolean;
  codes: AbsenceCode[];
  subjects: Subject[];
  schoolPupils?: SchoolPupil[];
  students: {
    id: string;
    name: string;
    electronicNotes?: boolean;
    classTeacher?: string | null;
  }[];
  classSettings?: ClassSetting[];
  yearStart: string;
  yearEnd: string;
  defaultDate: string;
  config?: { version: number; assignments: Record<string, string[]> };
  classes?: { id: string; name: string }[];
  teachers?: { id: string; name: string; login: string }[];
};
type History = {
  id: number;
  version: number;
  created: string;
  actor: string;
  action: string;
  comment: string;
  batchId?: string | null;
};
type BulkPreview = {
  id: string;
  expires: string;
  action: string;
  comment: string;
  items: (Item & { nextStatus: string })[];
};
// One view at a time replaces the others in place; the tabs belong to the list view.
type View = "list" | "note" | "school" | "compose" | "bulk";

// A heading that can take the focus: forms bring their own heading, which gets its tabindex here.
const headingOf = (box: HTMLElement | null | undefined) => {
  const heading = box?.querySelector<HTMLElement>("h3");
  heading?.setAttribute("tabindex", "-1");
  return heading;
};

export default function AbsencePanel({
  workspace,
  defaultOpen = false,
  studentId,
  pinned = false,
  compose = false,
}: {
  workspace: string;
  // Opened from the menu: the panel starts expanded.
  defaultOpen?: boolean;
  // The child the parent form starts with, when the account has that child.
  studentId?: string;
  // The portal looks at that child: the form is for them and offers no other.
  pinned?: boolean;
  // Opened to write a new note: the form is scrolled into view and focused once the options are here.
  compose?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultOpen),
    [options, setOptions] = useState<Options | null>(null),
    [busy, setBusy] = useState(false),
    [serverBusy, setServerBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [items, setItems] = useState<Item[]>([]),
    [next, setNext] = useState<number | null>(null),
    [filter, setFilter] = useState("all"),
    [selected, setSelected] = useState<Item | null>(null);
  const [history, setHistory] = useState<History[]>([]),
    [historyNext, setHistoryNext] = useState<number | null>(null),
    [comment, setComment] = useState("");
  const [student, setStudent] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [from, setFrom] = useState("00:00"),
    [to, setTo] = useState("23:59"),
    [reason, setReason] = useState(""),
    [codeId, setCodeId] = useState(""),
    [subjectId, setSubjectId] = useState("");
  // Filters of the reviewer queue; any change reloads the first page.
  const [fClass, setFClass] = useState(""),
    [fFrom, setFFrom] = useState(""),
    [fTo, setFTo] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID()),
    [files, setFiles] = useState<FileItem[]>([]),
    [available, setAvailable] = useState<FileItem[]>([]);
  const [preview, setPreview] = useState<
    (PreviewLesson & { group: string })[] | null
  >(null);
  const [decisionRequest, setDecisionRequest] = useState(() =>
    crypto.randomUUID(),
  );
  const [bulkIds, setBulkIds] = useState<string[]>([]),
    [bulkAction, setBulkAction] = useState("approve"),
    [bulkComment, setBulkComment] = useState(""),
    [bulkPreview, setBulkPreview] = useState<BulkPreview | null>(null),
    [bulkRequest, setBulkRequest] = useState(() => crypto.randomUUID());
  // The compose form starts the panel when asked; the school form and the types editor mount when first shown.
  const [view, setView] = useState<View>(compose ? "compose" : "list"),
    [tab, setTab] = useState<Tab>("notes"),
    [schoolSeen, setSchoolSeen] = useState(false),
    [codesSeen, setCodesSeen] = useState(false);
  // One request id per class, version and direction, so a repeated click or retry is the same switch.
  const switchIds = useRef<Record<string, string>>({}),
    composeForm = useRef<HTMLFormElement | null>(null),
    composed = useRef(false);
  // Where the focus goes once the next view is on screen; one shot, so a later render never steals it.
  const root = useRef<HTMLDivElement | null>(null),
    schoolBox = useRef<HTMLDivElement | null>(null),
    noteHeading = useRef<HTMLHeadingElement | null>(null),
    bulkHeading = useRef<HTMLHeadingElement | null>(null),
    focusOn = useRef<(() => HTMLElement | null | undefined) | null>(null);
  useEffect(() => {
    const find = focusOn.current;
    if (!find) return;
    focusOn.current = null;
    find()?.focus();
  });
  useEffect(() => {
    if (!compose || !options?.parent || composed.current) return;
    composed.current = true;
    composeForm.current?.scrollIntoView({ block: "start" });
    composeForm.current
      ?.querySelector<HTMLElement>("select, input, textarea")
      ?.focus({ preventScroll: true });
  }, [compose, options]);
  // The portal asks again for the form after the panel has left it: it opens on the form without a remount, so the text stays.
  useEffect(() => {
    const again = () => {
      setExpanded(true);
      setView("compose");
      focusOn.current = () => {
        composeForm.current?.scrollIntoView({ block: "start" });
        return composeForm.current?.querySelector<HTMLElement>(
          "select, input, textarea",
        );
      };
    };
    window.addEventListener("atlas-absence-compose", again);
    return () => window.removeEventListener("atlas-absence-compose", again);
  }, []);
  // Asked to open while it is on show (the launcher): the section opens.
  useEffect(() => {
    if (defaultOpen) setExpanded(true);
  }, [defaultOpen]);
  // The portal asks before it changes the child or the screen while a note is half written.
  const unsent = !!(reason || files.length);
  useEffect(() => {
    if (!unsent) return;
    const leave = (e: Event) => {
      if (!window.confirm("Неотправленная записка будет потеряна. Продолжить?"))
        e.preventDefault();
    };
    window.addEventListener("atlas-homework-leave", leave);
    return () => window.removeEventListener("atlas-homework-leave", leave);
  }, [unsent]);
  const child = options?.students.find((s) => s.id === student);
  const notesOff = closed(child);
  const locks = formLocks(busy, child);
  // Parents choose only types marked for them; with none the field is hidden and the note has no type.
  const parentCodes = options?.codes.filter((c) => c.active && c.parents) ?? [];
  const clearBulkPreview = () => {
    setBulkPreview(null);
    setBulkRequest(crypto.randomUUID());
  };
  const clearBulk = () => {
    clearBulkPreview();
    setBulkIds([]);
  };
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const loadOptions = async () => {
    const r: Options = await api("journal/absences/options", workspace);
    setOptions(r);
    setStudent((old) => defaultChild(r.students, old || studentId || ""));
    setStart((old) => old || r.defaultDate);
    setEnd((old) => old || r.defaultDate);
  };
  const query = (before?: number) =>
    noticeQuery({
      status: filter,
      classId: options?.canReview ? fClass : "",
      start: fFrom,
      end: fTo,
      before,
    });
  const list = async (before?: number) => {
    const r = await api(`journal/absences?${query(before)}`, workspace);
    setItems((old) => (before ? [...old, ...r.items] : r.items));
    setNext(r.nextBefore);
    if (!before) clearBulk();
  };
  // `enter`: the note replaces the list and takes the focus; a refresh of the open note stays where it is.
  const open = async (id: string, enter = false) => {
    const r = await api(`journal/absences/${id}`, workspace);
    setSelected(r);
    setHistory([]);
    setHistoryNext(null);
    setComment("");
    setDecisionRequest(crypto.randomUUID());
    if (enter) showNote();
  };
  const showNote = () => {
    setView("note");
    focusOn.current = () => noteHeading.current;
  };
  const changes = async (before?: number) => {
    if (!selected) return;
    const r = await api(
      `journal/absences/${selected.id}/history${before ? `?before=${before}` : ""}`,
      workspace,
    );
    setHistory((old) => (before ? [...old, ...r.items] : r.items));
    setHistoryNext(r.nextBefore);
  };
  useEffect(() => {
    if (!expanded) return;
    clearBulk();
    let live = true;
    setBusy(true);
    setError("");
    Promise.all([
      api("journal/absences/options", workspace),
      api(
        `journal/absences?${noticeQuery({ status: filter, classId: fClass, start: fFrom, end: fTo })}`,
        workspace,
      ),
    ])
      .then(([o, r]) => {
        if (!live) return;
        setOptions(o);
        setStudent((old) => defaultChild(o.students, old || studentId || ""));
        setStart((old) => old || o.defaultDate);
        setEnd((old) => old || o.defaultDate);
        setItems(r.items);
        setNext(r.nextBefore);
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
  }, [expanded, workspace, filter, fClass, fFrom, fTo]);
  const payload = () => ({
    requestId,
    studentId: student,
    startDate: start,
    endDate: end,
    startTime: from,
    endTime: to,
    reason,
    codeId,
    subjectId,
    fileIds: files.map((f) => f.id),
  });
  const reset = () => {
    setRequestId(crypto.randomUUID());
    setFiles([]);
    setAvailable([]);
    setReason("");
    setCodeId("");
    setSubjectId("");
    setPreview(null);
  };
  // The first or the last lesson of the note is picked from the preview, no times to type.
  const edge = (lesson: PreviewLesson, which: "start" | "end") => {
    const { date, time } = lessonEdge(lesson, which);
    if (which === "start") {
      setStart(date);
      setFrom(time);
      if (end < date) setEnd(date);
    } else {
      setEnd(date);
      setTo(time);
      if (start > date) setStart(date);
    }
    setPreview(null);
  };
  const upload = async (file: File) => {
    if (files.length >= 5)
      throw new Error("К заявлению можно приложить до пяти файлов.");
    const query = new URLSearchParams({
      workspace,
      studentId: student,
      draftId: requestId,
      filename: file.name,
    });
    const value = await uploadFile(
      `/api/journal/absence-files?${query}`,
      file,
      {
        onBusy: setServerBusy,
        fallback: "Не удалось загрузить файл.",
      },
    );
    setFiles((old) => [...old, value]);
  };
  const decide = async (action: string) => {
    if (
      !selected ||
      !window.confirm(
        action === "approve"
          ? "Принять объяснительную? Отсутствие на полностью включённых в период уроках будет учитываться как уважительная причина."
          : action === "withdraw"
            ? "Отозвать заявление? Школа увидит, что оно отозвано."
            : "Сохранить это решение с указанным объяснением?",
      )
    )
      return;
    const r = await api(
      `journal/absences/${selected.id}/decision`,
      workspace,
      "POST",
      {
        requestId: decisionRequest,
        version: selected.version,
        action,
        comment,
      },
    );
    setSelected(r);
    setComment("");
    setDecisionRequest(crypto.randomUUID());
    await changes();
    await list();
    setNotice(
      "Решение сохранено и учитывается при следующем просмотре посещаемости.",
    );
  };
  const switchNotes = async (c: ClassSetting) => {
    const enabled = !c.enabled;
    if (
      !window.confirm(
        enabled
          ? `Включить электронные записки для класса ${c.name}? Родители снова смогут отправлять объяснительные.`
          : `Выключить электронные записки для класса ${c.name}? Родители не смогут отправлять новые заявления и загружать файлы. Поданные заявления можно рассматривать и отзывать.`,
      )
    )
      return;
    const key = `${c.id}:${c.version}:${enabled}`;
    const requestId = (switchIds.current[key] ||= crypto.randomUUID());
    await api(
      `journal/absences/classes/${encodeURIComponent(c.id)}/electronic`,
      workspace,
      "PUT",
      { requestId, version: c.version, enabled },
    );
    await loadOptions();
    setNotice(
      enabled
        ? `Электронные записки класса ${c.name} включены.`
        : `Электронные записки класса ${c.name} выключены.`,
    );
  };
  const fileUrl = (id: string) =>
    `/api/journal/absence-files/${id}?workspace=${encodeURIComponent(workspace)}`;
  const bulkDone = async (count: number) => {
    clearBulk();
    setBulkComment("");
    setSelected(null);
    setHistory([]);
    setView("list");
    focusOn.current = () => firstAction();
    setNotice(
      `Сохранено решений: ${count}. Они учитываются при следующем просмотре посещаемости.`,
    );
    await list();
  };
  const prepareBulk = async () => {
    const r = await api("journal/absences/bulk/preview", workspace, "POST", {
      requestId: bulkRequest,
      items: items
        .filter((item) => bulkIds.includes(item.id))
        .map(({ id, version }) => ({ id, version })),
      action: bulkAction,
      comment: bulkComment,
    });
    if (r.status === "applied") await bulkDone(r.count);
    else {
      setBulkPreview(r);
      setView("bulk");
      focusOn.current = () => bulkHeading.current;
    }
  };
  const applyBulk = async () => {
    if (
      !bulkPreview ||
      !window.confirm(
        `Применить решение «${ACTIONS[bulkPreview.action]}» ко всем ${bulkPreview.items.length} заявлениям из предпросмотра?`,
      )
    )
      return;
    const r = await api(
      `journal/absences/bulk/${bulkPreview.id}/apply`,
      workspace,
      "POST",
    );
    await bulkDone(r.count);
  };
  const byAttr = (attr: string, value: string) =>
    Array.from(
      root.current?.querySelectorAll<HTMLElement>(`[${attr}]`) ?? [],
    ).find((el) => el.getAttribute(attr) === value);
  const firstAction = () =>
    root.current?.querySelector<HTMLElement>(".absence-notes-actions button");
  const tabButton = () =>
    root.current?.querySelector<HTMLElement>('[data-tab="notes"]');
  // Back to the list: the focus returns to the button that opened the view, or to the nearest thing that is still there.
  const goBack = (from: View) => {
    const id = selected?.id ?? "";
    focusOn.current = () =>
      (from === "note"
        ? byAttr("data-note-open", id)
        : from === "bulk"
          ? byAttr("data-focus", "prepare")
          : byAttr("data-focus", from === "school" ? "school" : "compose")) ??
      tabButton() ??
      firstAction();
    setView("list");
  };
  const selectTab = (id: Tab) => {
    setTab(id);
    if (id === "codes") setCodesSeen(true);
    setView("list");
  };
  const classSettings = options?.classSettings ?? [];
  const viewer = { parent: !!options?.parent, reviewer: !!options?.canReview };
  const tabs = tabsFor(options);
  const shownTab = tabs.some(([id]) => id === tab) ? tab : "notes";
  // A view whose data is gone (no open note, no prepared decision) falls back to the list.
  const shown: View =
    (view === "note" && !selected) ||
    (view === "bulk" && !bulkPreview) ||
    (view === "school" && !options?.canReview) ||
    (view === "compose" && options && !options.parent)
      ? "list"
      : view;
  return (
    <details
      className="absence-panel"
      open={expanded}
      onToggle={(e) => {
        const open = e.currentTarget.open;
        setExpanded(open);
        // Collapsing unmounts the content: the types list is asked for again only when its tab is shown again.
        if (!open) {
          setCodesSeen(false);
          setTab("notes");
        }
      }}
    >
      <summary>Записки об отсутствии</summary>
      {expanded && (
        <div className="absence-notes" ref={root}>
          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          <details className="absence-notes-help">
            <summary>Кто что видит</summary>
            <p>
              Родитель сообщает причину, школа рассматривает заявление или
              вносит записку сама. Причину, файлы и историю видят автор,
              законные представители с подтверждённой связью в семейном реестре,
              администратор и назначенные ответственные за класс. Ученик и
              остальные представители видят только тип, период, предмет и статус
              записок, внесённых школой, а ученик ещё и записок своей семьи.
              Предметникам виден только результат в посещаемости.
            </p>
            {options?.admin && (
              <p>
                Назначение ответственных: «Пользователи и доступ» → «Права по
                классам». Рассмотрение объяснительных, сводная посещаемость и
                контроль журналов выдаются отдельно.
              </p>
            )}
            {options?.canReview && (
              <p>
                Для общего решения выберите до {BULK_LIMIT} заявлений в таблице.
                Одно объяснение решения сохранится в истории каждого. Родители
                увидят только своё заявление.
              </p>
            )}
          </details>
          {options &&
            !options.parent &&
            !options.canReview &&
            !options.pupil && (
              <p>
                Администратор пока не назначил вас ответственным за заявления
                класса.
              </p>
            )}
          {shown === "list" && (
            <>
              {tabs.length > 1 && (
                <div
                  className="segmented absence-notes-tabs"
                  role="group"
                  aria-label="Разделы записок"
                >
                  {tabs.map(([id, name]) => (
                    <button
                      key={id}
                      type="button"
                      data-tab={id}
                      className={shownTab === id ? "selected" : ""}
                      aria-pressed={shownTab === id}
                      onClick={() => selectTab(id)}
                    >
                      {name} {id === "notes" && <span>{items.length}</span>}
                    </button>
                  ))}
                </div>
              )}
              {shownTab === "notes" && (
                <>
                  <div className="absence-notes-actions">
                    {options?.canReview && (
                      <button
                        className="primary"
                        data-focus="school"
                        onClick={() => {
                          setSchoolSeen(true);
                          setView("school");
                          focusOn.current = () => headingOf(schoolBox.current);
                        }}
                      >
                        Внести записку
                      </button>
                    )}
                    {options?.parent && (
                      <button
                        className="primary"
                        data-focus="compose"
                        onClick={() => {
                          setView("compose");
                          focusOn.current = () =>
                            headingOf(composeForm.current);
                        }}
                      >
                        {composeLabel(unsent)}
                      </button>
                    )}
                    <button
                      aria-label="Обновить записки"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          setSelected(null);
                          await loadOptions();
                          await list();
                        })
                      }
                    >
                      Обновить
                    </button>
                  </div>
                  <div className="absence-notes-filters">
                    <label className="absence-notes-field">
                      <span>Статус</span>
                      <select
                        disabled={busy}
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                      >
                        <option value="all">Все</option>
                        {Object.entries(STATUS_LABELS)
                          .filter(
                            ([id]) => !["submitted", "recorded"].includes(id),
                          )
                          .map(([id, label]) => (
                            <option key={id} value={id}>
                              {label}
                            </option>
                          ))}
                      </select>
                    </label>
                    {options?.canReview && (
                      <>
                        <label className="absence-notes-field">
                          <span>Класс</span>
                          <select
                            disabled={busy}
                            value={fClass}
                            onChange={(e) => setFClass(e.target.value)}
                          >
                            <option value="">Все мои классы</option>
                            {options.classSettings?.map((c) => (
                              <option key={c.id} value={c.id}>
                                {c.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="absence-notes-field">
                          <span>Период с</span>
                          <input
                            type="date"
                            disabled={busy}
                            value={fFrom}
                            onChange={(e) => setFFrom(e.target.value)}
                          />
                        </label>
                        <label className="absence-notes-field">
                          <span>Период по</span>
                          <input
                            type="date"
                            disabled={busy}
                            min={fFrom}
                            value={fTo}
                            onChange={(e) => setFTo(e.target.value)}
                          />
                        </label>
                      </>
                    )}
                  </div>
                  {options?.canReview && (
                    <fieldset
                      disabled={busy}
                      className="access-fields absence-notes-bulk"
                      aria-label="Общее решение по запискам"
                    >
                      <div className="absence-notes-bulk-bar">
                        <label className="absence-notes-field">
                          <span>Общее решение</span>
                          <select
                            value={bulkAction}
                            onChange={(e) => {
                              setBulkAction(e.target.value);
                              clearBulk();
                            }}
                          >
                            {Object.entries(ACTIONS).map(([id, label]) => (
                              <option key={id} value={id}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <span className="absence-notes-count">
                          Выбрано {bulkIds.length} из {BULK_LIMIT}
                        </span>
                        <button disabled={!bulkIds.length} onClick={clearBulk}>
                          Сбросить выбор
                        </button>
                      </div>
                      {bulkIds.length > 0 && (
                        <div className="absence-notes-bulk-more">
                          <label className="absence-notes-field">
                            <span>Объяснение общего решения</span>
                            <textarea
                              rows={2}
                              value={bulkComment}
                              maxLength={2000}
                              onChange={(e) => {
                                setBulkComment(e.target.value);
                                clearBulkPreview();
                              }}
                            />
                          </label>
                          <button
                            data-focus="prepare"
                            disabled={!bulkComment.trim()}
                            onClick={() => void run(prepareBulk)}
                          >
                            Подготовить предпросмотр
                          </button>
                        </div>
                      )}
                    </fieldset>
                  )}
                  <NotesTable
                    items={items}
                    codes={options?.codes ?? []}
                    subjects={options?.subjects ?? []}
                    viewer={viewer}
                    loading={busy}
                    busy={busy}
                    pick={
                      options?.canReview
                        ? {
                            ids: bulkIds,
                            action: bulkAction,
                            onToggle: (id, checked) => {
                              setBulkIds((old) =>
                                checked
                                  ? [...old, id]
                                  : old.filter((x) => x !== id),
                              );
                              clearBulkPreview();
                            },
                            onToggleAll: () => {
                              if (allPicked(items, bulkIds, bulkAction))
                                clearBulk();
                              else {
                                setBulkIds(pickEligible(items, bulkAction));
                                clearBulkPreview();
                              }
                            },
                          }
                        : undefined
                    }
                    onOpen={(n) => void run(() => open(n.id, true))}
                  />
                  {next !== null && (
                    <button
                      className="absence-notes-more"
                      disabled={busy}
                      onClick={() => void run(() => list(next))}
                    >
                      Предыдущие записки
                    </button>
                  )}
                </>
              )}
              {shownTab === "classes" && classSettings.length > 0 && (
                <>
                  <p className="muted">
                    Классный руководитель или администратор включает и выключает
                    приём новых заявлений от родителей; поданные заявления и
                    файлы остаются доступными.
                  </p>
                  <ClassesTable
                    classes={classSettings}
                    busy={busy}
                    onSwitch={(c) => void run(() => switchNotes(c))}
                  />
                </>
              )}
            </>
          )}
          {options?.admin && codesSeen && (
            <div hidden={!(shown === "list" && shownTab === "codes")}>
              <AbsenceCodesEditor
                bare
                workspace={workspace}
                onSaved={() => void loadOptions()}
              />
            </div>
          )}
          {shown === "note" && selected && (
            <AbsenceNoteView
              note={selected}
              facts={factsLine(selected, {
                codes: options?.codes ?? [],
                subjects: options?.subjects ?? [],
                filedBy: filedBy(
                  {
                    origin: selected.origin,
                    mine:
                      selected.mine ??
                      items.find((x) => x.id === selected.id)?.mine,
                  },
                  viewer,
                ),
              })}
              busy={busy}
              comment={comment}
              history={history}
              historyNext={historyNext}
              headingRef={noteHeading}
              fileUrl={fileUrl}
              onBack={() => goBack("note")}
              onComment={(value) => {
                setComment(value);
                setDecisionRequest(crypto.randomUUID());
              }}
              onDecide={(action) => void run(() => decide(action))}
              onRefresh={() => void run(() => open(selected.id))}
              onHistory={(before) => void run(() => changes(before))}
            />
          )}
          {options?.canReview && schoolSeen && (
            <div
              className="absence-notes-view"
              ref={schoolBox}
              hidden={shown !== "school"}
            >
              <button
                type="button"
                className="absence-notes-back"
                onClick={() => goBack("school")}
              >
                ← К запискам
              </button>
              <AbsenceSchoolForm
                workspace={workspace}
                classes={options.classSettings ?? []}
                pupils={options.schoolPupils ?? []}
                codes={options.codes}
                subjects={options.subjects}
                yearStart={options.yearStart}
                yearEnd={options.yearEnd}
                defaultDate={options.defaultDate}
                onDone={() => void run(list)}
              />
            </div>
          )}
          {shown === "compose" && options?.parent && (
            <div className="absence-notes-view">
              <button
                type="button"
                className="absence-notes-back"
                onClick={() => goBack("compose")}
              >
                ← К запискам
              </button>
              <form
                ref={composeForm}
                id="absence-compose"
                className="absence-notes-form absence-notes-compose"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (
                    !window.confirm(
                      "Передать школе причину отсутствия и выбранные файлы? После отправки их нельзя заменить; ожидающее заявление можно отозвать.",
                    )
                  )
                    return;
                  void run(async () => {
                    const r = await api(
                      "journal/absences",
                      workspace,
                      "POST",
                      payload(),
                    );
                    setSelected(r);
                    setHistory([]);
                    showNote();
                    reset();
                    await list();
                    setNotice("Заявление отправлено школе.");
                  });
                }}
              >
                <h3>Сообщить об отсутствии</h3>
                {notesOff && (
                  <p role="status">
                    Электронные записки для класса выключены. Свяжитесь с
                    классным руководителем
                    {child?.classTeacher ? `: ${child.classTeacher}` : ""}.
                  </p>
                )}
                <fieldset disabled={locks.pick} className="access-fields">
                  {pinned && !!child && child.id === studentId ? (
                    <p className="info-line">
                      Ребёнок: <strong>{child.name}</strong>
                    </p>
                  ) : (
                    <label className="field">
                      <span>Ребёнок</span>
                      <select
                        value={student}
                        onChange={(e) => {
                          if (
                            (reason || files.length) &&
                            !window.confirm(
                              "Начать форму для другого ребёнка? Неотправленный текст будет очищен.",
                            )
                          )
                            return;
                          setStudent(e.target.value);
                          reset();
                        }}
                      >
                        {options.students.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {/* Notes off for this class: only what writes a new notice is locked. */}
                  <fieldset disabled={locks.write} className="access-fields">
                    <div className="absence-notes-dates">
                      <label className="field">
                        <span>Первый день отсутствия</span>
                        <input
                          type="date"
                          required
                          min={options.yearStart}
                          max={options.yearEnd}
                          value={start}
                          onChange={(e) => {
                            setStart(e.target.value);
                            setPreview(null);
                          }}
                        />
                      </label>
                      <label className="field">
                        <span>С какого времени</span>
                        <input
                          type="time"
                          required
                          value={from}
                          onChange={(e) => {
                            setFrom(e.target.value);
                            setPreview(null);
                          }}
                        />
                      </label>
                      <label className="field">
                        <span>Последний день отсутствия</span>
                        <input
                          type="date"
                          required
                          min={start}
                          max={options.yearEnd}
                          value={end}
                          onChange={(e) => {
                            setEnd(e.target.value);
                            setPreview(null);
                          }}
                        />
                      </label>
                      <label className="field">
                        <span>До какого времени</span>
                        <input
                          type="time"
                          required
                          value={to}
                          onChange={(e) => {
                            setTo(e.target.value);
                            setPreview(null);
                          }}
                        />
                      </label>
                    </div>
                    <details className="absence-notes-help">
                      <summary>Как указать период</summary>
                      <p>
                        Время школы — Asia/Qyzylorda. Для полных дней оставьте
                        00:00–23:59. Период непрерывный, до 31 дня; учитываются
                        только полностью включённые в него уроки. Перенос урока
                        за пределы периода отменяет применение объяснительной к
                        нему. Чтобы указать первый и последний урок, покажите
                        уроки в периоде и нажмите «С этого урока» и «По этот
                        урок».
                      </p>
                    </details>
                    <div className="absence-notes-pair">
                      {parentCodes.length > 0 && (
                        <label className="field">
                          <span>Тип отсутствия</span>
                          <select
                            required
                            value={codeId}
                            onChange={(e) => setCodeId(e.target.value)}
                          >
                            <option value="">Выберите тип</option>
                            {parentCodes.map((c) => (
                              <option key={c.id} value={c.id}>
                                {c.name}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                      <label className="field">
                        <span>Предмет</span>
                        <select
                          value={subjectId}
                          onChange={(e) => {
                            setSubjectId(e.target.value);
                            setPreview(null);
                          }}
                        >
                          <option value="">Все предметы</option>
                          {options.subjects.map((x) => (
                            <option key={x.id} value={x.id}>
                              {x.name}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <label className="field">
                      <span>Причина отсутствия</span>
                      <textarea
                        required
                        rows={3}
                        maxLength={4000}
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                      />
                    </label>
                  </fieldset>
                  <div className="absence-notes-filerow">
                    <fieldset disabled={locks.write} className="access-fields">
                      <label className="field">
                        <span>
                          Приложить файл (необязательно, до 5 по 10 МиБ)
                        </span>
                        <input
                          type="file"
                          accept=".pdf,.png,.jpg,.jpeg,.txt,.docx,.xlsx,.pptx"
                          disabled={!student || files.length >= 5}
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            e.target.value = "";
                            if (f) void run(() => upload(f));
                          }}
                        />
                        {serverBusy && (
                          <small role="status">Сервер занят, повторяем…</small>
                        )}
                      </label>
                    </fieldset>
                    <fieldset disabled={locks.write} className="access-fields">
                      <button
                        type="button"
                        disabled={!student}
                        onClick={() =>
                          void run(async () => {
                            const r = await api(
                              `journal/absence-files?${new URLSearchParams({ studentId: student, draftId: requestId })}`,
                              workspace,
                            );
                            setAvailable(r.items);
                          })
                        }
                      >
                        Найти загрузки этой формы
                      </button>
                    </fieldset>
                  </div>
                  {files.map((f) => (
                    <div className="absence-file" key={f.id}>
                      <a href={fileUrl(f.id)}>{f.filename}</a>
                      <button
                        type="button"
                        onClick={() =>
                          void run(async () => {
                            await api(
                              `journal/absence-files/${f.id}`,
                              workspace,
                              "DELETE",
                            );
                            setFiles((old) => old.filter((x) => x.id !== f.id));
                          })
                        }
                      >
                        Удалить неотправленный файл
                      </button>
                    </div>
                  ))}
                  <fieldset disabled={locks.write} className="access-fields">
                    {available
                      .filter((f) => !files.some((x) => x.id === f.id))
                      .map((f) => (
                        <div className="absence-file" key={f.id}>
                          <span>{f.filename}</span>
                          <button
                            type="button"
                            disabled={files.length >= 5}
                            onClick={() => setFiles((old) => [...old, f])}
                          >
                            Приложить найденный файл
                          </button>
                        </div>
                      ))}
                  </fieldset>
                  <p className="muted absence-notes-small">
                    Неотправленный текст не сохраняется после закрытия формы.
                    Неиспользованные файлы очищаются через сутки при следующих
                    загрузках.
                  </p>
                  <div className="absence-notes-buttons">
                    <button
                      type="button"
                      disabled={!student || !reason.trim() || locks.write}
                      onClick={() =>
                        void run(async () => {
                          const r = await api(
                            "journal/absences/preview",
                            workspace,
                            "POST",
                            payload(),
                          );
                          setPreview(r.lessons);
                        })
                      }
                    >
                      Показать уроки в периоде
                    </button>
                    <button
                      className="primary"
                      type="submit"
                      disabled={
                        !student ||
                        !reason.trim() ||
                        locks.write ||
                        (parentCodes.length > 0 && !codeId)
                      }
                    >
                      Отправить объяснительную
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm("Очистить неотправленную форму?"))
                          reset();
                      }}
                    >
                      Новая форма
                    </button>
                  </div>
                  {preview && (
                    <div className="absence-preview">
                      <p>
                        Уроков по текущему расписанию: {preview.length}. Решение
                        будет действовать в указанный период и для добавленных
                        позднее уроков.
                      </p>
                      <LessonsTable lessons={preview} onEdge={edge} />
                    </div>
                  )}
                </fieldset>
              </form>
            </div>
          )}
          {shown === "bulk" && bulkPreview && (
            <section
              className="absence-notes-note"
              aria-label="Предпросмотр общего решения"
            >
              <button
                type="button"
                className="absence-notes-back"
                onClick={() => goBack("bulk")}
              >
                ← К запискам
              </button>
              <h3
                tabIndex={-1}
                ref={bulkHeading}
                className="absence-notes-title"
              >
                Проверьте общее решение
              </h3>
              <p>
                <strong>
                  {ACTIONS[bulkPreview.action]} · {bulkPreview.items.length}{" "}
                  заявлений
                </strong>
              </p>
              <p className="journal-text">{bulkPreview.comment}</p>
              <p>
                {bulkPreview.action === "approve"
                  ? "Отсутствие на полностью включённых в период уроках будет учитываться как уважительная причина, в том числе в журналах, созданных позже."
                  : bulkPreview.action === "revoke"
                    ? "Эти объяснительные перестанут подтверждать уважительную причину. Исходные отметки учителей снова определят посещаемость."
                    : "Заявления будут отклонены. Посещаемость останется прежней."}{" "}
                Оценки, исходные отметки и публикация журналов сохраняются.
              </p>
              <BulkPreviewTable items={bulkPreview.items} fileUrl={fileUrl} />
              <p>
                Предпросмотр действителен до{" "}
                {new Date(bulkPreview.expires).toLocaleTimeString("ru-RU")}.
                Если заявления или доступы изменились, ни одно решение не
                сохранится. После ошибки соединения можно повторить применение:
                решения не продублируются.
              </p>
              <button
                className="primary"
                disabled={busy}
                onClick={() => void run(applyBulk)}
              >
                Применить ко всем ({bulkPreview.items.length})
              </button>
            </section>
          )}
        </div>
      )}
    </details>
  );
}

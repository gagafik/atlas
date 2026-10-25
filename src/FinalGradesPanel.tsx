import { useRef, useState } from "react";
import { api } from "./api";
import { defaultDraft, needsReason } from "./finalGrades";
import GradebookPreparationPanel from "./GradebookPreparationPanel";
import { csvCell } from "./utils";

type Named = { id: string; name: string };
type Scale = Named & {
  version: number;
  kind: "thresholds" | "labels";
  rounding: string;
  minCount: number;
  active: boolean;
  bands: { label: string; minPercent: string | number }[];
};
type Period = Named & {
  version: number;
  status: string;
  start: string;
  end: string;
};
type Book = {
  id: string;
  period: Period;
  closureVersion: number;
  current: boolean;
};
type Calculation = {
  rawPercent: string | null;
  roundedPercent: string | null;
  recommended: string | null;
};
type Proposal = {
  scale: Scale;
  outcome: string;
  label: string;
  comment: string;
  explanation: string;
  calculation: Calculation;
  author: string;
  approval?: { actor: string; created: string };
  carriedFrom?: { bookId: string };
};
type Published = {
  student: Named & { className: string };
  subjectId: string;
  subject: string;
  label: string;
  outcome: string;
  comment: string;
  period: string;
  scale: Scale;
  closureVersion: number;
  publishedAt: string;
  current: boolean;
};
type Row = {
  id: string;
  bookId: string;
  studentId: string;
  subjectId: string;
  classId: string;
  assigned: string;
  version: number;
  status: string;
  proposal: Proposal | null;
  published: Published | null;
  source: {
    student: Named & { className: string };
    subject: string;
    expected: number;
    published: number;
    numeric: number;
    text: number;
    missing: number;
    zeroWeight: number;
    incompleteRoster: number;
    weight: string;
    weightedSum: string;
  };
};
type Options = {
  admin: boolean;
  staff: boolean;
  classTeacher?: boolean;
  scales: Scale[];
  books: Book[];
  periods: Period[];
  students: Named[];
  teachers: Named[];
};
type Sheet = {
  book: Book;
  items: Row[];
  total: number;
  nextOffset: number | null;
  classes: Named[];
  subjects: Named[];
};
type Evidence = {
  id: number;
  date: string;
  start: string;
  assessment: string;
  score: number | null;
  label: string;
  maxScore: number;
  weight: number;
};
type History = {
  id: number;
  action: string;
  actor: string;
  created: string;
  reason: string;
  row: Row;
};
const actionLabels: Record<string, string> = {
  approve: "Утвердить выбранные",
  return: "Вернуть на исправление",
  publish: "Опубликовать выбранные",
  reopen: "Открыть исправление",
  withdraw: "Отозвать публикацию",
};
const names: Record<string, string> = {
  draft: "Черновик",
  submitted: "На утверждении",
  approved: "Утверждено",
  published: "Опубликовано",
  graded: "Оценка",
  not_assessed: "Не аттестован",
  exempt: "Освобождён",
  assign: "Назначен ответственный",
  saved: "Черновик сохранён",
  approve: "Утверждение",
  return: "Возврат на исправление",
  publish: "Публикация",
  reopen: "Открыто исправление",
  withdraw: "Публикация отозвана",
};
function finalLabel(p: { outcome: string; label: string }) {
  return p.outcome === "graded" ? p.label : names[p.outcome];
}
function download(rows: unknown[][], filename: string) {
  const blob = new Blob(
    [
      "\ufeff" +
        rows
          .map((r) => r.map((v) => csvCell(String(v ?? ""))).join(";"))
          .join("\r\n"),
    ],
    { type: "text/csv;charset=utf-8" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function Select({
  label,
  value,
  onChange,
  items,
  empty = "Выберите",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  items: Named[];
  empty?: string;
}) {
  return (
    <label className="field">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{empty}</option>
        {items.map((i) => (
          <option key={i.id} value={i.id}>
            {i.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function ScaleEditor({
  scales,
  busy,
  onSave,
}: {
  scales: Scale[];
  busy: boolean;
  onSave: (s: Scale) => Promise<boolean>;
}) {
  const blank = (): Scale => ({
    id: crypto.randomUUID().replaceAll("-", ""),
    version: 0,
    name: "",
    kind: "thresholds",
    rounding: "none",
    minCount: 1,
    active: true,
    bands: [
      { label: "", minPercent: 0 },
      { label: "", minPercent: 50 },
    ],
  });
  const [value, setValue] = useState<Scale>(blank),
    [open, setOpen] = useState(false);
  return (
    <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Школьные шкалы оценивания</summary>
      <p>
        Порог — минимальный процент для оценки. Рекомендация использует среднее
        процентов работ с учётом их веса. Пропуски и работы с нулевым весом не
        снижают среднее. Текстовые отметки не переводятся в баллы.
      </p>
      <fieldset disabled={busy}>
        <Select
          label="Редактировать шкалу"
          value={value.version ? value.id : ""}
          onChange={(id) =>
            setValue(
              structuredClone(scales.find((s) => s.id === id) || blank()),
            )
          }
          items={scales}
          empty="Новая шкала"
        />
        <label className="field">
          Название шкалы
          <input
            value={value.name}
            maxLength={120}
            onChange={(e) => setValue({ ...value, name: e.target.value })}
          />
        </label>
        <label className="field">
          Тип шкалы
          <select
            value={value.kind}
            onChange={(e) =>
              setValue({ ...value, kind: e.target.value as Scale["kind"] })
            }
          >
            <option value="thresholds">Пороги процентов</option>
            <option value="labels">Итог выбирается вручную</option>
          </select>
        </label>
        {value.bands.map((band, i) => (
          <div className="final-band" key={i}>
            <label className="field">
              Оценка {i + 1}
              <input
                value={band.label}
                maxLength={30}
                onChange={(e) =>
                  setValue({
                    ...value,
                    bands: value.bands.map((b, n) =>
                      n === i ? { ...b, label: e.target.value } : b,
                    ),
                  })
                }
              />
            </label>
            {value.kind === "thresholds" && (
              <label className="field">
                Порог {i + 1}, %
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="0.0001"
                  value={band.minPercent}
                  onChange={(e) =>
                    setValue({
                      ...value,
                      bands: value.bands.map((b, n) =>
                        n === i ? { ...b, minPercent: e.target.value } : b,
                      ),
                    })
                  }
                />
              </label>
            )}
            <button
              disabled={value.bands.length <= 2}
              onClick={() =>
                setValue({
                  ...value,
                  bands: value.bands.filter((_, n) => n !== i),
                })
              }
            >
              Удалить оценку {i + 1}
            </button>
          </div>
        ))}
        <button
          disabled={value.bands.length >= 30}
          onClick={() =>
            setValue({
              ...value,
              bands: [...value.bands, { label: "", minPercent: 100 }],
            })
          }
        >
          Добавить оценку
        </button>
        <label className="field">
          Округление процента перед выбором оценки
          <select
            value={value.rounding}
            onChange={(e) => setValue({ ...value, rounding: e.target.value })}
          >
            <option value="none">Без округления</option>
            <option value="0">До целого, половина вверх</option>
            <option value="1">До десятых, половина вверх</option>
            <option value="2">До сотых, половина вверх</option>
          </select>
        </label>
        <label className="field">
          Минимум работ с баллами и положительным весом
          <input
            type="number"
            min="1"
            max="10000"
            value={value.minCount}
            onChange={(e) =>
              setValue({ ...value, minCount: Number(e.target.value) })
            }
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={value.active}
            onChange={(e) => setValue({ ...value, active: e.target.checked })}
          />{" "}
          Доступна для новых итогов
        </label>
        <p>
          Изменение шкалы не меняет уже подготовленные и опубликованные итоги.
        </p>
        <button
          onClick={() =>
            void onSave(value).then((ok) => {
              if (ok) {
                setValue(blank());
                setOpen(false);
              }
            })
          }
        >
          Сохранить шкалу
        </button>
      </fieldset>
    </details>
  );
}

export default function FinalGradesPanel({ workspace }: { workspace: string }) {
  const [open, setOpen] = useState(false),
    [options, setOptions] = useState<Options | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [sheet, setSheet] = useState<Sheet | null>(null),
    [bookId, setBookId] = useState(""),
    [periodId, setPeriodId] = useState(""),
    [studentId, setStudentId] = useState("");
  const [filter, setFilter] = useState({
      classId: "",
      subjectId: "",
      status: "all",
    }),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<string[]>([]),
    [teacherId, setTeacherId] = useState(""),
    [reason, setReason] = useState("");
  const [row, setRow] = useState<Row | null>(null),
    [marks, setMarks] = useState<Evidence[]>([]),
    [nextMark, setNextMark] = useState<number | null>(null),
    [history, setHistory] = useState<History[]>([]),
    [nextHistory, setNextHistory] = useState<number | null>(null);
  const [draft, setDraft] = useState({
      scaleId: "",
      scaleVersion: 0,
      outcome: "graded",
      label: "",
      comment: "",
      explanation: "",
    }),
    [calculations, setCalculations] = useState<Record<string, Calculation>>({}),
    [dirty, setDirty] = useState(false),
    [report, setReport] = useState<{
      period: Period;
      items: Published[];
    } | null>(null);
  const lock = useRef(false),
    lastScale = useRef(""),
    reportRef = useRef<HTMLDivElement>(null);
  const leave = () =>
    !dirty ||
    window.confirm("В строке есть несохранённый итог. Отменить эти изменения?");
  async function run(work: () => Promise<void>) {
    if (lock.current) return false;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
      return true;
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить действие.",
      );
      return false;
    } finally {
      setBusy(false);
      lock.current = false;
    }
  }
  async function loadOptions() {
    const r = await api("final-grades/options", workspace);
    setOptions(r);
    return r as Options;
  }
  async function loadSheet(id: string, start = 0, filters = filter) {
    const r = await api(
      `final-grades/books/${id}?${new URLSearchParams({ ...filters, offset: String(start) })}`,
      workspace,
    );
    setSheet(r);
    setBookId(id);
    setOffset(start);
    setSelected([]);
    return r as Sheet;
  }
  function clearRow() {
    setRow(null);
    setMarks([]);
    setHistory([]);
    setDirty(false);
  }
  async function openRow(value: Row) {
    const r = await api(`final-grades/rows/${value.id}/evidence`, workspace);
    setRow(r.row);
    setCalculations(r.recommendations);
    setMarks(r.items);
    setNextMark(r.nextAfter);
    setHistory([]);
    setNextHistory(null);
    const p = r.row.proposal as Proposal | null;
    setDraft(
      defaultDraft(
        p,
        options?.scales || [],
        r.recommendations,
        lastScale.current,
      ),
    );
    setDirty(false);
  }
  async function act(action: string) {
    if (!sheet || !selected.length || !leave()) return;
    if (
      action === "publish" &&
      !window.confirm(
        `Опубликовать выбранные итоги (${selected.length}) ученикам и родителям?`,
      )
    )
      return;
    await run(async () => {
      await api("final-grades/actions", workspace, "POST", {
        action,
        rows: sheet.items
          .filter((r) => selected.includes(r.id))
          .map((r) => ({ id: r.id, version: r.version })),
        teacherId,
        reason: needsReason(action) ? reason.trim() : "",
      });
      setReason("");
      clearRow();
      await loadSheet(bookId, offset);
      setNotice(
        `${names[action] || action}: выполнено для ${selected.length} строк.`,
      );
    });
  }
  const activeScale = options?.scales.find(
    (s) =>
      s.id === draft.scaleId && s.version === draft.scaleVersion && s.active,
  );
  const draftScale =
    activeScale ||
    (row?.proposal?.scale.id === draft.scaleId
      ? row.proposal.scale
      : undefined);
  const calculation =
    calculations[draft.scaleId + ":" + draft.scaleVersion] ||
    (row?.proposal?.scale.id === draft.scaleId &&
    row.proposal.scale.version === draft.scaleVersion
      ? row.proposal.calculation
      : undefined);
  const updateDraft = (update: Partial<typeof draft>) => {
    setDraft({ ...draft, ...update });
    setDirty(true);
  };
  // Families, administrators and the class teacher of the pupils read published reports.
  const readsReports =
    !!options?.classTeacher || !options?.staff || options.admin;
  return (
    <section className="absence-panel final-grades-panel">
      <button
        className="secondary"
        aria-expanded={open}
        onClick={() => {
          if (!open) {
            setOpen(true);
            void run(async () => {
              await loadOptions();
            });
          } else if (leave()) setOpen(false);
        }}
      >
        Итоговые оценки и табели
      </button>
      {open && (
        <>
          <p>
            Итоги готовятся по архиву закрытого периода. Учитель предлагает
            оценку, администратор утверждает и публикует её. До публикации она
            не видна семье.
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          {busy && <p role="status">Загрузка…</p>}
          <fieldset disabled={busy}>
            <button
              onClick={() => {
                if (leave())
                  void run(async () => {
                    clearRow();
                    setReport(null);
                    await loadOptions();
                    if (bookId) await loadSheet(bookId, offset);
                  });
              }}
            >
              Обновить итоги
            </button>
            {options?.admin && (
              <>
                <ScaleEditor
                  scales={options.scales}
                  busy={busy}
                  onSave={async (value) =>
                    run(async () => {
                      const { id, ...body } = value;
                      await api(
                        `final-grades/scales/${id}`,
                        workspace,
                        "PUT",
                        body,
                      );
                      await loadOptions();
                      setNotice("Шкала сохранена.");
                    })
                  }
                />
                <GradebookPreparationPanel
                  workspace={workspace}
                  periods={options.periods}
                  onOpen={async (id) => {
                    if (!leave()) return;
                    await run(async () => {
                      clearRow();
                      setReason("");
                      setFilter({ classId: "", subjectId: "", status: "all" });
                      await loadOptions();
                      await loadSheet(id, 0, {
                        classId: "",
                        subjectId: "",
                        status: "all",
                      });
                      setNotice(
                        "Ведомость готова. Назначьте ответственных учителей.",
                      );
                    });
                  }}
                />
              </>
            )}
            {options?.staff && (
              <>
                <Select
                  label="Ведомость"
                  value={bookId}
                  items={options.books.map((b) => ({
                    id: b.id,
                    name: `${b.period.name} · закрытие ${b.closureVersion}${b.current ? "" : " · архив"}`,
                  }))}
                  onChange={(id) => {
                    if (!leave()) return;
                    clearRow();
                    setReason("");
                    setBookId(id);
                    setSheet(null);
                    setFilter({ classId: "", subjectId: "", status: "all" });
                    if (id)
                      void run(async () => {
                        await loadSheet(id, 0, {
                          classId: "",
                          subjectId: "",
                          status: "all",
                        });
                      });
                  }}
                />
                {!options.books.length && (
                  <p>
                    {options.admin
                      ? "Подготовьте первую ведомость закрытого периода."
                      : "Вам пока не назначены строки итоговых оценок."}
                  </p>
                )}
                {sheet && (
                  <>
                    <h3>
                      {sheet.book.period.name} · {sheet.book.period.start}–
                      {sheet.book.period.end}
                    </h3>
                    {!sheet.book.current && (
                      <p className="error">
                        Этот источник устарел: период открыт или закрыт
                        повторно. Для новых итогов нужна ведомость нового
                        закрытия. История доступна; ошибочную действующую
                        публикацию может отозвать администратор.
                      </p>
                    )}
                    <div className="dated-toolbar">
                      {(["classId", "subjectId"] as const).map((key) => (
                        <Select
                          key={key}
                          label={key === "classId" ? "Класс" : "Предмет"}
                          value={filter[key]}
                          empty="Все"
                          items={
                            key === "classId" ? sheet.classes : sheet.subjects
                          }
                          onChange={(value) => {
                            if (!leave()) return;
                            const f = { ...filter, [key]: value };
                            setFilter(f);
                            clearRow();
                            void run(async () => {
                              await loadSheet(bookId, 0, f);
                            });
                          }}
                        />
                      ))}
                      <label className="field">
                        Статус
                        <select
                          value={filter.status}
                          onChange={(e) => {
                            if (!leave()) return;
                            const f = { ...filter, status: e.target.value };
                            setFilter(f);
                            clearRow();
                            void run(async () => {
                              await loadSheet(bookId, 0, f);
                            });
                          }}
                        >
                          <option value="all">Все</option>
                          {["draft", "submitted", "approved", "published"].map(
                            (s) => (
                              <option key={s} value={s}>
                                {names[s]}
                              </option>
                            ),
                          )}
                        </select>
                      </label>
                    </div>
                    <p>
                      Строк: {sheet.total}. Показаны{" "}
                      {sheet.items.length ? offset + 1 : 0}–
                      {offset + sheet.items.length}.
                    </p>
                    {options.admin && (
                      <>
                        <label>
                          <input
                            type="checkbox"
                            checked={
                              !!sheet.items.length &&
                              selected.length === sheet.items.length
                            }
                            onChange={(e) =>
                              setSelected(
                                e.target.checked
                                  ? sheet.items.map((r) => r.id)
                                  : [],
                              )
                            }
                          />{" "}
                          Выбрать страницу
                        </label>
                        <div className="dated-toolbar">
                          <Select
                            label="Ответственный учитель"
                            value={teacherId}
                            onChange={setTeacherId}
                            items={options.teachers}
                            empty="Снять назначение"
                          />
                          <button
                            disabled={!selected.length || !sheet.book.current}
                            onClick={() => void act("assign")}
                          >
                            Назначить выбранным
                          </button>
                        </div>
                        <p>
                          Назначение даёт учителю доступ к итогам и работам
                          только выбранных учеников и предметов.
                        </p>
                        <label className="field">
                          Причина возврата, исправления или отзыва
                          <input
                            value={reason}
                            maxLength={1000}
                            onChange={(e) => setReason(e.target.value)}
                          />
                        </label>
                        <div className="dated-toolbar">
                          {[
                            "approve",
                            "return",
                            "publish",
                            "reopen",
                            "withdraw",
                          ].map((action) => (
                            <button
                              key={action}
                              disabled={
                                !selected.length ||
                                (!sheet.book.current &&
                                  action !== "withdraw") ||
                                (["return", "reopen", "withdraw"].includes(
                                  action,
                                ) &&
                                  reason.trim().length < 3)
                              }
                              onClick={() => void act(action)}
                            >
                              {actionLabels[action]}
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                    <div className="attendance-cards">
                      {sheet.items.map((r) => (
                        <article className="absence-card" key={r.id}>
                          {options.admin && (
                            <label>
                              <input
                                type="checkbox"
                                checked={selected.includes(r.id)}
                                onChange={(e) =>
                                  setSelected(
                                    e.target.checked
                                      ? [...selected, r.id]
                                      : selected.filter((id) => id !== r.id),
                                  )
                                }
                              />{" "}
                              Выбрать {r.source.student.name},{" "}
                              {r.source.subject}
                            </label>
                          )}
                          <h4>
                            {r.source.student.name} ·{" "}
                            {r.source.student.className}
                          </h4>
                          <p>
                            {r.source.subject} · {names[r.status]}
                          </p>
                          <p>
                            {r.proposal
                              ? finalLabel(r.proposal)
                              : "Итог не подготовлен"}{" "}
                            · работ с баллами: {r.source.numeric}
                          </p>
                          {options.admin && (
                            <p>
                              Ответственный:{" "}
                              {options.teachers.find((t) => t.id === r.assigned)
                                ?.name ||
                                (r.assigned
                                  ? "Учётная запись недоступна"
                                  : "Не назначен")}
                            </p>
                          )}
                          {r.published && (
                            <p>
                              В табеле: {finalLabel(r.published)}
                              {r.status !== "published"
                                ? " — сохраняется до новой публикации"
                                : ""}
                            </p>
                          )}
                          <button
                            onClick={() => {
                              if (leave())
                                void run(async () => {
                                  await openRow(r);
                                });
                            }}
                          >
                            Итог и работы: {r.source.student.name},{" "}
                            {r.source.subject}
                          </button>
                        </article>
                      ))}
                    </div>
                    <div className="dated-toolbar">
                      <button
                        disabled={offset === 0}
                        onClick={() => {
                          if (leave())
                            void run(async () => {
                              clearRow();
                              await loadSheet(bookId, Math.max(0, offset - 50));
                            });
                        }}
                      >
                        Предыдущая страница итогов
                      </button>
                      <button
                        disabled={sheet.nextOffset === null}
                        onClick={() => {
                          if (leave())
                            void run(async () => {
                              clearRow();
                              await loadSheet(bookId, sheet.nextOffset!);
                            });
                        }}
                      >
                        Следующая страница итогов
                      </button>
                      <button
                        disabled={!sheet.items.length}
                        onClick={() =>
                          download(
                            [
                              ["Ученик", "Класс", "Предмет", "Статус", "Итог"],
                              ...sheet.items.map((r) => [
                                r.source.student.name,
                                r.source.student.className,
                                r.source.subject,
                                names[r.status],
                                r.proposal ? finalLabel(r.proposal) : "",
                              ]),
                            ],
                            "Ведомость — текущая страница.csv",
                          )
                        }
                      >
                        CSV текущей страницы
                      </button>
                    </div>
                    {row && (
                      <article className="absence-card final-grade-editor">
                        <h3>
                          {row.source.student.name} · {row.source.subject}
                        </h3>
                        <p>
                          Запланировано уроков: {row.source.expected};
                          опубликовано: {row.source.published}. Учтено работ с
                          баллами: {row.source.numeric}; текстовых:{" "}
                          {row.source.text}; без отметки: {row.source.missing};
                          с нулевым весом: {row.source.zeroWeight}.
                        </p>
                        {(!row.source.expected ||
                          row.source.incompleteRoster > 0 ||
                          row.source.published < row.source.expected) && (
                          <p className="error">
                            Есть пробелы или расхождения с расписанием.
                            Проверьте исходные журналы перед утверждением.
                          </p>
                        )}
                        <fieldset
                          disabled={
                            row.status !== "draft" || !sheet.book.current
                          }
                        >
                          <Select
                            label="Шкала для итога"
                            value={draft.scaleId}
                            items={options.scales.filter(
                              (s) => s.active || s.id === draft.scaleId,
                            )}
                            onChange={(id) => {
                              const s = options.scales.find((s) => s.id === id);
                              updateDraft({
                                scaleId: id,
                                scaleVersion: s?.version || 0,
                                label:
                                  (s &&
                                    calculations[`${id}:${s.version}`]
                                      ?.recommended) ||
                                  "",
                              });
                            }}
                          />
                          {!activeScale && draft.scaleId && (
                            <p className="error">
                              Сохранённая редакция шкалы недоступна для новой
                              записи. Выберите актуальную шкалу.
                            </p>
                          )}
                          {activeScale && !calculation && (
                            <button
                              onClick={() =>
                                void run(async () => {
                                  const r = await api(
                                    `final-grades/rows/${row.id}/evidence`,
                                    workspace,
                                  );
                                  setCalculations(r.recommendations);
                                })
                              }
                            >
                              Рассчитать рекомендацию
                            </button>
                          )}
                          {calculation && (
                            <p>
                              Рекомендация: {calculation.recommended || "нет"}.
                              Процент: {calculation.rawPercent || "нет"}; после
                              округления: {calculation.roundedPercent || "нет"}.
                              Минимум работ: {draftScale?.minCount}.
                            </p>
                          )}
                          <label className="field">
                            Вид итога
                            <select
                              value={draft.outcome}
                              onChange={(e) =>
                                updateDraft({
                                  outcome: e.target.value,
                                  label:
                                    e.target.value === "graded"
                                      ? calculation?.recommended || ""
                                      : "",
                                })
                              }
                            >
                              {["graded", "not_assessed", "exempt"].map((s) => (
                                <option value={s} key={s}>
                                  {names[s]}
                                </option>
                              ))}
                            </select>
                          </label>
                          {draft.outcome === "graded" && (
                            <Select
                              label="Итоговая оценка"
                              value={draft.label}
                              onChange={(label) => updateDraft({ label })}
                              items={(draftScale?.bands || []).map((b) => ({
                                id: b.label,
                                name: b.label,
                              }))}
                            />
                          )}
                          <label className="field">
                            Комментарий ученику и родителям
                            <textarea
                              value={draft.comment}
                              maxLength={1000}
                              onChange={(e) =>
                                updateDraft({ comment: e.target.value })
                              }
                            />
                          </label>
                          <label className="field">
                            Обоснование для школы
                            <textarea
                              value={draft.explanation}
                              maxLength={1000}
                              onChange={(e) =>
                                updateDraft({ explanation: e.target.value })
                              }
                            />
                          </label>
                          <p>
                            Обоснование обязательно при отклонении от
                            рекомендации, отсутствии рекомендации по пороговой
                            шкале, неаттестации или освобождении. Для шкалы с
                            ручным выбором оценки оно необязательно. Оно не
                            публикуется семье.
                          </p>
                          <div className="dated-toolbar">
                            {[false, true].map((submit) => (
                              <button
                                key={String(submit)}
                                disabled={!activeScale || !draft.scaleId}
                                onClick={() =>
                                  void run(async () => {
                                    const r = await api(
                                      `final-grades/rows/${row.id}`,
                                      workspace,
                                      "PUT",
                                      {
                                        ...draft,
                                        version: row.version,
                                        submit,
                                      },
                                    );
                                    lastScale.current = draft.scaleId;
                                    await loadSheet(bookId, offset);
                                    await openRow(r);
                                    setNotice(
                                      submit
                                        ? "Итог отправлен на утверждение."
                                        : "Черновик сохранён.",
                                    );
                                  })
                                }
                              >
                                {submit
                                  ? "Отправить на утверждение"
                                  : "Сохранить черновик"}
                              </button>
                            ))}
                          </div>
                        </fieldset>
                        {row.proposal?.carriedFrom &&
                          row.status === "draft" && (
                            <p className="error">
                              Итог перенесён из прошлого закрытия. Проверьте
                              работы и отправьте заново.
                            </p>
                          )}
                        {row.proposal && (
                          <p>
                            Сохранённый итог: {finalLabel(row.proposal)}.
                            Рекомендация:{" "}
                            {row.proposal.calculation.recommended || "нет"}.
                            Процент:{" "}
                            {row.proposal.calculation.rawPercent || "нет"};
                            после округления:{" "}
                            {row.proposal.calculation.roundedPercent || "нет"}.
                            Шкала «{row.proposal.scale.name}», редакция{" "}
                            {row.proposal.scale.version}.{" "}
                            {row.proposal.approval &&
                              `Утвердил: ${row.proposal.approval.actor}.`}
                          </p>
                        )}
                        <h4>Работы из архива закрытия</h4>
                        {!marks.length && <p>Проверочных работ нет.</p>}
                        <div className="final-evidence">
                          {marks.map((m) => (
                            <p key={m.id}>
                              {m.date}, {m.start} · {m.assessment}:{" "}
                              {m.score !== null
                                ? `${m.score} / ${m.maxScore}`
                                : m.label || "Нет отметки"}{" "}
                              · вес {m.weight}
                            </p>
                          ))}
                        </div>
                        {nextMark !== null && (
                          <button
                            onClick={() =>
                              void run(async () => {
                                const r = await api(
                                  `final-grades/rows/${row.id}/evidence?after=${nextMark}`,
                                  workspace,
                                );
                                setMarks([...marks, ...r.items]);
                                setNextMark(r.nextAfter);
                              })
                            }
                          >
                            Ещё работы
                          </button>
                        )}
                        <button
                          onClick={() =>
                            void run(async () => {
                              const r = await api(
                                `final-grades/rows/${row.id}/history`,
                                workspace,
                              );
                              setHistory(r.items);
                              setNextHistory(r.nextBefore);
                            })
                          }
                        >
                          История итоговой оценки
                        </button>
                        {history.map((h) => (
                          <details key={h.id}>
                            <summary>
                              {names[h.action] || h.action} · {h.actor} ·{" "}
                              {new Date(h.created).toLocaleString("ru-RU")}
                            </summary>
                            <p>{h.reason}</p>
                            {h.row.proposal && (
                              <>
                                <p>
                                  {finalLabel(h.row.proposal)} ·{" "}
                                  {h.row.proposal.scale.name}, редакция{" "}
                                  {h.row.proposal.scale.version}
                                </p>
                                <p>{h.row.proposal.explanation}</p>
                              </>
                            )}
                          </details>
                        ))}
                        {nextHistory !== null && (
                          <button
                            onClick={() =>
                              void run(async () => {
                                const r = await api(
                                  `final-grades/rows/${row.id}/history?before=${nextHistory}`,
                                  workspace,
                                );
                                setHistory([...history, ...r.items]);
                                setNextHistory(r.nextBefore);
                              })
                            }
                          >
                            Ещё события итоговой оценки
                          </button>
                        )}
                      </article>
                    )}
                  </>
                )}
              </>
            )}
            {options && readsReports && (
              <>
                <h3>
                  {options.classTeacher
                    ? "Табели учеников вашего класса"
                    : "Опубликованный личный табель"}
                </h3>
                <div className="dated-toolbar">
                  <Select
                    label="Ученик для табеля"
                    value={studentId}
                    onChange={(id) => {
                      setStudentId(id);
                      setReport(null);
                    }}
                    items={options.students}
                  />
                  <Select
                    label="Период табеля"
                    value={periodId}
                    onChange={(id) => {
                      setPeriodId(id);
                      setReport(null);
                    }}
                    items={options.periods}
                  />
                  <button
                    disabled={!studentId || !periodId}
                    onClick={() =>
                      void run(async () => {
                        setReport(
                          await api(
                            `final-grades/report?${new URLSearchParams({ studentId, periodId })}`,
                            workspace,
                          ),
                        );
                      })
                    }
                  >
                    Показать табель
                  </button>
                </div>
                <p>
                  Показаны только опубликованные итоги. Пустой табель не
                  означает нулевые оценки.
                </p>
                {report && (
                  <>
                    <div className="final-report" ref={reportRef}>
                      <h3>Табель · {report.period.name}</h3>
                      <p>
                        {options.students.find((s) => s.id === studentId)?.name}{" "}
                        · {report.period.start}–{report.period.end}
                      </p>
                      {!report.items.length && (
                        <p>Опубликованных итогов пока нет.</p>
                      )}
                      {report.items.map((r) => (
                        <article key={r.subjectId}>
                          <h4>
                            {r.subject} — {finalLabel(r)}
                          </h4>
                          <p>{r.comment}</p>
                          <p>
                            Шкала: {r.scale.name}. Опубликовано:{" "}
                            {new Date(r.publishedAt).toLocaleString("ru-RU")}.
                          </p>
                          {!r.current && (
                            <p className="error">
                              Последний опубликованный итог. Период открыт для
                              исправлений или уже закрыт повторно; нового итога
                              пока нет.
                            </p>
                          )}
                        </article>
                      ))}
                    </div>
                    <div className="dated-toolbar">
                      <button
                        disabled={!report.items.length}
                        onClick={() =>
                          download(
                            [
                              [
                                "Ученик",
                                "Класс",
                                "Период",
                                "Предмет",
                                "Итог",
                                "Комментарий",
                                "Шкала",
                                "Редакция шкалы",
                                "Закрытие",
                                "Источник актуален",
                                "Опубликовано",
                              ],
                              ...report.items.map((r) => [
                                r.student.name,
                                r.student.className,
                                r.period,
                                r.subject,
                                finalLabel(r),
                                r.comment,
                                r.scale.name,
                                r.scale.version,
                                r.closureVersion,
                                r.current ? "Да" : "Нет",
                                r.publishedAt,
                              ]),
                            ],
                            "Табель.csv",
                          )
                        }
                      >
                        Скачать табель CSV
                      </button>
                      <button
                        disabled={!report.items.length}
                        onClick={() => {
                          const win = window.open("", "_blank");
                          if (!win) {
                            setError("Разрешите открытие окна для печати.");
                            return;
                          }
                          win.opener = null;
                          win.document.title = "Табель";
                          const node = reportRef.current?.cloneNode(true);
                          if (node) win.document.body.appendChild(node);
                          const style = win.document.createElement("style");
                          style.textContent =
                            "body{font:14px sans-serif;padding:24px;color:#111}article{break-inside:avoid;border-top:1px solid #ccc;padding:8px}h4,p{margin:8px 0}.error{font-weight:bold}";
                          win.document.head.appendChild(style);
                          win.print();
                        }}
                      >
                        Печать табеля
                      </button>
                    </div>
                  </>
                )}
              </>
            )}
          </fieldset>
        </>
      )}
    </section>
  );
}

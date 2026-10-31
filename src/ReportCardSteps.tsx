import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import FinalGradesMatrix from "./FinalGradesMatrix";
import FinalGradesTable from "./FinalGradesTable";
import { chunked } from "./finalGrades";
import type { ClassReport, Named, Options } from "./finalGradesTypes";
import { CLASS_PRINT, printNode } from "./printReport";
import ReportCards from "./ReportCards";
import { useBusy } from "./useBusy";

type Layout = {
  version: number;
  order: string[];
  hidden: string[];
  subjects: Named[];
};
type Counts = Record<string, number>;

const STEPS = [
  "Предметы",
  "Оценки",
  "Словесная оценка",
  "Печать",
  "Публикация семье",
];

// Subjects in the saved order; the ones the layout does not mention yet follow by name.
const ordered = (layout: Layout) => {
  const rank = new Map(layout.order.map((id, i) => [id, i]));
  return [...layout.subjects].sort(
    (a, b) =>
      (rank.get(a.id) ?? 1e6) - (rank.get(b.id) ?? 1e6) ||
      a.name.localeCompare(b.name, "ru", { numeric: true }),
  );
};

function Pick({
  label,
  value,
  items,
  onChange,
}: {
  label: string;
  value: string;
  items: Named[];
  onChange: (id: string) => void;
}) {
  return (
    <label className="field">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Выберите</option>
        {items.map((i) => (
          <option key={i.id} value={i.id}>
            {i.name}
          </option>
        ))}
      </select>
    </label>
  );
}

// The administrator walks one class through the card: subjects, grades, verbal assessment, print, publication.
// A class teacher only reads and prints the published cards of the own class.
export default function ReportCardSteps({
  workspace,
  options,
  lastScale,
  leave,
  onDirty,
  onChanged,
}: {
  workspace: string;
  options: Options;
  lastScale: { current: string };
  leave: () => boolean;
  onDirty: (dirty: boolean) => void;
  onChanged: () => void;
}) {
  const { busy, error, notice, setError, setNotice, run } = useBusy();
  const [periodId, setPeriodId] = useState(""),
    [classId, setClassId] = useState(""),
    [step, setStep] = useState(1),
    [layout, setLayout] = useState<Layout | null>(null),
    [items, setItems] = useState<Named[]>([]),
    [hidden, setHidden] = useState<string[]>([]),
    [counts, setCounts] = useState<Counts | null>(null),
    [subjectId, setSubjectId] = useState(""),
    [preview, setPreview] = useState<ClassReport | null>(null),
    [final, setFinal] = useState<ClassReport | null>(null),
    [phrases, setPhrases] = useState(options.phrases.join("\n")),
    [yearRule, setYearRule] = useState(options.yearRule),
    [loading, setLoading] = useState(false);
  const cards = useRef<HTMLDivElement>(null);
  const admin = options.admin;
  // The newest book of the period; a re-closure gets a book of its own.
  const book = options.books.find((b) => b.period.id === periodId);
  const ready = !!periodId && !!classId;
  const query = `periodId=${encodeURIComponent(periodId)}&classId=${encodeURIComponent(classId)}`;
  // The selects stay usable while a load runs, so a reply is shown only while the period and class it was asked for are still chosen.
  const key = `${periodId}/${classId}`;
  const chosen = useRef(key);
  const fresh = () => chosen.current === key;

  async function loadLayout() {
    const r: Layout = await api(
      `final-grades/report-layouts/${periodId}/${classId}`,
      workspace,
    );
    if (!fresh()) return;
    setLayout(r);
    setItems(ordered(r));
    setHidden(r.hidden);
    setSubjectId((id) => (r.subjects.some((s) => s.id === id) ? id : ""));
  }
  async function loadCounts() {
    if (!book) return setCounts(null);
    const r = await api(
      `final-grades/books/${book.id}/handles?${new URLSearchParams({ classId })}`,
      workspace,
    );
    if (!fresh()) return;
    const total: Counts = {};
    for (const x of r.items) total[x.status] = (total[x.status] || 0) + 1;
    setCounts(total);
  }
  async function loadFinal() {
    const r = await api(`final-grades/class-report?${query}`, workspace);
    if (fresh()) setFinal(r);
  }
  useEffect(() => {
    chosen.current = key;
    setLayout(null);
    setCounts(null);
    setPreview(null);
    setFinal(null);
    setError("");
    setNotice("");
    setLoading(ready && admin);
    if (!ready || !admin) return;
    // Not behind the busy lock: the load of the newly chosen class must never be dropped because the previous one is still running.
    void (async () => {
      try {
        await loadLayout();
        if (fresh()) await loadCounts();
      } catch (e) {
        if (fresh())
          setError(
            e instanceof Error ? e.message : "Не удалось загрузить класс.",
          );
      } finally {
        if (fresh()) setLoading(false);
      }
    })();
  }, [periodId, classId]);

  function move(i: number, by: number) {
    const j = i + by;
    if (j < 0 || j >= items.length) return;
    const next = [...items];
    [next[i], next[j]] = [next[j], next[i]];
    setItems(next);
  }
  function print(title: string) {
    if (!printNode(cards.current, title, CLASS_PRINT))
      setError("Разрешите открытие окна для печати.");
  }
  const printable = (r: ClassReport | null) =>
    !!r?.pupils.some((p) => p.items.length);

  const picks = (
    <div className="dated-toolbar">
      <Pick
        label="Период"
        value={periodId}
        items={options.periods}
        onChange={(id) => leave() && setPeriodId(id)}
      />
      <Pick
        label="Класс"
        value={classId}
        items={options.classes}
        onChange={(id) => leave() && setClassId(id)}
      />
    </div>
  );
  const messages = (
    <>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {(busy || loading) && <p role="status">Загрузка…</p>}
    </>
  );

  if (!admin)
    return (
      <section className="final-steps">
        <h3>Табели класса</h3>
        {picks}
        {messages}
        <button disabled={busy || !ready} onClick={() => void run(loadFinal)}>
          Показать табели класса
        </button>
        <p>
          Показаны только опубликованные итоги учеников вашего класса.
          Предварительные и неутверждённые оценки здесь не видны.
        </p>
        {final && (
          <>
            <button
              disabled={!printable(final)}
              onClick={() => print(`Табели ${final.className}`)}
            >
              Печать класса
            </button>
            <ReportCards report={final} ref={cards} />
          </>
        )}
      </section>
    );

  const state = [
    layout ? (layout.version ? "настроено" : "по умолчанию") : "",
    counts
      ? counts.draft || counts.submitted
        ? `не утверждено: ${(counts.draft || 0) + (counts.submitted || 0)}`
        : "готово"
      : "",
    "",
    "",
    counts?.approved
      ? `к публикации: ${counts.approved}`
      : counts?.published
        ? "опубликовано"
        : "",
  ];
  return (
    <section className="final-steps">
      <h3>Табель класса по шагам</h3>
      {picks}
      {messages}
      {ready && !book && (
        <p className="error">
          Для периода нет ведомости. Подготовьте её выше, затем вернитесь к
          шагам.
        </p>
      )}
      {ready && (
        <>
          <ol className="final-step-list">
            {STEPS.map((title, i) => (
              <li key={title}>
                <button
                  aria-current={step === i + 1 ? "step" : undefined}
                  onClick={() => leave() && setStep(i + 1)}
                >
                  {i + 1}. {title}
                  {state[i] ? ` — ${state[i]}` : ""}
                </button>
              </li>
            ))}
          </ol>
          {step === 1 && layout && (
            <div>
              <p>
                Выберите предметы, которые попадут в табель этого класса, и их
                порядок. Скрытый предмет не показывается семье и не входит в
                средний балл; опубликованные итоги при этом не удаляются.
              </p>
              {!items.length && <p>В ведомости нет предметов этого класса.</p>}
              <ul className="final-subjects">
                {items.map((s, i) => (
                  <li key={s.id}>
                    <label>
                      <input
                        type="checkbox"
                        checked={!hidden.includes(s.id)}
                        onChange={(e) =>
                          setHidden(
                            e.target.checked
                              ? hidden.filter((h) => h !== s.id)
                              : [...hidden, s.id],
                          )
                        }
                      />{" "}
                      В табель: {s.name}
                    </label>{" "}
                    <button
                      disabled={i === 0}
                      aria-label={`Поднять: ${s.name}`}
                      onClick={() => move(i, -1)}
                    >
                      Выше
                    </button>{" "}
                    <button
                      disabled={i === items.length - 1}
                      aria-label={`Опустить: ${s.name}`}
                      onClick={() => move(i, 1)}
                    >
                      Ниже
                    </button>
                  </li>
                ))}
              </ul>
              <button
                disabled={busy || !items.length}
                onClick={() =>
                  void run(async () => {
                    await api(
                      `final-grades/report-layouts/${periodId}/${classId}`,
                      workspace,
                      "PUT",
                      {
                        version: layout.version,
                        order: items.map((s) => s.id),
                        hidden: hidden.filter((h) =>
                          items.some((s) => s.id === h),
                        ),
                      },
                    );
                    await loadLayout();
                    if (fresh())
                      setNotice("Порядок и состав предметов сохранены.");
                  })
                }
              >
                Сохранить предметы
              </button>
            </div>
          )}
          {step === 2 && book && (
            <FinalGradesMatrix
              workspace={workspace}
              book={book}
              admin
              scales={options.scales}
              phrases={options.phrases}
              teachers={options.teachers}
              lastScale={lastScale}
              classFilter={classId}
              leave={leave}
              onDirty={onDirty}
              onChanged={() => {
                void loadCounts();
                onChanged();
              }}
            />
          )}
          {step === 3 && (
            <div>
              <Pick
                label="Предмет"
                value={subjectId}
                items={layout?.subjects || []}
                onChange={(id) => leave() && setSubjectId(id)}
              />
              {book && subjectId && (
                <FinalGradesTable
                  key={`${book.id}-${classId}-${subjectId}`}
                  workspace={workspace}
                  bookId={book.id}
                  classId={classId}
                  subjectId={subjectId}
                  scales={options.scales}
                  phrases={options.phrases}
                  current={book.current}
                  lastScale={lastScale}
                  onDirty={onDirty}
                  onChanged={() => {
                    void loadCounts();
                    onChanged();
                  }}
                />
              )}
              <details>
                <summary>Фразы словесной оценки и годовая оценка школы</summary>
                <label className="field">
                  Готовые фразы, по одной в строке
                  <textarea
                    rows={5}
                    value={phrases}
                    onChange={(e) => setPhrases(e.target.value)}
                  />
                </label>
                <label className="field">
                  Годовая оценка
                  <select
                    value={yearRule}
                    onChange={(e) =>
                      setYearRule(e.target.value as Options["yearRule"])
                    }
                  >
                    <option value="periods">
                      По итогам четвертей или полугодий
                    </option>
                    <option value="works">По всем работам года</option>
                  </select>
                </label>
                <p>
                  Рекомендация по итогам периодов работает для шкал с весом
                  оценок; округление идёт к ближайшей оценке, середина — вверх.
                  Правило нужно подтвердить в школе.
                </p>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(
                        "final-grades/report-settings",
                        workspace,
                        "PUT",
                        {
                          version: options.reportVersion,
                          phrases: phrases
                            .split("\n")
                            .map((p) => p.trim())
                            .filter(Boolean),
                          yearRule,
                        },
                      );
                      onChanged();
                      setNotice("Настройки табеля сохранены.");
                    })
                  }
                >
                  Сохранить настройки табеля
                </button>
              </details>
            </div>
          )}
          {step === 4 && (
            <div>
              <p>
                Предварительный табель собран из утверждённых и опубликованных
                итогов. Черновики и итоги на утверждении в нём не показываются.
              </p>
              <button
                disabled={busy || !book}
                onClick={() =>
                  void run(async () => {
                    const r = await api(
                      `final-grades/class-report?${query}&preview=true`,
                      workspace,
                    );
                    if (fresh()) setPreview(r);
                  })
                }
              >
                Собрать предварительный табель
              </button>
              {preview && (
                <>
                  <button
                    disabled={!printable(preview)}
                    onClick={() =>
                      print(`Предварительный табель ${preview.className}`)
                    }
                  >
                    Печать класса
                  </button>
                  <ReportCards report={preview} ref={cards} />
                </>
              )}
            </div>
          )}
          {step === 5 && (
            <div>
              <p>
                Публикация показывает табель ученикам и родителям. После неё
                семья получит уведомление. Опубликуйте утверждённые итоги класса
                и распечатайте итоговый табель.
              </p>
              <button
                disabled={busy || !book?.current || !counts?.approved}
                onClick={() =>
                  void run(async () => {
                    if (!book) return;
                    const h = await api(
                      `final-grades/books/${book.id}/handles?${new URLSearchParams({ classId, status: "approved" })}`,
                      workspace,
                    );
                    const rows = h.items.map(
                      (x: { id: string; version: number }) => ({
                        id: x.id,
                        version: x.version,
                      }),
                    );
                    if (!rows.length)
                      return setNotice(
                        "Нет утверждённых итогов для публикации.",
                      );
                    if (
                      !window.confirm(
                        `Опубликовать утверждённые итоги (${rows.length}) ученикам и родителям?`,
                      )
                    )
                      return;
                    let done = 0;
                    try {
                      for (const part of chunked<{
                        id: string;
                        version: number;
                      }>(rows, 100)) {
                        await api("final-grades/actions", workspace, "POST", {
                          action: "publish",
                          rows: part,
                          teacherId: "",
                          reason: "",
                        });
                        done += part.length;
                      }
                    } catch (e) {
                      await loadCounts().catch(() => undefined);
                      throw new Error(
                        `Опубликовано ${done} из ${rows.length}; остальные строки не изменены. ${e instanceof Error ? e.message : ""}`,
                      );
                    }
                    await loadCounts();
                    await loadFinal();
                    onChanged();
                    setNotice(`Опубликовано итогов: ${rows.length}.`);
                  })
                }
              >
                Опубликовать утверждённые ({counts?.approved || 0})
              </button>
              <button disabled={busy} onClick={() => void run(loadFinal)}>
                Показать итоговый табель
              </button>
              {final && (
                <>
                  <button
                    disabled={!printable(final)}
                    onClick={() => print(`Табели ${final.className}`)}
                  >
                    Печать итогового табеля
                  </button>
                  <ReportCards report={final} ref={cards} />
                </>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

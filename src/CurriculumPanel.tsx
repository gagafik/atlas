import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { csvCell, download } from "./utils";
import type {
  PlanContent,
  PlanDetail,
  PlanProgress,
  PlanTopic,
} from "./curriculum";
import "./curriculum.css";
import TopicMaterials from "./TopicMaterials";

interface Pair {
  groupId: string;
  group: string;
  subjectId: string;
  subject: string;
}
interface Options {
  start: string;
  end: string;
  pairs: Pair[];
}
interface Summary {
  id: string;
  title: string;
  start: string;
  end: string;
  version: number;
}
interface History {
  version: number;
  created: string;
  actor: string;
  archived: boolean;
}
const topic = (): PlanTopic => ({
  id: crypto.randomUUID(),
  title: "",
  hours: 1,
  plannedDate: null,
  standard: "",
});
const base = "journal/curriculum";

export default function CurriculumPanel({ workspace }: { workspace: string }) {
  const [options, setOptions] = useState<Options | null>(null),
    [pairIndex, setPairIndex] = useState(0);
  const [items, setItems] = useState<Summary[]>([]),
    [archived, setArchived] = useState(false),
    [offset, setOffset] = useState(0),
    [more, setMore] = useState(false);
  const [selected, setSelected] = useState<PlanDetail | null>(null),
    [draft, setDraft] = useState<PlanContent | null>(null);
  const [progress, setProgress] = useState<PlanProgress | null>(null),
    [history, setHistory] = useState<History[]>([]),
    [historyMore, setHistoryMore] = useState(false);
  const [copyGroup, setCopyGroup] = useState(""),
    [copyStart, setCopyStart] = useState(""),
    [copyEnd, setCopyEnd] = useState("");
  const [omitUnavailableMaterials, setOmitUnavailableMaterials] =
    useState(false);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(false),
    [epoch, setEpoch] = useState(0);
  const dirty = useRef(false),
    lock = useRef(false),
    generation = useRef(0),
    alive = useRef(true);
  const pair = options?.pairs[pairIndex];
  const editable =
    !selected ||
    (!selected.archived && selected.version === selected.latestVersion);
  function canLeave() {
    return (
      !lock.current &&
      (!dirty.current ||
        window.confirm(
          "Есть несохранённые изменения учебного плана. Отбросить их?",
        ))
    );
  }
  useEffect(() => {
    alive.current = true;
    const leave = (e: Event) => {
      if (!canLeave()) e.preventDefault();
    };
    const unload = (e: BeforeUnloadEvent) => {
      if (dirty.current || lock.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("atlas-homework-leave", leave);
    window.addEventListener("beforeunload", unload);
    return () => {
      alive.current = false;
      generation.current++;
      window.removeEventListener("atlas-homework-leave", leave);
      window.removeEventListener("beforeunload", unload);
    };
  }, []);
  useEffect(() => {
    let active = true;
    api(base + "/options", workspace)
      .then((r) => {
        if (active) setOptions(r);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [workspace]);
  useEffect(() => {
    if (!pair) return;
    let active = true;
    setLoading(true);
    setItems([]);
    api(
      `${base}?${new URLSearchParams({ groupId: pair.groupId, subjectId: pair.subjectId, archived: String(archived), offset: String(offset) })}`,
      workspace,
    )
      .then((r) => {
        if (active) {
          setItems(r.items);
          setMore(r.hasMore);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [workspace, pair, archived, offset, epoch]);
  function install(item: PlanDetail) {
    setSelected(item);
    setDraft(item.content);
    dirty.current = false;
    setCopyGroup(item.content.groupId);
    setCopyStart(item.content.start);
    setCopyEnd(item.content.end);
    setOmitUnavailableMaterials(false);
  }
  async function run(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function open(id: string, version?: number) {
    if (!canLeave()) return;
    await run(async () => {
      const current = ++generation.current;
      const item = await api(
        `${base}/${id}${version ? `?version=${version}` : ""}`,
        workspace,
      );
      if (!alive.current || current !== generation.current) return;
      install(item);
      setHistory([]);
      setHistoryMore(false);
      setProgress(null);
      const p = await api(`${base}/${id}/progress`, workspace);
      if (alive.current && current === generation.current) setProgress(p);
    });
  }
  function reset() {
    generation.current++;
    setSelected(null);
    setDraft(null);
    setProgress(null);
    setHistory([]);
    dirty.current = false;
    setError("");
    setNotice("");
  }
  function change(next: PlanContent) {
    setDraft(next);
    dirty.current = true;
  }
  function changeTopic(id: string, patch: Partial<PlanTopic>) {
    if (draft)
      change({
        ...draft,
        topics: draft.topics.map((t) => (t.id === id ? { ...t, ...patch } : t)),
      });
  }
  function move(i: number, delta: number) {
    if (!draft) return;
    const next = [...draft.topics];
    [next[i], next[i + delta]] = [next[i + delta], next[i]];
    change({ ...draft, topics: next });
  }
  async function save() {
    if (!draft) return;
    await run(async () => {
      const item = await api(
        `${base}${selected ? `/${selected.id}` : ""}`,
        workspace,
        selected ? "PUT" : "POST",
        { version: selected?.version || 0, content: draft },
      );
      if (!alive.current) return;
      install(item);
      setEpoch((v) => v + 1);
      setHistory([]);
      setProgress(null);
      setNotice(
        "План сохранён. Ранее опубликованные уроки сохраняют свои редакции тем.",
      );
      const p = await api(`${base}/${item.id}/progress`, workspace);
      if (alive.current) setProgress(p);
    });
  }
  async function loadHistory(append = false) {
    if (!selected) return;
    await run(async () => {
      const r = await api(
        `${base}/${selected.id}/history${append ? `?before=${history.at(-1)?.version}` : ""}`,
        workspace,
      );
      if (alive.current) {
        setHistory(append ? [...history, ...r.items] : r.items);
        setHistoryMore(r.hasMore);
      }
    });
  }
  function exportPlan() {
    if (!draft) return;
    const rows = [
      [
        "План",
        "Начало",
        "Окончание",
        "№",
        "Тема",
        "Часы",
        "Плановая дата",
        "Стандарт / результат",
      ],
      ...draft.topics.map((t, i) => [
        draft.title,
        draft.start,
        draft.end,
        String(i + 1),
        t.title,
        String(t.hours),
        t.plannedDate || "",
        t.standard,
      ]),
    ];
    download(
      "Учебный план.csv",
      "\uFEFF" + rows.map((r) => r.map(csvCell).join(";")).join("\r\n"),
      "text/csv;charset=utf-8",
    );
  }
  return (
    <section className="curriculum-panel">
      <h2>Учебные планы</h2>
      <p>
        Темы, плановые часы и результаты обучения по предмету и группе. План
        совместно ведут назначенные на эту нагрузку учителя и администратор.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="info-line">
          {notice}
        </p>
      )}
      {!options ? (
        <p role="status">Загружаем учебные назначения…</p>
      ) : !options.pairs.length ? (
        <p>
          Нет действующей учебной нагрузки. Администратор может назначить
          предмет и группу в расписании.
        </p>
      ) : (
        <>
          <div className="curriculum-toolbar">
            <label className="field">
              <span>Предмет и группа</span>
              <select
                disabled={busy}
                value={pairIndex}
                onChange={(e) => {
                  if (canLeave()) {
                    reset();
                    setPairIndex(Number(e.target.value));
                    setOffset(0);
                  }
                }}
              >
                {options.pairs.map((p, i) => (
                  <option key={`${p.groupId}:${p.subjectId}`} value={i}>
                    {p.subject} · {p.group}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={archived}
                disabled={busy}
                onChange={(e) => {
                  if (canLeave()) {
                    reset();
                    setArchived(e.target.checked);
                    setOffset(0);
                  }
                }}
              />{" "}
              Архив планов
            </label>
            <button
              disabled={busy || !pair}
              onClick={() => {
                if (pair && canLeave()) {
                  reset();
                  setDraft({
                    title: `${pair.subject} · ${pair.group}`,
                    groupId: pair.groupId,
                    subjectId: pair.subjectId,
                    start: options.start,
                    end: options.end,
                    topics: [topic()],
                  });
                  dirty.current = true;
                }
              }}
            >
              Создать план
            </button>
          </div>
          {loading ? (
            <p role="status">Загружаем планы…</p>
          ) : (
            <div className="curriculum-list">
              {!items.length && <p>Планов пока нет.</p>}
              {items.map((item) => (
                <button
                  key={item.id}
                  disabled={busy}
                  aria-pressed={selected?.id === item.id}
                  onClick={() => void open(item.id)}
                >
                  <strong>{item.title}</strong>
                  <br />
                  {item.start} — {item.end} · редакция {item.version}
                </button>
              ))}
            </div>
          )}
          <div className="curriculum-toolbar">
            <button
              disabled={busy || loading || offset === 0}
              onClick={() => {
                if (canLeave()) {
                  reset();
                  setOffset((v) => Math.max(0, v - 20));
                }
              }}
            >
              Предыдущие планы
            </button>
            <button
              disabled={busy || loading || !more}
              onClick={() => {
                if (canLeave()) {
                  reset();
                  setOffset((v) => v + 20);
                }
              }}
            >
              Следующие планы
            </button>
          </div>
        </>
      )}
      {draft && (
        <section aria-label="Редактор учебного плана">
          <h3>
            {selected
              ? `Редакция ${selected.version}${selected.archived ? " · архив" : ""}`
              : "Новый план"}
          </h3>
          {selected && (
            <p>
              {selected.actor} ·{" "}
              {new Date(selected.created).toLocaleString("ru")}
              {selected.version !== selected.latestVersion &&
                " · Просмотр истории: редактирование отключено"}
            </p>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <fieldset disabled={busy || !editable}>
              <label className="field">
                <span>Название плана</span>
                <input
                  required
                  maxLength={200}
                  value={draft.title}
                  onChange={(e) => change({ ...draft, title: e.target.value })}
                />
              </label>
              <div className="curriculum-toolbar">
                <label className="field">
                  <span>Начало периода</span>
                  <input
                    required
                    type="date"
                    disabled={!!selected}
                    value={draft.start}
                    onChange={(e) =>
                      change({ ...draft, start: e.target.value })
                    }
                  />
                </label>
                <label className="field">
                  <span>Конец периода</span>
                  <input
                    required
                    type="date"
                    disabled={!!selected}
                    value={draft.end}
                    onChange={(e) => change({ ...draft, end: e.target.value })}
                  />
                </label>
              </div>
              <p>
                Плановых часов:{" "}
                <strong>
                  {draft.topics.reduce((sum, t) => sum + t.hours, 0)}
                </strong>
                . Даты тем необязательны. Стандарты и результаты вводятся
                школой.
              </p>
              {draft.topics.map((t, i) => (
                <article className="curriculum-topic" key={t.id}>
                  <h4>Тема {i + 1}</h4>
                  <label className="field">
                    <span>Название темы {i + 1}</span>
                    <input
                      required
                      maxLength={300}
                      value={t.title}
                      onChange={(e) =>
                        changeTopic(t.id, { title: e.target.value })
                      }
                    />
                  </label>
                  <div className="curriculum-toolbar">
                    <label className="field">
                      <span>Плановых часов</span>
                      <input
                        required
                        type="number"
                        min={1}
                        max={1000}
                        value={t.hours}
                        onChange={(e) =>
                          changeTopic(t.id, { hours: Number(e.target.value) })
                        }
                      />
                    </label>
                    <label className="field">
                      <span>Плановая дата</span>
                      <input
                        type="date"
                        min={draft.start}
                        max={draft.end}
                        value={t.plannedDate || ""}
                        onChange={(e) =>
                          changeTopic(t.id, {
                            plannedDate: e.target.value || null,
                          })
                        }
                      />
                    </label>
                  </div>
                  <label className="field">
                    <span>Стандарт или ожидаемый результат</span>
                    <textarea
                      maxLength={2000}
                      value={t.standard}
                      onChange={(e) =>
                        changeTopic(t.id, { standard: e.target.value })
                      }
                    />
                  </label>
                  <TopicMaterials
                    key={`${selected?.id || "new"}:${selected?.version || 0}:${t.id}`}
                    workspace={workspace}
                    items={t.materials || []}
                    status={selected?.materialStatus || {}}
                    disabled={busy || !editable}
                    onChange={(materials) => changeTopic(t.id, { materials })}
                  />
                  <div className="curriculum-toolbar">
                    <button
                      type="button"
                      disabled={i === 0}
                      onClick={() => move(i, -1)}
                    >
                      Тему {i + 1} выше
                    </button>
                    <button
                      type="button"
                      disabled={i === draft.topics.length - 1}
                      onClick={() => move(i, 1)}
                    >
                      Тему {i + 1} ниже
                    </button>
                    <button
                      type="button"
                      disabled={draft.topics.length === 1}
                      onClick={() => {
                        if (
                          window.confirm(
                            "Убрать тему из текущего плана? Связи с прежними уроками сохранятся.",
                          )
                        )
                          change({
                            ...draft,
                            topics: draft.topics.filter((x) => x.id !== t.id),
                          });
                      }}
                    >
                      Убрать тему {i + 1}
                    </button>
                  </div>
                </article>
              ))}
              <div className="curriculum-toolbar">
                <button
                  type="button"
                  disabled={draft.topics.length >= 500}
                  onClick={() =>
                    change({ ...draft, topics: [...draft.topics, topic()] })
                  }
                >
                  Добавить тему
                </button>
                <button type="submit">Сохранить план</button>
              </div>
            </fieldset>
          </form>
          <div className="curriculum-toolbar">
            <button disabled={busy} onClick={exportPlan}>
              Скачать план CSV
            </button>
            {selected && (
              <>
                <button disabled={busy} onClick={() => void open(selected.id)}>
                  Открыть актуальную редакцию
                </button>
                <button disabled={busy} onClick={() => void loadHistory()}>
                  История плана
                </button>
                <button
                  disabled={busy || selected.version !== selected.latestVersion}
                  onClick={() => {
                    if (
                      !canLeave() ||
                      !window.confirm(
                        selected.archived
                          ? "Восстановить план?"
                          : "Перенести план в архив? Новые уроки не смогут выбирать его темы.",
                      )
                    )
                      return;
                    void run(async () => {
                      const item = await api(
                        `${base}/${selected.id}/archive`,
                        workspace,
                        "POST",
                        {
                          version: selected.version,
                          archived: !selected.archived,
                        },
                      );
                      if (alive.current) {
                        install(item);
                        setEpoch((v) => v + 1);
                        setHistory([]);
                        setNotice(
                          item.archived
                            ? "План перенесён в архив."
                            : "План восстановлен.",
                        );
                      }
                    });
                  }}
                >
                  {selected.archived ? "Восстановить план" : "В архив"}
                </button>
              </>
            )}
          </div>
          {!!history.length && (
            <div aria-label="История учебного плана">
              {history.map((h) => (
                <p key={h.version}>
                  <button
                    disabled={busy}
                    onClick={() => void open(selected!.id, h.version)}
                  >
                    Редакция {h.version}
                  </button>{" "}
                  · {h.actor} · {new Date(h.created).toLocaleString("ru")}
                  {h.archived && " · архив"}
                </p>
              ))}
              {historyMore && (
                <button disabled={busy} onClick={() => void loadHistory(true)}>
                  Ещё редакции
                </button>
              )}
            </div>
          )}
          {selected && (
            <details>
              <summary>Копия для другой группы или учебного года</summary>
              <p>
                Копия получит новые темы без прогресса и без плановых дат.
                Предмет сохранится. Выберите группу с действующей нагрузкой.
              </p>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!canLeave()) return;
                  void run(async () => {
                    const item = await api(
                      `${base}/${selected.id}/copy`,
                      workspace,
                      "POST",
                      {
                        version: selected.version,
                        groupId: copyGroup,
                        start: copyStart,
                        end: copyEnd,
                        omitUnavailableMaterials,
                      },
                    );
                    if (alive.current) {
                      install(item);
                      setProgress(null);
                      setHistory([]);
                      setArchived(false);
                      const i = options!.pairs.findIndex(
                        (p) =>
                          p.groupId === item.content.groupId &&
                          p.subjectId === item.content.subjectId,
                      );
                      setPairIndex(i);
                      setOffset(0);
                      setEpoch((v) => v + 1);
                      setNotice(
                        `Копия создана. Проверьте и заполните плановые даты тем.${item.omittedMaterials ? ` Пропущено недоступных материалов: ${item.omittedMaterials}.` : ""}`,
                      );
                    }
                  });
                }}
              >
                <fieldset disabled={busy}>
                  <label className="field">
                    <span>Группа для копии</span>
                    <select
                      value={copyGroup}
                      onChange={(e) => setCopyGroup(e.target.value)}
                    >
                      {options?.pairs
                        .filter(
                          (p) => p.subjectId === selected.content.subjectId,
                        )
                        .map((p) => (
                          <option key={p.groupId} value={p.groupId}>
                            {p.group}
                          </option>
                        ))}
                    </select>
                  </label>
                  <div className="curriculum-toolbar">
                    <label className="field">
                      <span>Начало копии</span>
                      <input
                        type="date"
                        required
                        value={copyStart}
                        onChange={(e) => setCopyStart(e.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span>Конец копии</span>
                      <input
                        type="date"
                        required
                        value={copyEnd}
                        onChange={(e) => setCopyEnd(e.target.value)}
                      />
                    </label>
                  </div>
                  <label>
                    <input
                      type="checkbox"
                      checked={omitUnavailableMaterials}
                      onChange={(e) =>
                        setOmitUnavailableMaterials(e.target.checked)
                      }
                    />{" "}
                    Копировать без недоступных материалов, если они есть
                  </label>
                  <button>Создать копию редакции {selected.version}</button>
                </fieldset>
              </form>
            </details>
          )}
          {selected && (
            <section aria-label="Прогресс учебного плана">
              <h3>Прогресс текущего плана</h3>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const r = await api(
                      `${base}/${selected.id}/progress`,
                      workspace,
                    );
                    if (alive.current) setProgress(r);
                  })
                }
              >
                Обновить прогресс
              </button>
              <p>
                Учитываются часы из опубликованных уроков до сегодняшнего дня.
                Черновики не считаются. Отменённые, исчезнувшие и перенесённые
                без обновления журнала уроки требуют сверки. Часы вводит
                учитель; это не автоматическая проверка проведения занятия.
              </p>
              {progress && (
                <>
                  <p>
                    На {progress.asOf}:{" "}
                    <strong>
                      {progress.totals.actual} из {progress.totals.planned} ч.
                    </strong>{" "}
                    · На сверку: {progress.totals.review} · Будущие публикации:{" "}
                    {progress.totals.upcoming}
                  </p>
                  <div className="curriculum-table">
                    <table>
                      <thead>
                        <tr>
                          <th>Тема</th>
                          <th>План</th>
                          <th>Учтено</th>
                          <th>На сверку</th>
                          <th>Будущие</th>
                        </tr>
                      </thead>
                      <tbody>
                        {progress.topics.map((t) => (
                          <tr key={t.id}>
                            <td>
                              {t.title}
                              {t.retired && " (убрана из текущего плана)"}
                            </td>
                            <td>{t.planned}</td>
                            <td>{t.actual}</td>
                            <td>{t.review}</td>
                            <td>{t.upcoming}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <details>
                    <summary>
                      Учтённые публикации и записи на сверку (
                      {progress.totalLessons})
                    </summary>
                    {progress.lessons.map((l) => (
                      <p key={l.lessonKey}>
                        {l.day} · {l.title} · {l.units} ч. ·{" "}
                        {
                          {
                            actual: "Учтено",
                            review: "На сверку",
                            upcoming: "Будущий урок",
                          }[l.status]
                        }{" "}
                        · журнал v{l.version}
                      </p>
                    ))}
                    <div className="curriculum-toolbar">
                      {[
                        [-50, "Предыдущие уроки"],
                        [50, "Следующие уроки"],
                      ].map(([delta, label]) => (
                        <button
                          key={label}
                          disabled={
                            busy ||
                            (Number(delta) < 0
                              ? progress.lessonOffset === 0
                              : !progress.hasMore)
                          }
                          onClick={() =>
                            void run(async () => {
                              const r = await api(
                                `${base}/${selected.id}/progress?lessonOffset=${Math.max(0, progress.lessonOffset + Number(delta))}`,
                                workspace,
                              );
                              if (alive.current) setProgress(r);
                            })
                          }
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </details>
                </>
              )}
            </section>
          )}
        </section>
      )}
    </section>
  );
}

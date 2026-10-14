import { useEffect, useRef, useState } from "react";
import { teacherNames } from "./utils";
import type { State } from "./types";
import { named } from "./utils";
import { patterns, activeIn } from "./cycles";
import { api } from "./api";
import { startPolling } from "./polling";
import ChangePreview from "./ChangePreview";
import type { Preview } from "./ChangePreview";

type Priorities = { early: number; stability: number; preferences: number };
type Score = Priorities & { total: number; placed: number };
const DEFAULT_PRIORITIES: Priorities = {
  early: 2,
  stability: 3,
  preferences: 1,
};
const PRIORITY_LABELS: Record<keyof Priorities, string> = {
  early: "Более ранние уроки",
  stability: "Сохранение прежних позиций",
  preferences: "Выполнение пожеланий",
};

type Job = {
  id: string;
  revision: number;
  status: string;
  seconds: number;
  optimization?: Priorities;
  message: string;
  created: string;
  elapsed?: number;
  attempts?: number;
  restarts?: number;
  progress: {
    phase: string;
    cycleName?: string;
    cycleIndex?: number;
    cycleTotal?: number;
    completed?: number;
    total?: number;
    solutions?: number;
    objective?: number;
  };
  result?: {
    preview?: Preview;
    details?: string[];
    optimal?: boolean;
    qualityPenalty?: number;
    previousQualityPenalty?: number;
    optimization?: { weights: Priorities; before: Score; after: Score };
  };
};
const ACTIVE = ["queued", "running", "cancelling"];
const STATUS: Record<string, string> = {
  queued: "В очереди",
  running: "Поиск",
  cancelling: "Остановка",
  ready: "Готов к просмотру",
  cancelled: "Отменён",
  stale: "Исходные данные изменились",
  blocked: "Требуются уточнения",
  infeasible: "Нет решения",
  timeout: "Время истекло",
  failed: "Ошибка",
  interrupted: "Прерван перезапуском",
};

export default function GenerationPanel({
  state: s,
  workspace,
  defaultScope,
  defaultCycle = "weekly",
  onApply,
}: {
  state: State;
  workspace: string;
  defaultScope: string;
  defaultCycle?: string;
  onApply: (preview: Preview) => Promise<void>;
}) {
  const [jobs, setJobs] = useState<Job[]>([]),
    [selected, setSelected] = useState("");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [scope, setScope] = useState(defaultScope),
    [query, setQuery] = useState("");
  const [custom, setCustom] = useState<string[]>([]);
  const [selectedCycles, setSelectedCycles] = useState<string[]>([
    defaultCycle,
  ]);
  const [seconds, setSeconds] = useState(30);
  const [priorities, setPriorities] = useState<Priorities>(DEFAULT_PRIORITIES);
  const polling = useRef<ReturnType<typeof startPolling> | null>(null);
  useEffect(() => {
    let alive = true,
      active = false;
    const poll = async () => {
      try {
        const result = await api("generation-jobs", workspace);
        if (alive) {
          setJobs(result);
          active = result.some((j: Job) => ACTIVE.includes(j.status));
          setError("");
        }
        return true;
      } catch (e: any) {
        if (alive) setError(e.message);
        return false;
      }
    };
    const controller = startPolling(poll, {
      interval: () => (active ? 1500 : 30000),
    });
    polling.current = controller;
    return () => {
      alive = false;
      controller.stop();
      if (polling.current === controller) polling.current = null;
    };
  }, [workspace]);
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const job = jobs.find((j) => j.id === selected);
  const matches = s.workloads.filter(
    (w) =>
      selectedCycles.some((id) => activeIn(w, id)) &&
      `${named(s.groups, w.groupId)} ${named(s.subjects, w.subjectId)} ${teacherNames(s, w)}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );
  return (
    <div className="generation-panel">
      <p className="muted">
        Поиск работает в фоне на сервере. Можно закрыть окно и продолжить
        работу. Результат сначала проверяется и показывается для сравнения;
        расписание изменится после применения.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            const workloadIds =
              scope === "all"
                ? undefined
                : scope === "custom"
                  ? custom
                  : s.workloads
                      .filter((w) =>
                        s.groups
                          .find((g) => g.id === w.groupId)
                          ?.classIds.includes(scope),
                      )
                      .map((w) => w.id);
            const result = await api("generation-jobs", workspace, "POST", {
              revision: s.revision,
              seconds,
              cycleIds: selectedCycles,
              workloadIds,
              optimization: priorities,
            });
            setJobs((current) => [
              result,
              ...current.filter((j) => j.id !== result.id),
            ]);
            setSelected(result.id);
            polling.current?.refresh();
          });
        }}
      >
        <fieldset className="rule-days">
          <legend>Недели для генерации</legend>
          {patterns(s).map((p) => (
            <label key={p.id} className="inline-check">
              <input
                type="checkbox"
                checked={selectedCycles.includes(p.id)}
                onChange={(e) =>
                  setSelectedCycles(
                    e.target.checked
                      ? [...selectedCycles, p.id]
                      : selectedCycles.filter((id) => id !== p.id),
                  )
                }
              />
              {p.name}
            </label>
          ))}
        </fieldset>
        <p className="muted small">
          Лимит поиска делится между выбранными неделями. Все найденные недели
          применяются одним действием после проверки; ошибка одной недели
          сохраняет исходное расписание целиком.
        </p>
        <label className="field">
          <span>Область генерации</span>
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="all">Вся школа</option>
            <option value="custom">Выбранные нагрузки</option>
            {s.classes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {scope === "custom" && (
          <div className="generation-loads">
            <label className="field">
              <span>Поиск нагрузки</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Группа, предмет, учитель"
              />
            </label>
            <p>
              Выбрано: {custom.length}. Для синхронного блока выберите все его
              нагрузки.
            </p>
            <button
              type="button"
              onClick={() =>
                setCustom([
                  ...new Set([...custom, ...matches.map((w) => w.id)]),
                ])
              }
            >
              Выбрать найденные ({matches.length})
            </button>
            <button type="button" onClick={() => setCustom([])}>
              Снять выбор
            </button>
            <div>
              {matches.slice(0, 100).map((w) => (
                <label key={w.id} className="inline-check">
                  <input
                    type="checkbox"
                    checked={custom.includes(w.id)}
                    onChange={(e) =>
                      setCustom(
                        e.target.checked
                          ? [...custom, w.id]
                          : custom.filter((id) => id !== w.id),
                      )
                    }
                  />
                  {named(s.groups, w.groupId)} ·{" "}
                  {named(s.subjects, w.subjectId)} · {teacherNames(s, w)}
                  {w.block ? ` · блок ${w.block}` : ""}
                </label>
              ))}
            </div>
            {matches.length > 100 && (
              <p>
                Показаны первые 100. Уточните поиск или выберите все найденные.
              </p>
            )}
          </div>
        )}
        <fieldset className="generation-priorities">
          <legend>Приоритеты поиска</legend>
          <p className="muted">
            Вес от 0 до 1000. Ноль отключает предпочтение; обязательные условия
            и закрепления сохраняются при любом весе.
          </p>
          <div className="form-grid">
            {(Object.keys(PRIORITY_LABELS) as (keyof Priorities)[]).map(
              (key) => (
                <label className="field" key={key}>
                  <span>{PRIORITY_LABELS[key]}</span>
                  <input
                    type="number"
                    min={0}
                    max={1000}
                    step={1}
                    required
                    value={priorities[key]}
                    onChange={(e) =>
                      setPriorities({
                        ...priorities,
                        [key]: Number(e.target.value),
                      })
                    }
                  />
                </label>
              ),
            )}
          </div>
          <p className="muted">
            Раннее размещение уменьшает сумму номеров периодов, начиная с нуля.
            Новая позиция — сочетание нагрузки, дня, периода и кабинета,
            которого раньше не было. Вес пожеланий умножает индивидуальные веса
            из раздела «Ограничения».
          </p>
          <button
            type="button"
            onClick={() => setPriorities(DEFAULT_PRIORITIES)}
          >
            Обычный баланс
          </button>{" "}
          <button
            type="button"
            onClick={() =>
              setPriorities({ early: 0, stability: 100, preferences: 1 })
            }
          >
            Меньше перестановок
          </button>{" "}
          <button
            type="button"
            onClick={() =>
              setPriorities({ early: 0, stability: 0, preferences: 0 })
            }
          >
            Любой допустимый вариант
          </button>
        </fieldset>
        <label className="field">
          <span>Лимит поиска, секунд</span>
          <input
            type="number"
            min={1}
            max={600}
            value={seconds}
            onChange={(e) => setSeconds(Number(e.target.value))}
          />
          <small>
            Подготовка модели и проверка результата выполняются дополнительно к
            этому времени. При доказанной невозможности подробный разбор может
            занять ещё до 10 секунд подготовки и до 10 секунд поиска.
          </small>
        </label>
        <button
          className="primary"
          disabled={
            busy ||
            !selectedCycles.length ||
            (scope === "custom" && !custom.length)
          }
        >
          Запустить фоновый поиск
        </button>
      </form>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      <h3>Последние поиски</h3>
      {!jobs.length && <p className="muted">Поисков пока нет.</p>}
      <div className="generation-jobs">
        {jobs.map((j) => (
          <article key={j.id} className={selected === j.id ? "chosen" : ""}>
            <div>
              <strong>{STATUS[j.status] || j.status}</strong>
              <small>
                {new Date(j.created).toLocaleString("ru")} · версия {j.revision}{" "}
                · лимит {j.seconds} с
                {j.elapsed !== undefined ? ` · прошло ${j.elapsed} с` : ""}
                {!!j.restarts && ` · восстановлений ${j.restarts}`}
              </small>
              {j.status === "running" && (
                <p role="status">
                  {j.progress.cycleName
                    ? `${j.progress.cycleName} (${j.progress.cycleIndex}/${j.progress.cycleTotal}). `
                    : ""}
                  {j.progress.phase === "building"
                    ? `Подготовка нагрузок: ${j.progress.completed || 0} / ${j.progress.total || "…"}`
                    : j.progress.phase === "building_rules"
                      ? `Подготовка правил: ${j.progress.completed || 0} / ${j.progress.total || "…"}`
                      : j.progress.phase === "reviewing"
                        ? "Проверяем найденное расписание перед просмотром."
                        : j.progress.phase === "diagnosing"
                          ? "Выясняем, какие условия несовместимы."
                          : `Найдено допустимых вариантов: ${j.progress.solutions || 0}. Идёт поиск улучшений.`}
                </p>
              )}
              {(!ACTIVE.includes(j.status) || j.status === "queued") && (
                <p>{j.message}</p>
              )}
            </div>
            {ACTIVE.includes(j.status) ? (
              <button
                disabled={busy || j.status === "cancelling"}
                onClick={() =>
                  run(async () => {
                    const result = await api(
                      `generation-jobs/${j.id}/cancel`,
                      workspace,
                      "POST",
                    );
                    setJobs((current) =>
                      current.map((x) => (x.id === j.id ? result : x)),
                    );
                  })
                }
              >
                Отменить поиск
              </button>
            ) : (
              <button disabled={busy} onClick={() => setSelected(j.id)}>
                Показать результат
              </button>
            )}
          </article>
        ))}
      </div>
      {job?.result?.details?.length ? (
        <div className="generation-warning">
          <strong>{job.message}</strong>
          <ul>
            {job.result.details.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {job?.result?.qualityPenalty !== undefined && (
        <p>
          Штраф пожеланий: {job.result.previousQualityPenalty} →{" "}
          {job.result.qualityPenalty}. Меньше — лучше по заданным весам.
        </p>
      )}
      {job?.optimization && (
        <p className="muted">
          Веса этого поиска: ранние уроки {job.optimization.early}, прежние
          позиции {job.optimization.stability}, пожелания{" "}
          {job.optimization.preferences}.
        </p>
      )}
      {job?.result?.optimization && (
        <div className="generation-score">
          <h3>Качество выбранных недель</h3>
          <table>
            <thead>
              <tr>
                <th>Показатель</th>
                <th>До</th>
                <th>После</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["placed", "Размещённые часы"],
                  ["early", "Сумма номеров периодов"],
                  ["stability", "Новые позиции"],
                  ["preferences", "Штраф пожеланий"],
                  ["total", "Взвешенная сумма"],
                ] as const
              ).map(([key, label]) => (
                <tr key={key}>
                  <th>{label}</th>
                  <td>{job.result!.optimization!.before[key]}</td>
                  <td>{job.result!.optimization!.after[key]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">
            Меньше взвешенная сумма — лучше по выбранным весам. Часы и позиции
            относятся к выбранной нагрузке; пожелания — ко всей выбранной
            неделе. Если исходный вариант неполный или нарушает обязательные
            условия, его меньший штраф не означает допустимое расписание.
          </p>
        </div>
      )}
      {job?.result?.preview &&
        (job.revision !== s.revision ? (
          <p className="generation-warning">
            Этот результат рассчитан для версии {job.revision}, текущая версия —{" "}
            {s.revision}. Запустите новый поиск: устаревший результат не
            применяется.
          </p>
        ) : (
          <ChangePreview
            key={job.result.preview.token}
            preview={job.result.preview}
            busy={busy}
            onApply={() => run(() => onApply(job.result!.preview!))}
            onCancel={() => setSelected("")}
            title={
              job.result.optimal
                ? "Лучший вариант по текущей целевой функции"
                : "Найденный допустимый вариант"
            }
          />
        ))}
    </div>
  );
}

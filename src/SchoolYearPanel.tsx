import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import "./SchoolYear.css";

type Action = "promote" | "graduate" | "keep";
interface Step {
  classId: string;
  action: Action;
  name: string | null;
}
interface PlanPeriod {
  name: string;
  kind: "quarter" | "semester" | "year" | "custom";
  start: string;
  end: string;
}
interface Plan {
  start: string;
  end: string;
  periods: PlanPeriod[];
  classes: Step[];
  timetable: "copy" | "empty";
}
interface PlanAnswer {
  version: number;
  saved: boolean;
  revision: number;
  plan: Plan;
  defaults: Plan;
  current: { id: string; name: string; start: string; end: string };
  canStart: boolean;
  classes: { id: string; name: string; pupils: number }[];
  counts: Record<"teachers" | "subjects" | "rooms" | "workloads" | "placements", number>;
}
interface Summary {
  year: { id: string; name: string; start: string; end: string };
  periods: PlanPeriod[];
  classes: { id: string; name: string; action: Action; newName: string | null; pupils: number }[];
  graduates: number;
  graduatesClass: { id: string; name: string } | null;
  timetable: "copy" | "empty";
  kept: Record<string, number>;
  dropped: Record<string, number>;
}
interface Problem {
  code: "dates" | "classesChanged" | "duplicateName" | "invalidState";
  names?: string[];
  message?: string;
}
interface Preview {
  summary: Summary | null;
  problems: Problem[];
  warnings: { openPeriods: string[] };
  canApply: boolean;
  canStart: boolean;
}

const STEPS = [
  "Даты и периоды",
  "Классы",
  "Учителя, предметы и расписание",
  "Проверка и запуск",
];
const KINDS: Record<PlanPeriod["kind"], string> = {
  quarter: "Четверть",
  semester: "Полугодие",
  year: "Год",
  custom: "Другой",
};
const ACTIONS: Record<Action, string> = {
  promote: "Перевести",
  graduate: "Выпуск",
  keep: "Оставить",
};
// What the new year does not carry: the dated items belong to the old year and stay in its archive.
const DROPPED: [string, string][] = [
  ["datedLessons", "изменения по датам"],
  ["absences", "отсутствия учителей"],
  ["commitments", "бронирования и дежурства"],
  ["scheduleDays", "«дни в расписании»"],
  ["courseRequests", "заявки на курсы"],
  ["teachingPeriods", "учебные периоды расписания"],
  ["events", "события календаря, которые закончились"],
];
const ru = (day: string) => day.split("-").reverse().join(".");
// The name of the year as the server writes it.
const yearName = (start: string, end: string) =>
  start && end ? `${start.slice(0, 4)}/${end.slice(2, 4)}` : "";

function problemText(p: Problem, end: string) {
  const names = (p.names ?? []).join(", ");
  if (p.code === "dates")
    return `Новый год должен начаться после окончания текущего (${ru(end)}).`;
  if (p.code === "classesChanged")
    return `Список классов изменился: ${names}. Обновите страницу и проверьте шаг «Классы».`;
  if (p.code === "duplicateName")
    return `Названия классов повторяются: ${names}.`;
  return p.message || "План нельзя применить к данным школы.";
}

/** The administrator's guided move to the next school year: dates and periods, classes, what is carried over, check and start. */
export default function SchoolYearPanel({
  workspace,
  onStarted,
}: {
  workspace: string;
  onStarted: () => Promise<void>;
}) {
  const [data, setData] = useState<PlanAnswer | null>(null),
    [plan, setPlan] = useState<Plan | null>(null),
    [step, setStep] = useState(0),
    [preview, setPreview] = useState<Preview | null>(null),
    [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [done, setDone] = useState("");
  // One id per confirmed attempt: a retry after a lost answer sends the same id and gets the stored result.
  const attempt = useRef<{ key: string; id: string } | null>(null);
  async function load() {
    const r: PlanAnswer = await api("school-years/plan", workspace);
    setData(r);
    setPlan(r.plan);
    setPreview(null);
    return r;
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [workspace]);
  useEffect(() => {
    if (step === 3 && plan && data) void check();
  }, [step]);
  if (!data || !plan)
    return error ? (
      <p className="error-banner" role="alert">
        {error}
      </p>
    ) : (
      <p role="status">Загружаю план…</p>
    );
  const current = data.current;
  const edit = (update: Partial<Plan>) => {
    setPlan({ ...plan, ...update });
    setPreview(null);
    setNotice("");
  };
  const stepOf = (id: string) => plan.classes.find((s) => s.classId === id);
  const setStepOf = (id: string, update: Partial<Step>) =>
    edit({
      classes: plan.classes.map((s) =>
        s.classId === id ? { ...s, ...update } : s,
      ),
    });
  const setPeriod = (index: number, update: Partial<PlanPeriod>) =>
    edit({
      periods: plan.periods.map((p, i) => (i === index ? { ...p, ...update } : p)),
    });
  async function save() {
    if (!plan) return;
    setBusy(true);
    setError("");
    try {
      const r = await api("school-years/plan", workspace, "PUT", {
        version: data!.version,
        plan,
      });
      setData({ ...data!, version: r.version, saved: true });
      setNotice("Черновик сохранён.");
    } catch (e: any) {
      setError(
        e.status === 409
          ? "Черновик изменён в другом окне. Обновите страницу."
          : e.message,
      );
    } finally {
      setBusy(false);
    }
  }
  async function check() {
    if (!plan || !data) return;
    setBusy(true);
    setError("");
    try {
      const r: Preview = await api("school-years/preview", workspace, "POST", {
        revision: data.revision,
        plan,
      });
      setPreview(r);
      setAck(false);
    } catch (e: any) {
      setPreview(null);
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function begin() {
    if (!plan || !data || !preview?.summary) return;
    const name = preview.summary.year.name;
    if (
      !window.confirm(
        `Начать учебный год ${name}? Учебный год ${current.name} уйдёт в архив и станет доступен только для просмотра. Отменить запуск нельзя.`,
      )
    )
      return;
    const key = JSON.stringify([plan, data.revision, ack]);
    if (attempt.current?.key !== key)
      attempt.current = { key, id: crypto.randomUUID().replaceAll("-", "") };
    setBusy(true);
    setError("");
    try {
      await api("school-years/start", workspace, "POST", {
        requestId: attempt.current.id,
        revision: data.revision,
        plan,
        acknowledgeWarnings: ack,
      });
      attempt.current = null;
      setDone(`Начат учебный год ${name}. ${current.name} в архиве.`);
      await onStarted();
      await load().catch(() => {});
    } catch (e: any) {
      // A refusal from the server is final; only a lost answer keeps the id for the retry.
      if (e.status) attempt.current = null;
      setError(e.message);
      if (e.detail?.reason === "plan") await check();
    } finally {
      setBusy(false);
    }
  }
  if (done)
    return (
      <section className="year-wizard">
        <p className="info-line" role="status">
          {done}
        </p>
      </section>
    );
  const graduating = plan.classes.filter((s) => s.action === "graduate");
  const graduates = data.classes
    .filter((c) => graduating.some((s) => s.classId === c.id))
    .reduce((n, c) => n + c.pupils, 0);
  const summary = preview?.summary;
  const warnings = preview?.warnings.openPeriods ?? [];
  const ready =
    !!preview?.canApply && !!preview.canStart && (!warnings.length || ack);
  return (
    <section className="year-wizard" aria-label="Новый учебный год">
      <p className="info-line">
        Черновик можно готовить, пока идёт текущий год. Запуск доступен после
        его окончания, один раз и без отмены. Перед запуском создайте полную
        резервную копию (BACKUPS.md).
      </p>
      <ol className="year-steps">
        {STEPS.map((name, index) => (
          <li key={name}>
            <button
              type="button"
              aria-current={index === step ? "step" : undefined}
              onClick={() => setStep(index)}
            >
              <b>{index + 1}</b> {name}
            </button>
          </li>
        ))}
      </ol>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="info-line" role="status">
          {notice}
        </p>
      )}
      {step === 0 && (
        <div className="year-step">
          <h2>Даты и периоды</h2>
          <div className="year-dates">
            <label className="field">
              <span>Начало нового года</span>
              <input
                type="date"
                value={plan.start}
                onChange={(e) => edit({ start: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Конец нового года</span>
              <input
                type="date"
                value={plan.end}
                onChange={(e) => edit({ end: e.target.value })}
              />
            </label>
            <p className="year-name">
              Название года: <strong>{yearName(plan.start, plan.end)}</strong>
            </p>
          </div>
          <div className="table-card">
            <div className="table-scroll">
              <table className="year-table">
                <thead>
                  <tr>
                    <th>Период</th>
                    <th>Вид</th>
                    <th>Начало</th>
                    <th>Конец</th>
                    <th aria-label="Действия" />
                  </tr>
                </thead>
                <tbody>
                  {plan.periods.map((p, i) => (
                    <tr key={i}>
                      <td>
                        <input
                          aria-label="Название периода"
                          maxLength={120}
                          value={p.name}
                          onChange={(e) => setPeriod(i, { name: e.target.value })}
                        />
                      </td>
                      <td>
                        <select
                          aria-label="Вид периода"
                          value={p.kind}
                          onChange={(e) =>
                            setPeriod(i, { kind: e.target.value as PlanPeriod["kind"] })
                          }
                        >
                          {Object.entries(KINDS).map(([id, label]) => (
                            <option key={id} value={id}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          type="date"
                          aria-label="Начало периода"
                          value={p.start}
                          onChange={(e) => setPeriod(i, { start: e.target.value })}
                        />
                      </td>
                      <td>
                        <input
                          type="date"
                          aria-label="Конец периода"
                          value={p.end}
                          onChange={(e) => setPeriod(i, { end: e.target.value })}
                        />
                      </td>
                      <td>
                        <button
                          type="button"
                          onClick={() =>
                            edit({ periods: plan.periods.filter((_, k) => k !== i) })
                          }
                        >
                          Удалить
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!plan.periods.length && (
                    <tr>
                      <td colSpan={5}>Периодов нет: добавьте четверти или полугодия.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <button
            type="button"
            disabled={plan.periods.length >= 12}
            onClick={() =>
              edit({
                periods: [
                  ...plan.periods,
                  { name: "", kind: "quarter", start: plan.start, end: plan.start },
                ],
              })
            }
          >
            Добавить период
          </button>
        </div>
      )}
      {step === 1 && (
        <div className="year-step">
          <h2>Классы</h2>
          <div className="table-card">
            <div className="table-scroll">
              <table className="year-table">
                <thead>
                  <tr>
                    <th>Класс</th>
                    <th>Учеников</th>
                    <th>Действие</th>
                    <th>Новое название</th>
                  </tr>
                </thead>
                <tbody>
                  {data.classes.map((c) => {
                    const s = stepOf(c.id);
                    if (!s) return null;
                    return (
                      <tr key={c.id}>
                        <td>
                          <strong>{c.name}</strong>
                        </td>
                        <td>{c.pupils}</td>
                        <td>
                          <select
                            aria-label={`Действие для класса ${c.name}`}
                            value={s.action}
                            onChange={(e) =>
                              setStepOf(c.id, {
                                action: e.target.value as Action,
                                name:
                                  e.target.value === "promote"
                                    ? (s.name ?? c.name)
                                    : null,
                              })
                            }
                          >
                            {Object.entries(ACTIONS).map(([id, label]) => (
                              <option key={id} value={id}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          {s.action === "promote" && (
                            <input
                              aria-label={`Новое название класса ${c.name}`}
                              maxLength={160}
                              value={s.name ?? ""}
                              onChange={(e) => setStepOf(c.id, { name: e.target.value })}
                            />
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          <p className="year-name">
            {graduating.length
              ? `Выпускников: ${graduates}. Они переходят в класс «Выпуск ${current.end.slice(0, 4)}» и остаются в архиве.`
              : "Выпуска нет: класс выпускников не создаётся."}
          </p>
        </div>
      )}
      {step === 2 && (
        <div className="year-step">
          <h2>Учителя, предметы и расписание</h2>
          <p>
            Переходят без изменений: учителей {data.counts.teachers}, предметов{" "}
            {data.counts.subjects}, кабинетов {data.counts.rooms}. Нагрузка:{" "}
            {data.counts.workloads}, уроков в сетке: {data.counts.placements}.
          </p>
          <fieldset className="year-choice">
            <legend>Расписание нового года</legend>
            <label>
              <input
                type="radio"
                name="timetable"
                checked={plan.timetable === "copy"}
                onChange={() => edit({ timetable: "copy" })}
              />{" "}
              Расписание из текущего года
            </label>
            <label>
              <input
                type="radio"
                name="timetable"
                checked={plan.timetable === "empty"}
                onChange={() => edit({ timetable: "empty" })}
              />{" "}
              Пустое расписание, нагрузка сохраняется
            </label>
          </fieldset>
        </div>
      )}
      {step === 3 && (
        <div className="year-step">
          <h2>Проверка и запуск</h2>
          {!preview && busy && <p role="status">Проверяю план…</p>}
          {preview && (
            <>
              {!!preview.problems.length && (
                <div className="error-banner" role="alert">
                  <ul>
                    {preview.problems.map((p, i) => (
                      <li key={i}>{problemText(p, current.end)}</li>
                    ))}
                  </ul>
                </div>
              )}
              {summary && (
                <div className="year-summary">
                  <p>
                    Новый год: <strong>{summary.year.name}</strong>,{" "}
                    {ru(summary.year.start)} — {ru(summary.year.end)}.{" "}
                    {current.name} уходит в архив. Периодов: {summary.periods.length}.
                  </p>
                  <ul>
                    {summary.classes.map((c) => (
                      <li key={c.id}>
                        {c.action === "promote"
                          ? `${c.name} → ${c.newName}`
                          : c.action === "graduate"
                            ? `${c.name} — выпуск`
                            : `${c.name} — остаётся`}{" "}
                        ({c.pupils})
                      </li>
                    ))}
                  </ul>
                  <p>
                    Выпускников: {summary.graduates}
                    {summary.graduatesClass
                      ? `, класс «${summary.graduatesClass.name}»`
                      : ""}
                    . Расписание:{" "}
                    {summary.timetable === "copy" ? "из текущего года" : "пустое"}.
                  </p>
                  <p>
                    Сохраняются: учителей {summary.kept.teachers}, предметов{" "}
                    {summary.kept.subjects}, кабинетов {summary.kept.rooms}, классов{" "}
                    {summary.kept.classes}, учеников {summary.kept.students}, групп{" "}
                    {summary.kept.groups}, нагрузок {summary.kept.workloads}, уроков
                    в сетке {summary.kept.placements}.
                  </p>
                  <p>
                    Не переносятся (остаются в архиве):{" "}
                    {[
                      ...DROPPED.map(([key, label]) => [label, summary.dropped[key]] as const),
                      ["группы выпускных классов", summary.dropped.groups],
                      ["нагрузки и уроки выпускных классов", summary.dropped.workloads],
                    ]
                      .filter(([, n]) => n)
                      .map(([label, n]) => `${label}: ${n}`)
                      .join("; ") || "ничего"}
                    .
                  </p>
                </div>
              )}
              {!!warnings.length && (
                <label className="year-ack">
                  <input
                    type="checkbox"
                    checked={ack}
                    onChange={(e) => setAck(e.target.checked)}
                  />{" "}
                  Понимаю: открытые периоды текущего года после запуска изменить
                  нельзя ({warnings.join(", ")})
                </label>
              )}
              {!preview.canStart && (
                <p className="info-line">
                  Запуск доступен после окончания текущего учебного года (
                  {ru(current.end)}).
                </p>
              )}
            </>
          )}
          <div className="year-actions">
            <button type="button" disabled={busy} onClick={() => void check()}>
              Проверить ещё раз
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy || !ready}
              onClick={() => void begin()}
            >
              Начать новый учебный год
            </button>
          </div>
        </div>
      )}
      <div className="year-actions">
        <button type="button" disabled={step === 0} onClick={() => setStep(step - 1)}>
          Назад
        </button>
        <button type="button" disabled={busy} onClick={() => void save()}>
          Сохранить черновик
        </button>
        <button
          type="button"
          disabled={step === STEPS.length - 1}
          onClick={() => setStep(step + 1)}
        >
          Далее
        </button>
      </div>
    </section>
  );
}

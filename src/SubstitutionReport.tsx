import { useEffect, useState } from "react";
import { api } from "./api";
import { download } from "./utils";
import type { State, SubstitutionWeights } from "./types";
import {
  CHANGE_TYPES,
  DEFAULT_WEIGHTS,
  WEIGHT_LABELS,
  reportCsv,
  type ReportTeacher,
  type SubstitutionReportData,
} from "./substitutions";

const currentMonth = (s: State) => {
  const today = new Date().toLocaleDateString("sv-SE").slice(0, 7);
  const first = s.settings.yearStart.slice(0, 7);
  return today < first || today > s.settings.yearEnd.slice(0, 7)
    ? first
    : today;
};
function Figures({ t }: { t: ReportTeacher }) {
  const types = (["substitution", "merge", "self_study", "move"] as const)
    .filter((k) => t.byType[k])
    .map((k) => `${CHANGE_TYPES[k]}: ${t.byType[k]}`)
    .join(", ");
  return (
    <dl className="report-figures">
      <div>
        <dt>По расписанию</dt>
        <dd>{t.planned}</dd>
      </div>
      <div>
        <dt>Проведено</dt>
        <dd>{t.held}</dd>
        <small>
          свои {t.own}, за других {t.substitutions}, дополнительные {t.extra}
        </small>
      </div>
      <div>
        <dt>Замены за других</dt>
        <dd>{t.substitutions}</dd>
        {types && <small>{types}</small>}
      </div>
      <div>
        <dt>Пропущено</dt>
        <dd>{t.missed}</dd>
        <small>
          при отсутствии {t.missedAbsent}, без замены {t.uncovered ?? 0},
          отменено {t.cancelled}
        </small>
      </div>
      <div>
        <dt>Перенесено</dt>
        <dd>{t.moved}</dd>
      </div>
      <div>
        <dt>Предстоит</dt>
        <dd>{t.upcoming ?? 0}</dd>
      </div>
    </dl>
  );
}
const asDraft = (w: SubstitutionWeights) =>
  Object.fromEntries(Object.entries(w).map(([k, v]) => [k, String(v)]));

// Monthly totals from the dated schedule, and the weights used to rank substitutes.
export default function SubstitutionReport({
  state: s,
  workspace,
  onState,
}: {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
}) {
  const [month, setMonth] = useState(() => currentMonth(s)),
    [teacherId, setTeacherId] = useState("");
  const [report, setReport] = useState<SubstitutionReportData | null>(null),
    [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState(
    asDraft(s.settings.substitutionWeights ?? DEFAULT_WEIGHTS),
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(
    () => setDraft(asDraft(s.settings.substitutionWeights ?? DEFAULT_WEIGHTS)),
    [s.revision, s.settings.substitutionWeights],
  );
  useEffect(() => {
    if (!month) return;
    let live = true;
    setLoading(true);
    const filter = teacherId
      ? `&teacherId=${encodeURIComponent(teacherId)}`
      : "";
    api(
      `calendar/substitution-report?month=${encodeURIComponent(month)}${filter}`,
      workspace,
    )
      .then((r) => {
        if (live) {
          setReport(r);
          setError("");
        }
      })
      .catch((e) => {
        if (live) {
          setReport(null);
          setError(e.message);
        }
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [month, teacherId, workspace, s.revision]);
  async function saveWeights() {
    if (busy) return;
    const values = Object.values(draft).map((v) =>
      v.trim() === "" ? NaN : Number(v),
    );
    if (values.some((v) => !Number.isInteger(v) || v < 0 || v > 100)) {
      setError("Вес — целое число от 0 до 100.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const weights = Object.fromEntries(
        Object.entries(draft).map(([k, v]) => [k, Number(v)]),
      );
      await onState(
        await api("calendar/substitution-weights", workspace, "PUT", {
          revision: s.revision,
          weights,
        }),
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="dated-records substitution-report">
      <div className="dated-toolbar">
        <label className="field">
          <span>Месяц</span>
          <input
            type="month"
            value={month}
            min={s.settings.yearStart.slice(0, 7)}
            max={s.settings.yearEnd.slice(0, 7)}
            onChange={(e) => setMonth(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Учитель</span>
          <select
            value={teacherId}
            onChange={(e) => setTeacherId(e.target.value)}
          >
            <option value="">Все учителя</option>
            {s.teachers.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={!report || !report.teachers.length}
          onClick={() =>
            report &&
            download(
              `Итоги-замен-${month}.csv`,
              reportCsv(report),
              "text/csv;charset=utf-8",
            )
          }
        >
          Скачать CSV
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <p className="muted">
        Проведено — только завершившиеся уроки по расписанию того дня, с
        заменами и отменами; заполнение журнала не учитывается. Часы с
        подтверждением журналом — в отчёте «Нагрузка учителей». Перенесённый
        урок считается в дату проведения, группа, присоединённая к другому
        уроку, часов не добавляет.
      </p>
      {loading && <p role="status">Считаю итоги…</p>}
      {report && !loading && (
        <>
          {!report.teachers.length && <p>За месяц нет уроков и замен.</p>}
          {report.teachers.map((t) => (
            <article key={t.teacherId}>
              <div>
                <strong>{t.name}</strong>
                <Figures t={t} />
              </div>
            </article>
          ))}
          {report.teachers.length > 1 && (
            <article>
              <div>
                <strong>Итого</strong>
                <Figures t={report.totals} />
              </div>
            </article>
          )}
        </>
      )}
      <form
        className="dated-form"
        onSubmit={(e) => {
          e.preventDefault();
          void saveWeights();
        }}
      >
        <h3>Настройка подбора замен</h3>
        <p className="info-line">
          Баллы за каждый критерий при подборе заменяющего. Свободное время
          проверяется всегда и баллами не заменяется. Ноль отключает критерий.
        </p>
        <div className="dated-fields">
          {WEIGHT_LABELS.map(([key, label]) => (
            <label className="field" key={key}>
              <span>{label}</span>
              <input
                type="number"
                min={0}
                max={100}
                step={1}
                required
                value={draft[key] ?? ""}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <div className="modal-actions">
          <button
            type="button"
            disabled={busy}
            onClick={() => setDraft(asDraft(DEFAULT_WEIGHTS))}
          >
            Вернуть стандартные
          </button>
          <button className="primary" disabled={busy}>
            Сохранить веса
          </button>
        </div>
      </form>
    </div>
  );
}

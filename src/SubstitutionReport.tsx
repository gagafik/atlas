import { Fragment, useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "./api";
import { download } from "./utils";
import type { State, SubstitutionWeights } from "./types";
import {
  DEFAULT_WEIGHTS,
  WEIGHT_LABELS,
  reportCsv,
  type ReportTeacher,
  type SubstitutionReportData,
} from "./substitutions";
import { REPORT_COLUMNS, breakdownLines } from "./datedLists";
import "./DatedDay.css";
import "./DatedLists.css";

const currentMonth = (s: State) => {
  const today = new Date().toLocaleDateString("sv-SE").slice(0, 7);
  const first = s.settings.yearStart.slice(0, 7);
  return today < first || today > s.settings.yearEnd.slice(0, 7)
    ? first
    : today;
};
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
  const [open, setOpen] = useState<Record<string, boolean>>({});
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
  // One row of figures, closed to a single line and opened to the breakdowns of the former cards.
  const figures = (
    id: string,
    t: ReportTeacher,
    name: string,
    total: boolean,
  ) => {
    const toggle = (
      <button
        type="button"
        className="dl-toggle"
        aria-expanded={!!open[id]}
        aria-controls={`dl-more-${id}`}
        title="Показать расшифровку"
        onClick={() => setOpen({ ...open, [id]: !open[id] })}
      >
        {open[id] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>{name}</span>
      </button>
    );
    return (
      <Fragment key={id}>
        <tr>
          {total ? (
            <th scope="row" className="dl-cell dl-text">
              {toggle}
            </th>
          ) : (
            <td className="dl-cell dl-text" title={name}>
              {toggle}
            </td>
          )}
          {REPORT_COLUMNS.map((c) => (
            <td key={c.key} className="dl-cell dl-num">
              {c.value(t)}
            </td>
          ))}
        </tr>
        {open[id] && (
          <tr className="dl-more" id={`dl-more-${id}`}>
            <td className="dl-cell" colSpan={REPORT_COLUMNS.length + 1}>
              {breakdownLines(t).map((l) => (
                <span key={l}>{l}</span>
              ))}
            </td>
          </tr>
        )}
      </Fragment>
    );
  };
  return (
    <div className="dl substitution-report">
      <div className="dd-controls">
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
          type="button"
          className="dd-btn"
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
      <details className="dd-help">
        <summary>Как считаются итоги</summary>
        <p>
          Проведено — только завершившиеся уроки по расписанию того дня, с
          заменами и отменами; заполнение журнала не учитывается. Часы с
          подтверждением журналом — в отчёте «Нагрузка учителей». Перенесённый
          урок считается в дату проведения, группа, присоединённая к другому
          уроку, часов не добавляет. Урок учителя с внесённым отсутствием и без
          замены считается «Без замены» и «Пропущено» сразу, в том числе на
          будущие даты.
        </p>
      </details>
      {loading && <p role="status">Считаю итоги…</p>}
      {report && !loading && !report.teachers.length && (
        <p>За месяц нет уроков и замен.</p>
      )}
      {report && !loading && !!report.teachers.length && (
        <div className="table-card dl-scroll">
          <table className="data-table dl-table dl-totals">
            <caption className="sr-only">
              Итоги замен за месяц {report.month}
            </caption>
            <thead>
              <tr>
                <th scope="col" className="dl-cell dl-text">
                  Учитель
                </th>
                {REPORT_COLUMNS.map((c) => (
                  <th key={c.key} scope="col" className="dl-cell dl-num">
                    <abbr title={c.title}>{c.label}</abbr>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.teachers.map((t) =>
                figures(t.teacherId, t, t.name, false),
              )}
            </tbody>
            {report.teachers.length > 1 && (
              <tfoot>{figures("totals", report.totals, "Итого", true)}</tfoot>
            )}
          </table>
        </div>
      )}
      <details className="dl-weights">
        <summary>Настройка подбора замен</summary>
        <form
          className="dated-form"
          onSubmit={(e) => {
            e.preventDefault();
            void saveWeights();
          }}
        >
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
                  onChange={(e) =>
                    setDraft({ ...draft, [key]: e.target.value })
                  }
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
      </details>
    </div>
  );
}

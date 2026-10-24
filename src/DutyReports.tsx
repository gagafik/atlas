import { useEffect, useState } from "react";
import { api } from "./api";
import { download } from "./utils";
import {
  clamp,
  fairnessCsv,
  generationRanges,
  minutesText,
  monthRange,
  rosterCsv,
  shortDate,
  weekRange,
  type DutyProps,
  type DutySettings,
  type FairnessData,
  type RosterData,
} from "./dutySchedule";

export function DutyRoster({ state: s, workspace }: DutyProps) {
  const today = new Date().toLocaleDateString("sv-SE");
  const [mode, setMode] = useState<"week" | "month">("week"),
    [date, setDate] = useState(
      clamp(today, s.settings.yearStart, s.settings.yearEnd),
    ),
    [view, setView] = useState<"grid" | "list">("grid");
  const [data, setData] = useState<RosterData | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const [start, end] = mode === "week" ? weekRange(date) : monthRange(date);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    api(`duties/roster?start=${start}&end=${end}`, workspace)
      .then((r) => {
        if (live) setData(r);
      })
      .catch((e) => {
        if (live) {
          setData(null);
          setError(e.message);
        }
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [workspace, start, end, s.revision]);
  const cells = new Map(
    (data?.cells || []).map((c) => [`${c.place}|${c.date}`, c.items]),
  );
  return (
    <section className="duty-roster">
      <div className="dated-toolbar duty-controls">
        <div className="exchange-tabs" role="group" aria-label="Период печати">
          {(
            [
              ["week", "Неделя"],
              ["month", "Месяц"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              className={mode === id ? "selected" : ""}
              onClick={() => setMode(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="field">
          <span>Дата внутри периода</span>
          <input
            type="date"
            min={s.settings.yearStart}
            max={s.settings.yearEnd}
            value={date}
            onChange={(e) => e.target.value && setDate(e.target.value)}
          />
        </label>
        <div className="exchange-tabs" role="group" aria-label="Вид графика">
          {(
            [
              ["grid", "Сетка по местам"],
              ["list", "Список по сотрудникам"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              className={view === id ? "selected" : ""}
              onClick={() => setView(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <button disabled={!data} onClick={() => window.print()}>
          Печать
        </button>
        <button
          disabled={!data?.cells.length}
          onClick={() =>
            data &&
            download(
              "График дежурств.csv",
              "﻿" + rosterCsv(data),
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
      {loading && <p role="status">Загружаю график…</p>}
      {data && (
        <div className="duty-print">
          <h3 className="duty-print-title">
            График дежурств · {data.school} · {shortDate(start)}–
            {shortDate(end)}
          </h3>
          {!data.places.length ? (
            <p className="empty-state">На выбранный период дежурств нет.</p>
          ) : view === "grid" ? (
            <div className="duty-grid-scroll">
              <table className="duty-grid">
                <thead>
                  <tr>
                    <th scope="col">Место</th>
                    {data.days.map((d) => (
                      <th
                        scope="col"
                        key={d.date}
                        className={d.closed ? "closed" : ""}
                      >
                        {d.name.slice(0, 3)}, {shortDate(d.date).slice(0, 5)}
                        {d.closed && <small>{d.closed}</small>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.places.map((p) => (
                    <tr key={p.key}>
                      <th scope="row">{p.label}</th>
                      {data.days.map((d) => (
                        <td key={d.date} className={d.closed ? "closed" : ""}>
                          {(cells.get(`${p.key}|${d.date}`) || []).map((i) => (
                            <div className="duty-slot" key={i.id}>
                              <span className="duty-time">
                                {i.start}–{i.end}
                              </span>
                              <span>{i.teachers.join(", ")}</span>
                              {i.missing > 0 && (
                                <span className="duty-free">
                                  свободно: {i.missing}
                                </span>
                              )}
                            </div>
                          ))}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="duty-people">
              {!data.teachers.length && (
                <p className="empty-state">Дежурные ещё не назначены.</p>
              )}
              {data.teachers.map((t) => (
                <section key={t.teacherId}>
                  <h4>
                    {t.name} · {minutesText(t.minutes)}
                  </h4>
                  <table>
                    <tbody>
                      {t.items.map((i, n) => (
                        <tr key={n}>
                          <td>{shortDate(i.date)}</td>
                          <td>{i.weekday}</td>
                          <td>
                            {i.start}–{i.end}
                          </td>
                          <td>{i.place}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              ))}
            </div>
          )}
          {data.unfilled > 0 && (
            <p className="duty-free">
              Не хватает дежурных на периоде: {data.unfilled}.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export function DutyFairness({
  state: s,
  workspace,
  onAllocate,
}: DutyProps & { onAllocate?: () => void }) {
  const ranges = generationRanges(s);
  const [rangeId, setRangeId] = useState("year"),
    [from, setFrom] = useState(s.settings.yearStart),
    [to, setTo] = useState(s.settings.yearEnd);
  const [data, setData] = useState<FairnessData | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!from || !to) return;
    let live = true;
    setLoading(true);
    setError("");
    api(`duties/fairness?start=${from}&end=${to}`, workspace)
      .then((r) => {
        if (live) setData(r);
      })
      .catch((e) => {
        if (live) {
          setData(null);
          setError(e.message);
        }
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [workspace, from, to, s.revision]);
  function pick(id: string) {
    setRangeId(id);
    const range = ranges.find((r) => r.id === id);
    if (range && id !== "custom") {
      setFrom(range.start);
      setTo(range.end);
    }
  }
  const summary = data?.summary;
  return (
    <section className="duty-fairness">
      <div className="dated-fields">
        <label className="field">
          <span>Период</span>
          <select value={rangeId} onChange={(e) => pick(e.target.value)}>
            {ranges.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>С даты</span>
          <input
            type="date"
            min={s.settings.yearStart}
            max={s.settings.yearEnd}
            disabled={rangeId !== "custom"}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label className="field">
          <span>По дату</span>
          <input
            type="date"
            min={s.settings.yearStart}
            max={s.settings.yearEnd}
            disabled={rangeId !== "custom"}
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">Считаю минуты…</p>}
      {data && summary && (
        <>
          <p>
            Назначено: <strong>{minutesText(summary.assignedMinutes)}</strong>{" "}
            из требуемых <strong>{minutesText(summary.requiredMinutes)}</strong>
            ; в среднем {summary.mean} мин. на сотрудника, от {summary.min} до{" "}
            {summary.max}. Мест без дежурного: {summary.unfilledPlaces}.
          </p>
          <div className="dated-toolbar">
            <button
              disabled={!data.rows.length}
              onClick={() =>
                download(
                  "Минуты дежурств.csv",
                  "﻿" + fairnessCsv(data),
                  "text/csv;charset=utf-8",
                )
              }
            >
              Скачать CSV
            </button>
            {onAllocate && (
              <button onClick={onAllocate}>Распределить заново</button>
            )}
          </div>
          {!data.rows.length ? (
            <p className="empty-state">За период нет дежурств.</p>
          ) : (
            <div className="duty-grid-scroll">
              <table className="duty-fair">
                <thead>
                  <tr>
                    <th scope="col">Сотрудник</th>
                    <th scope="col">Дежурств</th>
                    <th scope="col">Минут</th>
                    <th scope="col">Отклонение</th>
                    <th scope="col">Мест</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.teacherId}>
                      <th scope="row">{r.name}</th>
                      <td>{r.duties}</td>
                      <td>{r.minutes}</td>
                      <td>
                        {r.deviation > 0 ? "+" : ""}
                        {r.deviation}
                      </td>
                      <td>{r.places}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted">
            В список входят все, кому разрешено дежурить, в том числе с нулём
            минут. Отклонение считается от среднего по этому списку.
            Автоматическое выравнивание минут остаётся во вкладке «Дежурства».
          </p>
        </>
      )}
    </section>
  );
}

export function DutyReminders({ workspace }: DutyProps) {
  const [settings, setSettings] = useState<DutySettings | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  useEffect(() => {
    let live = true;
    api("duties/options", workspace)
      .then((r) => {
        if (live) setSettings(r.settings);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  async function save() {
    if (!settings || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const { version, ...value } = settings;
      setSettings(
        await api("duties/settings", workspace, "PUT", { version, ...value }),
      );
      setNotice("Настройки напоминаний сохранены.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  if (!settings)
    return error ? (
      <p className="error-banner" role="alert">
        {error}
      </p>
    ) : (
      <p role="status">Загружаю настройки…</p>
    );
  return (
    <form
      className="dated-form duty-reminders"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <h3>Напоминания дежурным</h3>
      <label className="check-line">
        <input
          type="checkbox"
          checked={settings.enabled}
          onChange={(e) =>
            setSettings({ ...settings, enabled: e.target.checked })
          }
        />
        Напоминать о дежурстве
      </label>
      <div className="dated-fields">
        <label className="field">
          <span>Когда напоминать</span>
          <select
            value={settings.daysBefore}
            onChange={(e) =>
              setSettings({
                ...settings,
                daysBefore: Number(e.target.value) as 0 | 1,
              })
            }
          >
            <option value={1}>Накануне</option>
            <option value={0}>В день дежурства</option>
          </select>
        </label>
        <label className="field">
          <span>Во сколько (время школы)</span>
          <input
            type="time"
            required
            value={settings.remindAt}
            onChange={(e) =>
              setSettings({ ...settings, remindAt: e.target.value })
            }
          />
        </label>
      </div>
      <label className="check-line">
        <input
          type="checkbox"
          checked={settings.notifyChanges}
          onChange={(e) =>
            setSettings({ ...settings, notifyChanges: e.target.checked })
          }
        />
        Сообщать сотруднику об изменении назначений (один раз на сохранение)
      </label>
      <p className="muted">
        Напоминание приходит в уведомления один раз на дежурство, даже после
        перезапуска сервера. Если назначение сделано, когда напоминание уже пора
        отправить, сотрудник получает только сообщение об изменении. Фоновое
        (push) уведомление не содержит подробностей: текст виден после входа.
        Сотрудники без учётной записи уведомления не получают.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="modal-actions">
        <button className="primary" disabled={busy}>
          Сохранить
        </button>
      </div>
    </form>
  );
}

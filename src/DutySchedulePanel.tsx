import { useEffect, useRef, useState } from "react";
import type { State } from "./types";
import { api } from "./api";
import { named } from "./utils";
import { Multi } from "./DatedSchedule";
import ChangePreview, { type Preview } from "./ChangePreview";
import { DutyFairness, DutyReminders, DutyRoster } from "./DutyReports";
import DutySwaps from "./DutySwaps";
import {
  SKIP_KINDS,
  breakAfter,
  generationRanges,
  requestId,
  shortDate,
  templateFields,
  templatePlace,
  templateTime,
  type DutyProps,
  type DutyTemplate,
  type TemplateFields,
} from "./dutySchedule";
import "./DutySchedule.css";

type Tab =
  "templates" | "generate" | "print" | "fairness" | "swaps" | "reminders";
const TABS: [Tab, string][] = [
  ["templates", "Шаблоны"],
  ["generate", "Создать по шаблонам"],
  ["print", "Печать графика"],
  ["fairness", "Минуты по сотрудникам"],
  ["swaps", "Замены дежурных"],
  ["reminders", "Напоминания"],
];

export default function DutySchedulePanel({
  state: s,
  workspace,
  onState,
  onAllocate,
}: DutyProps & { onAllocate?: () => void }) {
  const [tab, setTab] = useState<Tab>("templates");
  return (
    <div className="duty-schedule">
      <p className="info-line">
        График дежурств собирается из недельных шаблонов. Создание по шаблонам
        пропускает праздники и каникулы, не дублирует уже созданные дежурства и
        не трогает ручные правки. Сами дежурства редактируются во вкладке
        «Дежурства».
      </p>
      <div className="exchange-tabs" role="group" aria-label="График дежурств">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "selected" : ""}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "templates" ? (
        <DutyTemplates state={s} workspace={workspace} onState={onState} />
      ) : tab === "generate" ? (
        <DutyGenerate state={s} workspace={workspace} onState={onState} />
      ) : tab === "print" ? (
        <DutyRoster state={s} workspace={workspace} onState={onState} />
      ) : tab === "fairness" ? (
        <DutyFairness
          state={s}
          workspace={workspace}
          onState={onState}
          onAllocate={onAllocate}
        />
      ) : tab === "swaps" ? (
        <DutySwaps state={s} workspace={workspace} onState={onState} />
      ) : (
        <DutyReminders state={s} workspace={workspace} onState={onState} />
      )}
    </div>
  );
}

type Fields = TemplateFields;
interface Draft {
  id: string | null;
  version: number;
  reason: string;
  fields: Fields;
}
function emptyDraft(s: State): Draft {
  return {
    id: null,
    version: 0,
    reason: "",
    fields: {
      name: "Дежурство на перемене",
      weekday: 0,
      profileId: s.profiles[0]?.id || "",
      afterPeriod: 0,
      startTime: null,
      endTime: null,
      location: "",
      roomIds: [],
      buildingId: null,
      requiredTeachers: 1,
      eligibleTeacherIds: [],
      teacherIds: [],
      cycleIds: [],
      note: "",
    },
  };
}

function DutyTemplates({ state: s, workspace }: DutyProps) {
  const [templates, setTemplates] = useState<DutyTemplate[]>([]),
    [draft, setDraft] = useState<Draft | null>(null),
    [archiving, setArchiving] = useState<{ id: string; reason: string } | null>(
      null,
    );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [reload, setReload] = useState(0);
  const slot = useRef({ key: "", id: "" });
  useEffect(() => {
    let live = true;
    api("duties/templates", workspace)
      .then((r) => {
        if (live) setTemplates(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace, reload]);
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const profile = s.profiles.find((p) => p.id === draft?.fields.profileId);
  const bells =
    profile?.dayPeriods?.[String(draft?.fields.weekday)] ||
    profile?.periods ||
    [];
  function setFields(update: Partial<Fields>) {
    if (draft) setDraft({ ...draft, fields: { ...draft.fields, ...update } });
  }
  function save() {
    if (!draft) return;
    const body = {
      id: draft.id,
      version: draft.version,
      template: draft.fields,
      reason: draft.reason,
    };
    return run(async () => {
      await api("duties/templates", workspace, "POST", {
        requestId: requestId(slot, body),
        ...body,
      });
      slot.current = { key: "", id: "" };
      setDraft(null);
      setNotice("Шаблон сохранён.");
      setReload((n) => n + 1);
    });
  }
  function archive() {
    const t = templates.find((x) => x.id === archiving?.id);
    if (!t || !archiving) return;
    const body = { version: t.version, reason: archiving.reason };
    return run(async () => {
      await api(`duties/templates/${t.id}/archive`, workspace, "POST", {
        requestId: requestId(slot, [t.id, body]),
        ...body,
      });
      slot.current = { key: "", id: "" };
      setArchiving(null);
      setNotice(
        "Шаблон в архиве. Созданные по нему дежурства остались без изменений.",
      );
      setReload((n) => n + 1);
    });
  }
  return (
    <section className="duty-templates">
      <div className="dated-toolbar">
        <button
          className="primary"
          disabled={busy}
          onClick={() => setDraft(emptyDraft(s))}
        >
          Добавить шаблон
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {draft && (
        <form
          className="dated-form"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <h3>{draft.id ? "Изменить шаблон" : "Новый шаблон дежурства"}</h3>
          <label className="field">
            <span>Название</span>
            <input
              required
              maxLength={160}
              value={draft.fields.name}
              onChange={(e) => setFields({ name: e.target.value })}
            />
          </label>
          <div className="dated-fields">
            <label className="field">
              <span>День недели</span>
              <select
                value={draft.fields.weekday}
                onChange={(e) => setFields({ weekday: Number(e.target.value) })}
              >
                {s.settings.days.map((d, i) => (
                  <option key={i} value={i}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Звонки</span>
              <select
                required
                value={draft.fields.profileId}
                onChange={(e) => setFields({ profileId: e.target.value })}
              >
                {s.profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Нужно дежурных</span>
              <input
                type="number"
                required
                min={1}
                max={50}
                value={draft.fields.requiredTeachers}
                onChange={(e) =>
                  setFields({ requiredTeachers: Number(e.target.value) })
                }
              />
            </label>
          </div>
          <fieldset className="duty-mode">
            <legend>Когда дежурить</legend>
            <label className="check-line">
              <input
                type="radio"
                name="duty-mode"
                checked={draft.fields.afterPeriod !== null}
                onChange={() =>
                  setFields({ afterPeriod: 0, startTime: null, endTime: null })
                }
              />
              Перемена после урока
            </label>
            <label className="check-line">
              <input
                type="radio"
                name="duty-mode"
                checked={draft.fields.afterPeriod === null}
                onChange={() =>
                  setFields({
                    afterPeriod: null,
                    startTime: "08:00",
                    endTime: "08:20",
                  })
                }
              />
              Время
            </label>
          </fieldset>
          {draft.fields.afterPeriod !== null ? (
            <label className="field">
              <span>Перемена</span>
              <select
                value={draft.fields.afterPeriod}
                onChange={(e) =>
                  setFields({ afterPeriod: Number(e.target.value) })
                }
              >
                {bells.slice(0, -1).map((_, i) => {
                  const pause = breakAfter(profile, draft.fields.weekday, i);
                  return (
                    <option key={i} value={i} disabled={!pause}>
                      {pause
                        ? `После ${pause.label} урока · ${pause.start}–${pause.end}`
                        : `После урока ${i + 1}: перемены нет`}
                    </option>
                  );
                })}
              </select>
              <small>
                Время берётся из звонков на день недели и меняется вместе с
                ними.
              </small>
            </label>
          ) : (
            <div className="dated-fields">
              <label className="field">
                <span>Начало</span>
                <input
                  type="time"
                  required
                  value={draft.fields.startTime || ""}
                  onChange={(e) => setFields({ startTime: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Конец</span>
                <input
                  type="time"
                  required
                  value={draft.fields.endTime || ""}
                  onChange={(e) => setFields({ endTime: e.target.value })}
                />
              </label>
            </div>
          )}
          <div className="dated-fields">
            <label className="field">
              <span>Место / участок</span>
              <input
                maxLength={160}
                placeholder="Например, холл 1 этажа"
                value={draft.fields.location}
                onChange={(e) => setFields({ location: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Здание места</span>
              <select
                value={draft.fields.buildingId || ""}
                onChange={(e) =>
                  setFields({ buildingId: e.target.value || null })
                }
              >
                <option value="">По кабинетам / не задано</option>
                {s.campus?.buildings.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <Multi
            label="Кабинеты места"
            items={s.rooms}
            values={draft.fields.roomIds}
            onChange={(v) => setFields({ roomIds: v })}
          />
          <div className="dated-fields">
            <Multi
              label="Кандидаты (пусто — любой учитель)"
              items={s.teachers}
              values={draft.fields.eligibleTeacherIds}
              onChange={(v) =>
                setFields({
                  eligibleTeacherIds: v,
                  teacherIds: draft.fields.teacherIds.filter(
                    (t) => !v.length || v.includes(t),
                  ),
                })
              }
            />
            <Multi
              label="Постоянные дежурные"
              items={s.teachers.filter(
                (t) =>
                  !draft.fields.eligibleTeacherIds.length ||
                  draft.fields.eligibleTeacherIds.includes(t.id),
              )}
              values={draft.fields.teacherIds}
              onChange={(v) => setFields({ teacherIds: v })}
            />
          </div>
          {s.cycles && s.cycles.patterns.length > 1 && (
            <Multi
              label="Недели расписания (пусто — каждая неделя)"
              items={s.cycles.patterns}
              values={draft.fields.cycleIds}
              onChange={(v) => setFields({ cycleIds: v })}
            />
          )}
          <label className="field">
            <span>Примечание</span>
            <textarea
              rows={2}
              maxLength={4000}
              value={draft.fields.note}
              onChange={(e) => setFields({ note: e.target.value })}
            />
          </label>
          {draft.id && (
            <label className="field">
              <span>Причина изменения</span>
              <input
                maxLength={500}
                value={draft.reason}
                onChange={(e) => setDraft({ ...draft, reason: e.target.value })}
              />
            </label>
          )}
          <p className="muted">
            Постоянный дежурный назначается, только если он свободен по
            расписанию в этот день; иначе место остаётся свободным и
            предупреждение покажет причину. Уже созданные дежурства шаблон не
            меняет, пока вы явно не обновите нетронутые записи.
          </p>
          <div className="modal-actions">
            <button
              type="button"
              disabled={busy}
              onClick={() => setDraft(null)}
            >
              Закрыть форму
            </button>
            <button className="primary" disabled={busy}>
              Сохранить шаблон
            </button>
          </div>
        </form>
      )}
      {!templates.length && !draft && (
        <p className="empty-state">
          Шаблонов пока нет. Добавьте первый: например, «Холл, перемена после 4
          урока, понедельник».
        </p>
      )}
      <div className="commitment-list duty-template-list">
        {templates.map((t) => (
          <article key={t.id}>
            <div>
              <strong>{t.name}</strong>
              <p>
                {s.settings.days[t.weekday] || "—"} ·{" "}
                {templateTime(t, s.profiles)} ·{" "}
                {templatePlace(t, s) || "место не задано"}
              </p>
              <p>
                Нужно дежурных: {t.requiredTeachers}
                {!!t.teacherIds.length &&
                  ` · постоянные: ${t.teacherIds.map((id) => named(s.teachers, id)).join(", ")}`}
                {!!t.eligibleTeacherIds.length &&
                  ` · кандидатов: ${t.eligibleTeacherIds.length}`}
                {` · недели: ${
                  t.cycleIds.length
                    ? t.cycleIds
                        .map((id) => named(s.cycles?.patterns || [], id))
                        .join(", ")
                    : "каждая"
                }`}
              </p>
              {archiving?.id === t.id && (
                <div className="dated-toolbar">
                  <label className="field">
                    <span>Причина архивации</span>
                    <input
                      maxLength={500}
                      value={archiving.reason}
                      onChange={(e) =>
                        setArchiving({ id: t.id, reason: e.target.value })
                      }
                    />
                  </label>
                  <button disabled={busy} onClick={() => void archive()}>
                    Подтвердить архивацию
                  </button>
                  <button disabled={busy} onClick={() => setArchiving(null)}>
                    Отмена
                  </button>
                </div>
              )}
            </div>
            <div className="dated-actions">
              <button
                disabled={busy}
                onClick={() => {
                  setArchiving(null);
                  setDraft({
                    id: t.id,
                    version: t.version,
                    reason: "",
                    fields: templateFields(t),
                  });
                }}
              >
                Изменить
              </button>
              <button
                disabled={busy}
                onClick={() => setArchiving({ id: t.id, reason: "" })}
              >
                Архивировать
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

interface Row {
  date: string;
  templateId: string;
  template: string;
  reason: string;
}
interface Generated extends Preview {
  counts: {
    created: number;
    updated: number;
    existing: number;
    removed: number;
    duplicates: number;
  };
  created: {
    id: string;
    date: string;
    start: string;
    end: string;
    name: string;
    place: string;
    teachers: string[];
  }[];
  skipped: Row[];
  removed: Row[];
  duplicates: Row[];
  warnings: string[];
}
function byDate(rows: Row[]) {
  const groups = new Map<string, Row[]>();
  for (const row of rows)
    groups.set(row.date, [...(groups.get(row.date) || []), row]);
  return [...groups.entries()];
}

function DutyGenerate({ state: s, workspace, onState }: DutyProps) {
  const ranges = generationRanges(s);
  const [templates, setTemplates] = useState<DutyTemplate[]>([]),
    [chosen, setChosen] = useState<string[]>([]),
    [rangeId, setRangeId] = useState("year"),
    [from, setFrom] = useState(s.settings.yearStart),
    [to, setTo] = useState(s.settings.yearEnd);
  const [skip, setSkip] = useState(["holiday", "winter"]),
    [exclude, setExclude] = useState<string[]>([]),
    [extra, setExtra] = useState(""),
    [restore, setRestore] = useState(false),
    [update, setUpdate] = useState(false);
  const [shown, setShown] = useState<Generated | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const slot = useRef({ key: "", id: "" });
  useEffect(() => {
    let live = true;
    api("duties/templates", workspace)
      .then((r: DutyTemplate[]) => {
        if (!live) return;
        setTemplates(r);
        setChosen(r.map((t) => t.id));
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  useEffect(() => setShown(null), [s.revision, workspace]);
  function pick(id: string) {
    setRangeId(id);
    setShown(null);
    const range = ranges.find((r) => r.id === id);
    if (range && id !== "custom") {
      setFrom(range.start);
      setTo(range.end);
    }
  }
  function toggle<T>(list: T[], value: T, on: boolean) {
    return on ? [...list, value] : list.filter((x) => x !== value);
  }
  async function check() {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    setShown(null);
    try {
      setShown(
        await api("duties/generate/preview", workspace, "POST", {
          revision: s.revision,
          templateIds: chosen,
          start: from,
          end: to,
          skipKinds: skip,
          excludeDates: exclude,
          restoreRemoved: restore,
          updateUntouched: update,
        }),
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!shown || busy) return;
    setBusy(true);
    setError("");
    try {
      const body = { revision: shown.revision, token: shown.token };
      const result = await api("duties/generate/apply", workspace, "POST", {
        requestId: requestId(slot, body),
        ...body,
      });
      slot.current = { key: "", id: "" };
      setNotice(
        `Дежурства созданы: ${result.counts.created}, обновлены: ${result.counts.updated}.`,
      );
      setShown(null);
      await onState(result.state);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const counts = shown?.counts;
  return (
    <section className="duty-generate">
      <form
        className="dated-form"
        onSubmit={(e) => {
          e.preventDefault();
          void check();
        }}
      >
        <h3>Создать дежурства по шаблонам</h3>
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
              required
              min={s.settings.yearStart}
              max={s.settings.yearEnd}
              disabled={rangeId !== "custom"}
              value={from}
              onChange={(e) => {
                setFrom(e.target.value);
                setShown(null);
              }}
            />
          </label>
          <label className="field">
            <span>По дату</span>
            <input
              type="date"
              required
              min={s.settings.yearStart}
              max={s.settings.yearEnd}
              disabled={rangeId !== "custom"}
              value={to}
              onChange={(e) => {
                setTo(e.target.value);
                setShown(null);
              }}
            />
          </label>
        </div>
        <fieldset className="dated-multi">
          <legend>Шаблоны</legend>
          <div>
            {!templates.length && (
              <p className="muted">Нет действующих шаблонов.</p>
            )}
            {templates.map((t) => (
              <label className="check-line" key={t.id}>
                <input
                  type="checkbox"
                  checked={chosen.includes(t.id)}
                  onChange={(e) => {
                    setChosen(toggle(chosen, t.id, e.target.checked));
                    setShown(null);
                  }}
                />
                {t.name} · {s.settings.days[t.weekday]} ·{" "}
                {templateTime(t, s.profiles)}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="dated-multi">
          <legend>Не создавать в дни событий календаря</legend>
          <div>
            {SKIP_KINDS.map(([kind, label]) => (
              <label className="check-line" key={kind}>
                <input
                  type="checkbox"
                  checked={skip.includes(kind)}
                  onChange={(e) => {
                    setSkip(toggle(skip, kind, e.target.checked));
                    setShown(null);
                  }}
                />
                {label}
              </label>
            ))}
          </div>
          <small>
            Дни с отменой занятий для всех классов пропускаются всегда.
          </small>
        </fieldset>
        <div className="dated-toolbar">
          <label className="field">
            <span>Исключить дату</span>
            <input
              type="date"
              min={from}
              max={to}
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={!extra || exclude.includes(extra) || exclude.length >= 60}
            onClick={() => {
              setExclude([...exclude, extra].sort());
              setExtra("");
              setShown(null);
            }}
          >
            Добавить исключение
          </button>
        </div>
        {!!exclude.length && (
          <div className="duty-chips">
            {exclude.map((d) => (
              <button
                type="button"
                key={d}
                aria-label={`Убрать исключение ${shortDate(d)}`}
                onClick={() => {
                  setExclude(exclude.filter((x) => x !== d));
                  setShown(null);
                }}
              >
                {shortDate(d)} ×
              </button>
            ))}
          </div>
        )}
        <label className="check-line">
          <input
            type="checkbox"
            checked={restore}
            onChange={(e) => {
              setRestore(e.target.checked);
              setShown(null);
            }}
          />
          Восстановить удалённые вручную
        </label>
        <label className="check-line">
          <input
            type="checkbox"
            checked={update}
            onChange={(e) => {
              setUpdate(e.target.checked);
              setShown(null);
            }}
          />
          Обновить нетронутые по новому шаблону
        </label>
        <p className="muted">
          Повторное создание ничего не дублирует. Дежурства, которые вы изменили
          или удалили вручную, остаются как есть. После отмены создания в
          редакторе отметьте «Восстановить удалённые вручную», чтобы создать эти
          даты заново.
        </p>
        <div className="modal-actions">
          <button className="primary" disabled={busy || !chosen.length}>
            Проверить
          </button>
        </div>
      </form>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Проверяю данные…</p>}
      {shown && counts && (
        <>
          <section className="dated-issues duty-summary">
            <h3>Что будет создано</h3>
            <p>
              Создать: <strong>{counts.created}</strong> · обновить:{" "}
              <strong>{counts.updated}</strong> · уже есть:{" "}
              <strong>{counts.existing}</strong> · удалены вручную:{" "}
              <strong>{counts.removed}</strong> · совпали с существующими:{" "}
              <strong>{counts.duplicates}</strong>
            </p>
            {!!shown.warnings.length && (
              <div role="status">
                <strong>Предупреждения</strong>
                <ul>
                  {shown.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
            {(
              [
                ["Пропущенные даты", shown.skipped],
                ["Удалены вручную раньше", shown.removed],
                ["Уже есть дежурство в это время и месте", shown.duplicates],
              ] as [string, Row[]][]
            ).map(
              ([title, rows]) =>
                !!rows.length && (
                  <details key={title}>
                    <summary>
                      {title}: {rows.length}
                    </summary>
                    <ul className="duty-skipped">
                      {byDate(rows).map(([date, list]) => (
                        <li key={date}>
                          <strong>{shortDate(date)}</strong>
                          <ul>
                            {list.map((r, i) => (
                              <li key={i}>
                                {r.template}: {r.reason}
                              </li>
                            ))}
                          </ul>
                        </li>
                      ))}
                    </ul>
                  </details>
                ),
            )}
            {!!shown.created.length && (
              <details>
                <summary>Новые дежурства: {counts.created}</summary>
                <ul>
                  {shown.created.slice(0, 40).map((c) => (
                    <li key={c.id}>
                      {shortDate(c.date)} · {c.start}–{c.end} · {c.place}
                      {c.teachers.length
                        ? ` · ${c.teachers.join(", ")}`
                        : " · без назначенных"}
                    </li>
                  ))}
                </ul>
                {counts.created > 40 && (
                  <p className="muted">
                    Показаны первые 40. Остальные видны в списке изменений ниже.
                  </p>
                )}
              </details>
            )}
          </section>
          <ChangePreview
            preview={shown}
            busy={busy}
            onApply={apply}
            onCancel={() => setShown(null)}
            title="Создание дежурств по шаблонам"
          />
        </>
      )}
    </section>
  );
}

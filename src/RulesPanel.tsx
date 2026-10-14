import { useEffect, useRef, useState } from "react";
import type { State, Report, TimetableRule, Named } from "./types";
import { patterns } from "./cycles";
import { api } from "./api";
import { named, uid, teacherNames } from "./utils";
import ChangePreview from "./ChangePreview";
import type { Preview } from "./ChangePreview";

const KINDS = {
  availability: "Недоступность",
  daily_load: "Нагрузка за день",
  day_shape: "Структура учебного дня",
  distribution: "Распределение по неделе",
  relation: "Связь между предметами",
};
const SCOPES = {
  class: "Классы — каждый ученик отдельно",
  teacher: "Учителя",
  student: "Ученики",
  group: "Учебные группы",
  subject: "Предметы — для каждого ученика",
  room: "Кабинеты",
  workload: "Отдельные нагрузки",
};
function blank(kind: TimetableRule["kind"] = "daily_load"): TimetableRule {
  return {
    id: uid(),
    name: "",
    kind,
    scope: "class",
    targetIds: [],
    days: [],
    cycleIds: [],
    enabled: true,
    mode: "hard",
    weight: 10,
    unavailable: [],
    minLessons: null,
    maxLessons: kind === "daily_load" ? 8 : null,
    includeEmptyDays: false,
    earliestStart: null,
    latestEnd: null,
    maxIdleMinutes: null,
    maxIdlePeriods: null,
    maxIdleBlocks: null,
    maxWeeklyIdlePeriods: null,
    gapProfileId: null,
    gapExcludedPeriods: [],
    relation: "before",
    subjectIds: [],
    otherSubjectIds: [],
    relationGapMinutes: 30,
    maxConsecutive: null,
    shortBreakMinutes: 20,
    lunchStart: null,
    lunchEnd: null,
    lunchMinutes: null,
    minDays: null,
    maxDays: null,
    minDayDistance: 1,
  };
}
function entities(s: State, scope: TimetableRule["scope"]): Named[] {
  if (scope === "workload")
    return s.workloads.map((w) => ({
      id: w.id,
      name: `${named(s.groups, w.groupId)} · ${named(s.subjects, w.subjectId)} · ${teacherNames(s, w)}`,
    }));
  if (scope === "student")
    return s.students.map((st) => ({
      id: st.id,
      name: `${st.name} · ${named(s.classes, st.classId)}${st.active === false ? " · выбыл" : ""}`,
    }));
  return s[
    (
      {
        class: "classes",
        teacher: "teachers",
        group: "groups",
        subject: "subjects",
        room: "rooms",
      } as const
    )[scope]
  ];
}

export default function RulesPanel({
  state: s,
  report,
  workspace,
  onState,
}: {
  state: State;
  report: Report;
  workspace: string;
  onState: (s: State) => Promise<void>;
}) {
  const [draft, setDraft] = useState<TimetableRule | null>(null),
    [allTargets, setAllTargets] = useState(true);
  const [query, setQuery] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const formAnchor = useRef<HTMLDivElement>(null),
    previewAnchor = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setDraft(null);
    setPreview(null);
    setError("");
  }, [s.revision, workspace]);
  useEffect(() => {
    if (preview)
      previewAnchor.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
  }, [preview?.token]);
  function open(rule: TimetableRule) {
    setDraft(structuredClone(rule));
    setAllTargets(!rule.targetIds.length);
    setQuery("");
    setError("");
    setPreview(null);
    setTimeout(
      () =>
        formAnchor.current?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        }),
      0,
    );
  }
  function update(patch: Partial<TimetableRule>) {
    setDraft((current) => current && { ...current, ...patch });
    setPreview(null);
  }
  async function run(task: () => Promise<void>) {
    if (busy) return;
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
  function stage(rule: TimetableRule) {
    return run(async () =>
      setPreview(
        await api("editor/preview", workspace, "POST", {
          revision: s.revision,
          action: "rule",
          rule,
        }),
      ),
    );
  }
  const targets = draft ? entities(s, draft.scope) : [];
  const matches = targets.filter((t) =>
    t.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  const rules = s.rules || [];
  const violations = report.issues.filter((i) => i.ruleId);
  function numberField(
    key: keyof TimetableRule,
    label: string,
    min: number,
    max: number,
    hint?: string,
  ) {
    return (
      <label className="field">
        <span>{label}</span>
        <input
          type="number"
          min={min}
          max={max}
          required={[
            "weight",
            "shortBreakMinutes",
            "minDayDistance",
            "relationGapMinutes",
          ].includes(key)}
          value={(draft?.[key] as number) ?? ""}
          onChange={(e) =>
            update({
              [key]: e.target.value === "" ? null : Number(e.target.value),
            })
          }
        />
        {hint && <small>{hint}</small>}
      </label>
    );
  }
  function timeField(key: keyof TimetableRule, label: string) {
    return (
      <label className="field">
        <span>{label}</span>
        <input
          type="time"
          value={(draft?.[key] as string) ?? ""}
          onChange={(e) => update({ [key]: e.target.value || null })}
        />
      </label>
    );
  }
  return (
    <div className="rules-panel">
      <div className="rules-summary">
        <div>
          <strong>
            {rules.filter((r) => r.enabled && r.mode === "hard").length}
          </strong>
          <span>Обязательных правил</span>
        </div>
        <div>
          <strong>{violations.length}</strong>
          <span>Нарушений правил</span>
        </div>
        <div>
          <strong>{report.warningCount || 0}</strong>
          <span>Невыполненных пожеланий</span>
        </div>
        <div>
          <strong>{report.qualityPenalty || 0}</strong>
          <span>Штраф пожеланий</span>
        </div>
      </div>
      <p className="muted">
        Обязательные правила ограничивают генерацию. Пожелания улучшают
        результат с учётом своего веса. Ориентир «уроков в день» в справочнике
        класса сам по себе не становится ограничением: задайте правило здесь.
      </p>
      <button className="primary" disabled={busy} onClick={() => open(blank())}>
        Добавить правило
      </button>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {!rules.length && (
        <div className="empty">
          <h3>Дополнительные правила пока не заданы</h3>
          <p>
            Начните с доступности ресурсов и дневной нагрузки. Базовые запреты
            пересечений и настройки каждой нагрузки уже действуют.
          </p>
        </div>
      )}
      <div className="rules-list">
        {rules.map((r) => (
          <article key={r.id}>
            <div>
              <strong>{r.name}</strong>
              <small>
                {KINDS[r.kind]} · {SCOPES[r.scope]} ·{" "}
                {r.targetIds.length ? `${r.targetIds.length} выбрано` : "Все"} ·{" "}
                {!r.enabled
                  ? "Выключено"
                  : r.mode === "hard"
                    ? "Обязательно"
                    : `Пожелание, вес ${r.weight}`}
              </small>
              <small>
                Недели:{" "}
                {r.cycleIds?.length
                  ? r.cycleIds.map((id) => named(patterns(s), id)).join(", ")
                  : "Все"}
              </small>
              <p>
                {report.issues.filter((i) => i.ruleId === r.id).length}{" "}
                нарушений ·{" "}
                {
                  (report.warnings || []).filter((i) => i.ruleId === r.id)
                    .length
                }{" "}
                пожеланий не выполнено
              </p>
            </div>
            <div className="button-row">
              <button disabled={busy} onClick={() => open(r)}>
                Изменить
              </button>
              <button
                disabled={busy}
                onClick={() => stage({ ...r, enabled: !r.enabled })}
              >
                {r.enabled ? "Выключить" : "Включить"}
              </button>
            </div>
          </article>
        ))}
      </div>
      <div ref={formAnchor} />
      {draft && (
        <form
          className="rule-editor"
          onSubmit={(e) => {
            e.preventDefault();
            if (!allTargets && !draft.targetIds.length) {
              setError("Выберите записи, к которым применяется правило.");
              return;
            }
            void stage(draft);
          }}
        >
          <h3>
            {rules.some((r) => r.id === draft.id)
              ? "Изменить правило"
              : "Новое правило"}
          </h3>
          <div className="form-grid">
            <label className="field">
              <span>Название</span>
              <input
                required
                maxLength={160}
                value={draft.name}
                onChange={(e) => update({ name: e.target.value })}
                placeholder="Например, не больше 8 уроков в день"
              />
            </label>
            <label className="field">
              <span>Тип правила</span>
              <select
                value={draft.kind}
                onChange={(e) => {
                  const next = blank(e.target.value as TimetableRule["kind"]);
                  const resetScope =
                    next.kind === "relation" &&
                    ["subject", "workload"].includes(draft.scope);
                  if (resetScope) setAllTargets(true);
                  update({
                    ...next,
                    id: draft.id,
                    name: draft.name,
                    scope: resetScope ? "class" : draft.scope,
                    targetIds: resetScope ? [] : draft.targetIds,
                    enabled: draft.enabled,
                    mode: draft.mode,
                    weight: draft.weight,
                    cycleIds: draft.cycleIds,
                  });
                }}
              >
                {Object.entries(KINDS).map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Кого касается</span>
              <select
                value={draft.scope}
                onChange={(e) => {
                  update({
                    scope: e.target.value as TimetableRule["scope"],
                    targetIds: [],
                  });
                  setAllTargets(true);
                  setQuery("");
                }}
              >
                {Object.entries(SCOPES)
                  .filter(
                    ([id]) =>
                      draft.kind !== "relation" ||
                      !["subject", "workload"].includes(id),
                  )
                  .map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
              </select>
            </label>
            <label className="field">
              <span>Обязательность</span>
              <select
                value={draft.mode}
                onChange={(e) =>
                  update({ mode: e.target.value as "hard" | "soft" })
                }
              >
                <option value="hard">Обязательное условие</option>
                <option value="soft">Пожелание</option>
              </select>
            </label>
          </div>
          {draft.mode === "soft" &&
            numberField(
              "weight",
              "Вес пожелания",
              1,
              1000,
              "Штраф за единицу отклонения: урок, минута, день или отсутствующий перерыв. Больше вес — важнее пожелание. Дополнительно генератор предпочитает прежние и более ранние позиции.",
            )}
          <label className="inline-check">
            <input
              type="checkbox"
              checked={allTargets}
              onChange={(e) => {
                setAllTargets(e.target.checked);
                update({ targetIds: [] });
              }}
            />{" "}
            Все записи выбранного типа
          </label>
          {draft.scope === "student" && allTargets && (
            <p className="muted">
              Общее правило применяется к действующим ученикам. Выбывшего
              ученика можно выбрать отдельно.
            </p>
          )}
          {!allTargets && (
            <div className="rule-targets">
              <label className="field">
                <span>Найти запись</span>
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
              <p>Выбрано: {draft.targetIds.length}</p>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  update({
                    targetIds: [
                      ...new Set([
                        ...draft.targetIds,
                        ...matches.map((t) => t.id),
                      ]),
                    ],
                  })
                }
              >
                Выбрать найденные ({matches.length})
              </button>
              <div>
                {matches.slice(0, 100).map((target) => (
                  <label key={target.id} className="inline-check">
                    <input
                      type="checkbox"
                      checked={draft.targetIds.includes(target.id)}
                      onChange={(e) =>
                        update({
                          targetIds: e.target.checked
                            ? [...draft.targetIds, target.id]
                            : draft.targetIds.filter((id) => id !== target.id),
                        })
                      }
                    />
                    {target.name}
                  </label>
                ))}
              </div>
              {matches.length > 100 && (
                <p>Показаны первые 100; уточните поиск.</p>
              )}
            </div>
          )}
          {draft.scope === "class" && (
            <p className="info-line">
              Пределы и структура дня проверяются по каждому действующему
              ученику с учётом его групп. Если списков учеников класса нет,
              используются общие занятия класса.
            </p>
          )}
          <fieldset className="rule-days">
            <legend>Недели правила — без отметок все недели</legend>
            {patterns(s).map((p) => (
              <label className="inline-check" key={p.id}>
                <input
                  type="checkbox"
                  checked={(draft.cycleIds || []).includes(p.id)}
                  onChange={(e) =>
                    update({
                      cycleIds: e.target.checked
                        ? [...(draft.cycleIds || []), p.id]
                        : (draft.cycleIds || []).filter((id) => id !== p.id),
                    })
                  }
                />
                {p.name}
              </label>
            ))}
          </fieldset>
          {draft.kind !== "availability" && (
            <fieldset className="rule-days">
              <legend>Дни правила — без отметок действует вся неделя</legend>
              {s.settings.days.map((day, i) => (
                <label className="inline-check" key={i}>
                  <input
                    type="checkbox"
                    checked={draft.days.includes(i)}
                    onChange={(e) =>
                      update({
                        days: e.target.checked
                          ? [...draft.days, i].sort()
                          : draft.days.filter((d) => d !== i),
                      })
                    }
                  />
                  {day}
                </label>
              ))}
            </fieldset>
          )}
          {draft.kind === "availability" && (
            <section className="rule-windows">
              <h4>Интервалы недоступности</h4>
              <p>
                Запрет касается любого урока, который пересекает указанное
                время, независимо от номера периода и профиля звонков.
              </p>
              {draft.unavailable.map((window, i) => (
                <div key={i} className="planner-filters">
                  <label>
                    День
                    <select
                      value={window.day}
                      onChange={(e) =>
                        update({
                          unavailable: draft.unavailable.map((v, j) =>
                            j === i ? { ...v, day: Number(e.target.value) } : v,
                          ),
                        })
                      }
                    >
                      {s.settings.days.map((name, d) => (
                        <option key={d} value={d}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    С
                    <input
                      required
                      type="time"
                      value={window.start}
                      onChange={(e) =>
                        update({
                          unavailable: draft.unavailable.map((v, j) =>
                            j === i ? { ...v, start: e.target.value } : v,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    До
                    <input
                      required
                      type="time"
                      value={window.end}
                      onChange={(e) =>
                        update({
                          unavailable: draft.unavailable.map((v, j) =>
                            j === i ? { ...v, end: e.target.value } : v,
                          ),
                        })
                      }
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() =>
                      update({
                        unavailable: draft.unavailable.filter(
                          (_, j) => j !== i,
                        ),
                      })
                    }
                  >
                    Убрать интервал
                  </button>
                </div>
              ))}
              <button
                type="button"
                onClick={() =>
                  update({
                    unavailable: [
                      ...draft.unavailable,
                      { day: 0, start: "08:00", end: "09:00" },
                    ],
                  })
                }
              >
                Добавить интервал
              </button>
            </section>
          )}
          {draft.kind === "daily_load" && (
            <>
              <div className="form-grid">
                {numberField("minLessons", "Минимум уроков за день", 0, 24)}
                {numberField("maxLessons", "Максимум уроков за день", 0, 24)}
              </div>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={draft.includeEmptyDays}
                  onChange={(e) =>
                    update({ includeEmptyDays: e.target.checked })
                  }
                />{" "}
                Требовать минимум и в дни без уроков
              </label>
              <p className="muted">
                Двойное занятие считается двумя уроками. Пустой минимум или
                максимум не ограничивает соответствующую границу.
              </p>
            </>
          )}
          {draft.kind === "day_shape" && (
            <>
              <div className="form-grid">
                {timeField("earliestStart", "Начинать не раньше")}
                {timeField("latestEnd", "Заканчивать не позже")}
                {numberField(
                  "maxIdleMinutes",
                  "Максимум минут между уроками за день",
                  0,
                  1440,
                  "От первого до последнего урока, включая все перемены и обед. Время до первого и после последнего не считается.",
                )}
                {numberField(
                  "maxConsecutive",
                  "Максимум уроков в серии",
                  1,
                  24,
                )}
                {numberField(
                  "shortBreakMinutes",
                  "Перемена, которая ещё не прерывает серию, минут",
                  0,
                  180,
                )}
              </div>
              <h4>Пустые периоды и окна</h4>
              <div className="form-grid">
                {numberField(
                  "maxIdlePeriods",
                  "Максимум пустых периодов за день",
                  0,
                  12,
                )}
                {numberField("maxIdleBlocks", "Максимум окон за день", 0, 12)}
                {numberField(
                  "maxWeeklyIdlePeriods",
                  "Максимум пустых периодов за выбранные дни недели",
                  0,
                  84,
                )}
                <label className="field">
                  <span>Профиль звонков для подсчёта окон</span>
                  <select
                    value={draft.gapProfileId || ""}
                    required={[
                      draft.maxIdlePeriods,
                      draft.maxIdleBlocks,
                      draft.maxWeeklyIdlePeriods,
                    ].some((v) => v != null)}
                    onChange={(e) =>
                      update({
                        gapProfileId: e.target.value || null,
                        gapExcludedPeriods: [],
                      })
                    }
                  >
                    <option value="">Выберите профиль</option>
                    {s.profiles.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {draft.gapProfileId && (
                <fieldset className="rule-days">
                  <legend>Не учитывать периоды</legend>
                  {s.profiles
                    .find((p) => p.id === draft.gapProfileId)
                    ?.periods.map((period, i) => (
                      <label key={i} className="inline-check">
                        <input
                          type="checkbox"
                          checked={(draft.gapExcludedPeriods || []).includes(i)}
                          onChange={(e) =>
                            update({
                              gapExcludedPeriods: e.target.checked
                                ? [...(draft.gapExcludedPeriods || []), i]
                                : (draft.gapExcludedPeriods || []).filter(
                                    (p) => p !== i,
                                  ),
                            })
                          }
                        />
                        {period.label} · {period.start}–{period.end}
                      </label>
                    ))}
                </fieldset>
              )}
              <p className="muted">
                Пустой период полностью свободен между первым и последним
                уроком. Любое пересечение с уроком делает период занятым. Окно —
                ряд соседних учитываемых пустых периодов. Исключённые свободные
                периоды и обычные перемены не увеличивают счётчик и не разделяют
                окно. Урок между пустыми периодами всегда разделяет окна.
                Используются звонки выбранного профиля для конкретного дня.
              </p>
              <h4>Непрерывный обеденный перерыв</h4>
              <div className="form-grid">
                {timeField("lunchStart", "Начало окна для обеда")}
                {timeField("lunchEnd", "Конец окна для обеда")}
                {numberField(
                  "lunchMinutes",
                  "Минимум свободных минут подряд",
                  1,
                  180,
                )}
              </div>
              <p className="muted">
                Для обеда заполните все три поля. Пустые условия не применяются.
              </p>
            </>
          )}
          {draft.kind === "relation" && (
            <>
              <label className="field">
                <span>Связь</span>
                <select
                  value={draft.relation || "before"}
                  onChange={(e) =>
                    update({
                      relation: e.target.value as TimetableRule["relation"],
                    })
                  }
                >
                  <option value="before">
                    Первый набор раньше второго в течение дня
                  </option>
                  <option value="not_following">
                    Второй набор не сразу после первого
                  </option>
                  <option value="same_day">
                    Оба набора встречаются в одни дни
                  </option>
                  <option value="different_days">
                    Наборы проходят в разные дни
                  </option>
                </select>
              </label>
              <div className="form-grid">
                {(["subjectIds", "otherSubjectIds"] as const).map(
                  (field, i) => (
                    <label className="field" key={field}>
                      <span>
                        {i === 0
                          ? "Первый набор предметов"
                          : "Второй набор предметов"}
                      </span>
                      <select
                        required
                        multiple
                        size={5}
                        value={draft[field] || []}
                        onChange={(e) =>
                          update({
                            [field]: Array.from(
                              e.target.selectedOptions,
                              (option) => option.value,
                            ),
                          })
                        }
                      >
                        {s.subjects.map((subject) => (
                          <option key={subject.id} value={subject.id}>
                            {subject.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ),
                )}
              </div>
              {draft.relation === "not_following" &&
                numberField(
                  "relationGapMinutes",
                  "Предельная перемена для непосредственного следования, минут",
                  0,
                  180,
                )}
              <p className="info-line">
                При выборе класса правило действует по личным предметам каждого
                ученика. Предметы двух наборов не должны повторяться. Порядок
                проверяется только в дни, где есть оба набора: все уроки первого
                завершаются до начала второго. Запрет следования учитывает
                промежуточные уроки любого предмета и заданную перемену.
                Совпадение дней не требует одинакового числа уроков.
              </p>
            </>
          )}
          {draft.kind === "distribution" && (
            <div className="form-grid">
              {numberField("minDays", "Минимум дней с занятиями", 0, 7)}
              {numberField("maxDays", "Максимум дней с занятиями", 0, 7)}
              {numberField(
                "minDayDistance",
                "Минимальное расстояние между днями",
                1,
                7,
                "1 — соседние дни разрешены; 2 — минимум один свободный день между занятиями. Проверка внутри повторяющейся недели.",
              )}
            </div>
          )}
          <label className="inline-check">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => update({ enabled: e.target.checked })}
            />{" "}
            Правило включено
          </label>
          <div className="modal-actions">
            {rules.some((r) => r.id === draft.id) && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  run(async () =>
                    setPreview(
                      await api("editor/preview", workspace, "POST", {
                        revision: s.revision,
                        action: "removeRule",
                        ids: [draft.id],
                      }),
                    ),
                  )
                }
              >
                Проверить удаление
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setDraft(null);
                setPreview(null);
              }}
            >
              Закрыть
            </button>
            <button
              className="primary"
              disabled={busy || (!allTargets && !draft.targetIds.length)}
            >
              Проверить правило
            </button>
          </div>
        </form>
      )}
      <div ref={previewAnchor} />
      {preview && (
        <>
          <p className="info-line">
            Можно сохранить новое правило, даже если текущее расписание ему не
            соответствует. Найденные нарушения останутся в проверке; генератор
            будет учитывать сохранённое правило.
          </p>
          <ChangePreview
            key={preview.token}
            preview={preview}
            busy={busy}
            onCancel={() => setPreview(null)}
            onApply={() =>
              run(async () =>
                onState(
                  await api("editor/apply", workspace, "POST", {
                    revision: s.revision,
                    token: preview.token,
                  }),
                ),
              )
            }
          />
        </>
      )}
      {(violations.length > 0 || !!report.warningCount) && (
        <details className="rules-violations">
          <summary>Показать текущие нарушения и пожелания</summary>
          {[...violations, ...(report.warnings || [])]
            .slice(0, 100)
            .map((issue, i) => (
              <p key={i}>
                {issue.penalty !== undefined
                  ? `Пожелание (штраф ${issue.penalty}): `
                  : "Обязательное: "}
                {issue.message}
              </p>
            ))}
        </details>
      )}
    </div>
  );
}

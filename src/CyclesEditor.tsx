import { useEffect, useState } from "react";
import type { Cycles, State } from "./types";
import { api } from "./api";
import { uid, named } from "./utils";
import { patterns, patternOn } from "./cycles";
import ChangePreview from "./ChangePreview";
import type { Preview } from "./ChangePreview";

export default function CyclesEditor({
  state: s,
  workspace,
  onState,
}: {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Cycles | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [source, setSource] = useState("weekly"),
    [target, setTarget] = useState("");
  const [replace, setReplace] = useState(false),
    [day, setDay] = useState(s.settings.yearStart);
  useEffect(() => {
    setDraft(null);
    setPreview(null);
    setError("");
    setSource(patterns(s)[0].id);
    setTarget("");
  }, [s.revision, workspace]);
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
  function stage(body: Record<string, unknown>) {
    return run(async () =>
      setPreview(
        await api("editor/preview", workspace, "POST", {
          revision: s.revision,
          ...body,
        }),
      ),
    );
  }
  function change(value: Cycles) {
    setDraft(value);
    setPreview(null);
  }
  const cycle = patternOn(s, day);
  return (
    <section className="settings-card full-span cycles-editor">
      <header>
        <div>
          <h2>Недели и учебные периоды</h2>
          <p>
            Общие справочники, отдельное расписание для каждой недели цикла.
          </p>
        </div>
        {!draft && (
          <button
            disabled={busy}
            onClick={() => {
              setDraft(
                structuredClone(
                  s.cycles || { patterns: patterns(s), periods: [] },
                ),
              );
              setPreview(null);
            }}
          >
            Настроить учебные циклы
          </button>
        )}
      </header>
      <p>
        {patterns(s)
          .map((p) => p.name)
          .join(" → ")}{" "}
        · {(s.cycles?.periods || []).length} отдельных учебных периодов
      </p>
      <label className="field">
        <span>Посмотреть неделю на дату</span>
        <input
          type="date"
          value={day}
          onChange={(e) => setDay(e.target.value)}
        />
      </label>
      <p role="status">
        {cycle ? named(patterns(s), cycle) : "Дата вне учебных периодов"}
      </p>
      <p className="muted">
        Чередование отсчитывается по календарным неделям с понедельника;
        каникулы его не сдвигают. Без отдельных периодов действует весь учебный
        год и последовательность недель из списка. Это выбор шаблона недели;
        отмены уроков по праздникам учитываются отдельно в календаре.
      </p>
      {error && (
        <div role="alert" className="error-banner">
          {error}
        </div>
      )}
      {draft && !preview && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void stage({ action: "cycles", cycles: draft });
          }}
        >
          <h3>Шаблоны недель</h3>
          {draft.patterns.map((p, i) => (
            <div className="campus-row" key={p.id}>
              <label className="field">
                <span>Неделя {i + 1}</span>
                <input
                  required
                  maxLength={160}
                  value={p.name}
                  onChange={(e) =>
                    change({
                      ...draft,
                      patterns: draft.patterns.map((x) =>
                        x.id === p.id ? { ...x, name: e.target.value } : x,
                      ),
                    })
                  }
                />
              </label>
              <button
                type="button"
                disabled={draft.patterns.length === 1}
                onClick={() =>
                  change({
                    ...draft,
                    patterns: draft.patterns.filter((x) => x.id !== p.id),
                  })
                }
              >
                Удалить неделю
              </button>
            </div>
          ))}
          <button
            type="button"
            disabled={draft.patterns.length >= 32}
            onClick={() =>
              change({
                ...draft,
                patterns: [
                  ...draft.patterns,
                  { id: uid(), name: "Новая неделя" },
                ],
              })
            }
          >
            Добавить неделю
          </button>
          <p className="muted small">
            Удалить используемую неделю можно после переноса или снятия её
            занятий, исправления нагрузок, правил и периодов. Добавление недели
            не копирует занятия: используйте копирование ниже или генератор.
          </p>
          <h3>Периоды учебного года</h3>
          {draft.periods.map((period, i) => (
            <fieldset key={period.id} className="cycle-term">
              <legend>Учебный период {i + 1}</legend>
              <label className="field">
                <span>Название периода</span>
                <input
                  required
                  maxLength={160}
                  value={period.name}
                  onChange={(e) =>
                    change({
                      ...draft,
                      periods: draft.periods.map((x) =>
                        x.id === period.id ? { ...x, name: e.target.value } : x,
                      ),
                    })
                  }
                />
              </label>
              <div className="form-grid">
                {(["start", "end", "anchor"] as const).map((field) => (
                  <label key={field} className="field">
                    <span>
                      {
                        {
                          start: "Начало периода",
                          end: "Конец периода",
                          anchor: "Понедельник первой недели цикла",
                        }[field]
                      }
                    </span>
                    <input
                      type="date"
                      required
                      value={period[field]}
                      onChange={(e) =>
                        change({
                          ...draft,
                          periods: draft.periods.map((x) =>
                            x.id === period.id
                              ? { ...x, [field]: e.target.value }
                              : x,
                          ),
                        })
                      }
                    />
                  </label>
                ))}
              </div>
              <h4>Последовательность недель</h4>
              {period.patternIds.map((id, index) => (
                <div className="campus-row" key={index}>
                  <label className="field">
                    <span>Позиция {index + 1}</span>
                    <select
                      required
                      value={id}
                      onChange={(e) =>
                        change({
                          ...draft,
                          periods: draft.periods.map((x) =>
                            x.id === period.id
                              ? {
                                  ...x,
                                  patternIds: x.patternIds.map((v, n) =>
                                    n === index ? e.target.value : v,
                                  ),
                                }
                              : x,
                          ),
                        })
                      }
                    >
                      <option value="">Выберите неделю</option>
                      {draft.patterns.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    disabled={period.patternIds.length === 1}
                    onClick={() =>
                      change({
                        ...draft,
                        periods: draft.periods.map((x) =>
                          x.id === period.id
                            ? {
                                ...x,
                                patternIds: x.patternIds.filter(
                                  (_, n) => n !== index,
                                ),
                              }
                            : x,
                        ),
                      })
                    }
                  >
                    Убрать позицию
                  </button>
                </div>
              ))}
              <button
                type="button"
                disabled={period.patternIds.length >= 8}
                onClick={() =>
                  change({
                    ...draft,
                    periods: draft.periods.map((x) =>
                      x.id === period.id
                        ? {
                            ...x,
                            patternIds: [...x.patternIds, draft.patterns[0].id],
                          }
                        : x,
                    ),
                  })
                }
              >
                Добавить позицию
              </button>
              <button
                type="button"
                onClick={() =>
                  change({
                    ...draft,
                    periods: draft.periods.filter((x) => x.id !== period.id),
                  })
                }
              >
                Удалить период
              </button>
            </fieldset>
          ))}
          <button
            type="button"
            onClick={() => {
              const d = new Date(s.settings.yearStart + "T00:00:00Z");
              d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
              change({
                ...draft,
                periods: [
                  ...draft.periods,
                  {
                    id: uid(),
                    name: "Учебный период",
                    start: s.settings.yearStart,
                    end: s.settings.yearEnd,
                    anchor: d.toISOString().slice(0, 10),
                    patternIds: [draft.patterns[0].id],
                  },
                ],
              });
            }}
          >
            Добавить учебный период
          </button>
          <p className="info-line">
            Периоды не пересекаются и находятся внутри учебного года. Если
            периоды заданы, даты между ними не имеют шаблона расписания. Для
            второго семестра можно выбрать свои шаблоны недель.
          </p>
          <div className="modal-actions">
            <button type="button" onClick={() => setDraft(null)}>
              Отмена
            </button>
            <button className="primary" disabled={busy}>
              Проверить циклы
            </button>
          </div>
        </form>
      )}
      {!draft && patterns(s).length > 1 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void stage({
              action: "copyCycle",
              sourceCycleId: source,
              cycleId: target,
              replaceCycle: replace,
            });
          }}
        >
          <h3>Скопировать занятия между неделями</h3>
          <div className="form-grid">
            <label className="field">
              <span>Исходная неделя</span>
              <select
                required
                value={source}
                onChange={(e) => setSource(e.target.value)}
              >
                {patterns(s).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Целевая неделя</span>
              <select
                required
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              >
                <option value="">Выберите неделю</option>
                {patterns(s)
                  .filter((p) => p.id !== source)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <label className="inline-check">
            <input
              type="checkbox"
              checked={replace}
              onChange={(e) => setReplace(e.target.checked)}
            />
            Заменить занятия целевой недели
          </label>
          <p className="muted small">
            Исходная неделя сохраняется. Копии получают новые идентификаторы и
            не закрепляются. Часы и активность нагрузок в целевой неделе
            проверяются до применения.
          </p>
          <button disabled={busy}>Проверить копирование</button>
        </form>
      )}
      {preview && (
        <ChangePreview
          preview={preview}
          busy={busy}
          onCancel={() => setPreview(null)}
          onApply={() =>
            run(async () => {
              await onState(
                await api("editor/apply", workspace, "POST", {
                  revision: preview.revision,
                  token: preview.token,
                }),
              );
              setPreview(null);
            })
          }
        />
      )}
    </section>
  );
}

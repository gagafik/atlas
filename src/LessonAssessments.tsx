import { useState, type Dispatch, type SetStateAction } from "react";
import CategoryBadge from "./CategoryBadge";
import {
  WEIGHT_PRESETS,
  bookingDefault,
  categoryOf,
  presetOf,
  type CategoryConfig,
} from "./assessmentCategories";
import {
  numberField,
  numberValue,
  withKind,
  type Assessment,
  type JournalContent,
  type JournalEntry,
} from "./journal";

const MAX_WORKS = 10;

// The works of the lesson: the booked ones from the assessment calendar and one row per work. The fields of a work open
// in place; a work that was just added is open.
export default function LessonAssessments({
  entry,
  editable,
  categories,
  customWeights,
  setCustomWeights,
  content,
  patchWork,
}: {
  entry: JournalEntry;
  editable: boolean;
  categories: CategoryConfig | null;
  // Works whose weight is typed by hand even when it equals a preset.
  customWeights: Set<string>;
  setCustomWeights: Dispatch<SetStateAction<Set<string>>>;
  content: (update: (c: JournalContent) => JournalContent) => void;
  patchWork: (id: string, change: Partial<Assessment>) => void;
}) {
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const toggle = (id: string, show: boolean) =>
    setOpened((s) => {
      const next = new Set(s);
      if (show) next.add(id);
      else next.delete(id);
      return next;
    });
  const works = entry.document.content.assessments;
  return (
    <section className="lesson-assessments" aria-label="Проверочные работы">
      <div className="lesson-row">
        <strong>
          Проверочные работы · {works.length}/{MAX_WORKS}
        </strong>
        <button
          type="button"
          disabled={works.length >= MAX_WORKS}
          onClick={() => {
            const id = crypto.randomUUID();
            toggle(id, true);
            content((c) => ({
              ...c,
              assessments: [
                ...c.assessments,
                {
                  id,
                  name: `Работа ${c.assessments.length + 1}`,
                  kind: "points",
                  maxScore: 10,
                  weight: 1,
                  category: categories?.defaultCategory || "fo",
                },
              ],
            }));
          }}
        >
          Добавить работу
        </button>
        <small className="lesson-hint">
          Баллы — от 0 до максимума; текстовые оценки (A*, зачёт) не входят в
          средний процент. Вес 0 исключает работу из расчёта.
        </small>
      </div>
      {entry.bookings?.map((b) => (
        <div className="lesson-row lesson-booking" key={b.id}>
          <span>
            По календарю: {b.short} «{b.topic}»
          </span>
          <button
            type="button"
            disabled={
              !editable ||
              works.length >= MAX_WORKS ||
              works.some((x) => x.bookingId === b.id)
            }
            onClick={() =>
              content((c) => ({
                ...c,
                assessments: [...c.assessments, bookingDefault(b)],
              }))
            }
          >
            Добавить работу из календаря
          </button>
        </div>
      ))}
      {works.map((a) => {
        const category = categoryOf(a, categories);
        const booking = a.bookingId
          ? entry.bookings?.find((b) => b.id === a.bookingId)
          : undefined;
        const show = opened.has(a.id);
        return (
          <div key={a.id} className="lesson-work">
            <div className="lesson-row">
              <strong className="lesson-work-name" title={a.name}>
                {a.name || "Без названия"}
              </strong>
              <CategoryBadge category={category} />
              <span className="muted">
                {a.kind === "points"
                  ? `макс. ${a.maxScore} · вес ${a.weight}`
                  : "текстовая оценка"}
              </span>
              {booking && (
                <span className="muted">Из календаря: {booking.short}</span>
              )}
              <button
                type="button"
                aria-expanded={show}
                onClick={() => toggle(a.id, !show)}
              >
                {show ? "Свернуть" : "Изменить"}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (
                    entry.document.content.records.some((p) =>
                      p.grades.some(
                        (g) =>
                          g.assessmentId === a.id &&
                          (g.score !== null || g.label || g.comment),
                      ),
                    ) &&
                    !window.confirm(
                      "Удалить работу и её оценки из текущей редакции? Сохранённые редакции останутся в истории.",
                    )
                  )
                    return;
                  content((c) => ({
                    ...c,
                    assessments: c.assessments.filter((x) => x.id !== a.id),
                    records: c.records.map((p) => ({
                      ...p,
                      grades: p.grades.filter((g) => g.assessmentId !== a.id),
                    })),
                  }));
                  toggle(a.id, false);
                }}
              >
                Удалить работу
              </button>
            </div>
            {show && (
              <div className="lesson-work-form">
                <label className="field">
                  <span>Название работы</span>
                  <input
                    maxLength={100}
                    value={a.name}
                    onChange={(e) =>
                      content((c) => ({
                        ...c,
                        assessments: c.assessments.map((x) =>
                          x.id === a.id ? { ...x, name: e.target.value } : x,
                        ),
                      }))
                    }
                  />
                </label>
                <label className="field">
                  <span>Формат</span>
                  <select
                    value={a.kind}
                    disabled={entry.document.content.records.some((p) =>
                      p.grades.some(
                        (g) =>
                          g.assessmentId === a.id &&
                          (g.score !== null || g.label),
                      ),
                    )}
                    onChange={(e) =>
                      content((c) => ({
                        ...c,
                        assessments: c.assessments.map((x) =>
                          x.id === a.id
                            ? withKind(x, e.target.value as Assessment["kind"])
                            : x,
                        ),
                      }))
                    }
                  >
                    <option value="points">Баллы</option>
                    <option value="label">Текстовая оценка</option>
                  </select>
                </label>
                <label className="field">
                  <span>Категория</span>
                  <select
                    value={category.id}
                    onChange={(e) =>
                      patchWork(a.id, { category: e.target.value })
                    }
                  >
                    {(categories?.categories ?? [])
                      .filter((k) => k.active || k.id === category.id)
                      .map((k) => (
                        <option key={k.id} value={k.id}>
                          {k.name}
                          {k.active ? "" : " (отключена)"}
                        </option>
                      ))}
                    {!categories?.categories.some(
                      (k) => k.id === category.id,
                    ) && <option value={category.id}>{category.name}</option>}
                  </select>
                </label>
                {a.kind === "points" && (
                  <>
                    <label className="field">
                      <span>Максимум баллов</span>
                      <input
                        type="number"
                        min="0.01"
                        max="10000"
                        step="0.01"
                        value={numberValue(a.maxScore)}
                        onChange={(e) =>
                          content((c) => ({
                            ...c,
                            assessments: c.assessments.map((x) =>
                              x.id === a.id
                                ? {
                                    ...x,
                                    maxScore: numberField(e.target.value),
                                  }
                                : x,
                            ),
                          }))
                        }
                      />
                    </label>
                    <label className="field">
                      <span>Вес работы</span>
                      <select
                        value={
                          customWeights.has(a.id)
                            ? "custom"
                            : String(presetOf(a.weight))
                        }
                        onChange={(e) => {
                          const custom = e.target.value === "custom";
                          setCustomWeights((s) => {
                            const next = new Set(s);
                            if (custom) next.add(a.id);
                            else next.delete(a.id);
                            return next;
                          });
                          if (!custom)
                            patchWork(a.id, {
                              weight: Number(e.target.value),
                            });
                        }}
                      >
                        {WEIGHT_PRESETS.map((w) => (
                          <option key={w.value} value={w.value}>
                            {w.label}
                          </option>
                        ))}
                        <option value="custom">Свой</option>
                      </select>
                    </label>
                    {(customWeights.has(a.id) ||
                      presetOf(a.weight) === "custom") && (
                      <label className="field">
                        <span>Свой вес (от 0 до 100)</span>
                        <input
                          type="number"
                          min="0"
                          max="100"
                          step="0.01"
                          value={numberValue(a.weight)}
                          onChange={(e) =>
                            patchWork(a.id, {
                              weight: numberField(e.target.value),
                            })
                          }
                        />
                      </label>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}

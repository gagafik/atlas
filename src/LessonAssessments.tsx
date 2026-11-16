import type { Dispatch, SetStateAction } from "react";
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

// The works of the lesson: the booked ones from the assessment calendar, the form of every work and the add button.
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
  return (
    <div className="journal-assessments">
      <h3>Проверочные работы</h3>
      <p>
        Баллы — от 0 до выбранного максимума. Текстовые оценки (A*, зачёт и
        другие) не входят в средний процент. Вес 0 исключает работу из расчёта;
        итог периода считается по долям категорий школы.
      </p>
      {!!entry.bookings?.length && (
        <div className="info-line">
          <strong>По календарю контрольных:</strong>
          {entry.bookings.map((b) => (
            <p key={b.id}>
              {b.short} «{b.topic}»{" "}
              <button
                type="button"
                disabled={
                  !editable ||
                  entry.document.content.assessments.length >= 10 ||
                  entry.document.content.assessments.some(
                    (x) => x.bookingId === b.id,
                  )
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
            </p>
          ))}
        </div>
      )}
      {entry.document.content.assessments.map((a) => (
        <div key={a.id} className="journal-assessment">
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
                    g.assessmentId === a.id && (g.score !== null || g.label),
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
              value={categoryOf(a, categories).id}
              onChange={(e) => patchWork(a.id, { category: e.target.value })}
            >
              {(categories?.categories ?? [])
                .filter(
                  (k) => k.active || k.id === categoryOf(a, categories).id,
                )
                .map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.name}
                    {k.active ? "" : " (отключена)"}
                  </option>
                ))}
              {!categories?.categories.some(
                (k) => k.id === categoryOf(a, categories).id,
              ) && (
                <option value={categoryOf(a, categories).id}>
                  {categoryOf(a, categories).name}
                </option>
              )}
            </select>
          </label>
          {a.bookingId && entry.bookings?.some((b) => b.id === a.bookingId) && (
            <p className="muted category-note">
              Из календаря:{" "}
              {entry.bookings.find((b) => b.id === a.bookingId)?.short}
            </p>
          )}
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
              {(customWeights.has(a.id) || presetOf(a.weight) === "custom") && (
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
            }}
          >
            Удалить работу
          </button>
        </div>
      ))}
      <button
        type="button"
        disabled={entry.document.content.assessments.length >= 10}
        onClick={() =>
          content((c) => ({
            ...c,
            assessments: [
              ...c.assessments,
              {
                id: crypto.randomUUID(),
                name: `Работа ${c.assessments.length + 1}`,
                kind: "points",
                maxScore: 10,
                weight: 1,
                category: categories?.defaultCategory || "fo",
              },
            ],
          }))
        }
      >
        Добавить работу
      </button>
    </div>
  );
}

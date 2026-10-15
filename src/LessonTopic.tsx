import { useEffect, useState } from "react";
import { api } from "./api";
import type { PlanDetail, TopicReference } from "./curriculum";

export function SavedPlanTopic({ value }: { value?: TopicReference | null }) {
  return value ? (
    <p className="journal-text">
      <strong>Тема плана: {value.title}</strong>
      <br />
      {value.planTitle} · редакция {value.version} · {value.units} ч.
      {value.standard && (
        <>
          <br />
          {value.standard}
        </>
      )}
    </p>
  ) : null;
}

export default function LessonTopic({
  workspace,
  lessonKey,
  day,
  value,
  disabled,
  onChange,
}: {
  workspace: string;
  lessonKey: string;
  day: string;
  value?: TopicReference | null;
  disabled: boolean;
  onChange: (value: TopicReference | null) => void;
}) {
  const [plans, setPlans] = useState<PlanDetail[]>([]),
    [error, setError] = useState("");
  const [loading, setLoading] = useState(true),
    [epoch, setEpoch] = useState(0);
  useEffect(() => {
    let active = true;
    setPlans([]);
    setError("");
    setLoading(true);
    if (disabled) {
      setLoading(false);
      return;
    }
    api(
      `journal/curriculum/lesson?${new URLSearchParams({ lessonKey, day })}`,
      workspace,
    )
      .then((r) => {
        if (active) setPlans(r.items);
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [workspace, lessonKey, day, disabled, epoch]);
  return (
    <section className="lesson-materials" aria-label="Тема учебного плана">
      <h3>Тема учебного плана</h3>
      {value && (
        <p>
          {value.planTitle} · редакция {value.version}
          <br />
          <strong>{value.title}</strong>
          {value.standard && (
            <>
              <br />
              {value.standard}
            </>
          )}
        </p>
      )}
      <label className="field">
        <span>Выбрать тему</span>
        <select
          disabled={disabled || loading}
          value=""
          onChange={(e) => {
            const [planId, topicId] = e.target.value.split(":");
            const plan = plans.find((p) => p.id === planId),
              topic = plan?.content.topics.find((t) => t.id === topicId);
            if (plan && topic)
              onChange({
                planId,
                topicId,
                version: plan.version,
                units: 1,
                planTitle: plan.content.title,
                title: topic.title,
                standard: topic.standard,
                plannedHours: topic.hours,
              });
          }}
        >
          <option value="">
            {loading ? "Загружаем темы…" : "Выберите тему из плана"}
          </option>
          {plans.map((p) => (
            <optgroup key={p.id} label={p.content.title}>
              {p.content.topics.map((t, i) => (
                <option key={t.id} value={`${p.id}:${t.id}`}>
                  {i + 1}. {t.title} · {t.hours} ч.
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      {!loading && !plans.length && !disabled && !error && (
        <p>
          На эту дату нет действующего плана для предмета и группы. Создайте его
          в разделе «Учебные планы».
        </p>
      )}
      {error && (
        <p role="alert">
          {error}{" "}
          <button type="button" onClick={() => setEpoch((e) => e + 1)}>
            Повторить загрузку тем
          </button>
        </p>
      )}
      {value && (
        <>
          <label className="field">
            <span>Часов по теме в этом уроке</span>
            <input
              type="number"
              min={1}
              max={12}
              value={value.units}
              disabled={disabled}
              onChange={(e) =>
                onChange({ ...value, units: Number(e.target.value) })
              }
            />
          </label>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onChange(null)}
          >
            Убрать связь с планом
          </button>
        </>
      )}
      <p className="muted">
        Часы попадут в прогресс после публикации. Текст темы урока можно
        уточнить отдельно; материалы добавляются ниже.
      </p>
    </section>
  );
}

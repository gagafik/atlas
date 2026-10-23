import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { numberField, numberValue } from "./journal";
import type { PlanDetail, TopicReference } from "./curriculum";
import { MaterialView } from "./LessonMaterials";
import type { MaterialSnapshot } from "./teachingMaterials";
import { mergeTopicMaterials } from "./topicMaterialSelection";

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
  materials,
  disabled,
  onChange,
}: {
  workspace: string;
  lessonKey: string;
  day: string;
  value?: TopicReference | null;
  materials: MaterialSnapshot[];
  disabled: boolean;
  onChange: (
    value: TopicReference | null,
    materials?: MaterialSnapshot[],
  ) => void;
}) {
  const [plans, setPlans] = useState<PlanDetail[]>([]),
    [error, setError] = useState("");
  const [loading, setLoading] = useState(true),
    [epoch, setEpoch] = useState(0);
  const sequence = useRef(0);
  const [resolving, setResolving] = useState(false);
  const [pending, setPending] = useState<{
    topic: TopicReference;
    materials: MaterialSnapshot[];
    unavailable: number;
    selected: string[];
  } | null>(null);
  useEffect(() => {
    let active = true;
    setPlans([]);
    setError("");
    setLoading(true);
    sequence.current++;
    setPending(null);
    setResolving(false);
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
      sequence.current++;
    };
  }, [workspace, lessonKey, day, disabled, epoch]);
  async function choose(planId: string, topicId: string) {
    const plan = plans.find((p) => p.id === planId),
      topic = plan?.content.topics.find((t) => t.id === topicId);
    if (!plan || !topic) return;
    const ref: TopicReference = {
      planId,
      topicId,
      version: plan.version,
      units: 1,
      planTitle: plan.content.title,
      title: topic.title,
      standard: topic.standard,
      plannedHours: topic.hours,
    };
    const sequenceId = ++sequence.current;
    setPending(null);
    setError("");
    if (!topic.materials?.length) {
      setResolving(false);
      onChange(ref);
      return;
    }
    setResolving(true);
    try {
      const result = (await api(
        `journal/curriculum/topic-materials?${new URLSearchParams({
          lessonKey,
          day,
          planId,
          topicId,
          version: String(plan.version),
        })}`,
        workspace,
      )) as { materials: MaterialSnapshot[]; unavailable: unknown[] };
      if (sequenceId !== sequence.current) return;
      setPending({
        topic: ref,
        materials: result.materials,
        unavailable: result.unavailable.length,
        selected: result.materials
          .filter(
            (item) =>
              !materials.some(
                (old) => old.id === item.id && old.version !== item.version,
              ),
          )
          .map((item) => item.id),
      });
    } catch (e) {
      if (sequenceId === sequence.current) setError((e as Error).message);
    } finally {
      if (sequenceId === sequence.current) setResolving(false);
    }
  }
  const merged = pending
    ? mergeTopicMaterials(
        materials,
        pending.materials.filter((item) => pending.selected.includes(item.id)),
      )
    : materials;
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
          disabled={disabled || loading || resolving}
          value=""
          onChange={(e) => {
            const [planId, topicId] = e.target.value.split(":");
            void choose(planId, topicId);
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
      {resolving && <p role="status">Проверяем материалы темы…</p>}
      {pending && (
        <section aria-label="Перенос материалов темы">
          <h4>{pending.topic.title}: материалы для урока</h4>
          <p>
            Выберите материалы для добавления. Остальные материалы урока
            сохранятся.
          </p>
          {pending.unavailable > 0 && (
            <p role="status">
              Недоступно материалов: {pending.unavailable}. Автор мог закрыть
              доступ или переместить их в архив. Они не будут добавлены.
            </p>
          )}
          {pending.materials.map((item) => {
            const old = materials.find((m) => m.id === item.id);
            return (
              <div key={item.id}>
                <label>
                  <input
                    type="checkbox"
                    disabled={disabled}
                    checked={pending.selected.includes(item.id)}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setPending(
                        (p) =>
                          p && {
                            ...p,
                            selected: checked
                              ? [...p.selected, item.id]
                              : p.selected.filter((id) => id !== item.id),
                          },
                      );
                    }}
                  />{" "}
                  {old && old.version !== item.version
                    ? `Заменить версию ${old.version} на версию ${item.version}`
                    : `Добавить «${item.title}»`}
                </label>
                <MaterialView item={item} workspace={workspace} />
              </div>
            );
          })}
          {merged.length > 10 && (
            <p role="alert">
              В уроке будет {merged.length} материалов, максимум — 10. Снимите
              лишние отметки или уберите материалы из урока.
            </p>
          )}
          <button
            type="button"
            disabled={disabled || merged.length > 10}
            onClick={() => {
              onChange(pending.topic, merged);
              setPending(null);
            }}
          >
            Использовать тему и выбранные материалы
          </button>{" "}
          <button
            type="button"
            disabled={disabled}
            onClick={() => {
              onChange(pending.topic);
              setPending(null);
            }}
          >
            Только тему
          </button>{" "}
          <button type="button" onClick={() => setPending(null)}>
            Отменить выбор темы
          </button>
        </section>
      )}
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
              value={numberValue(value.units)}
              disabled={disabled}
              onChange={(e) =>
                onChange({ ...value, units: numberField(e.target.value) })
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
        уточнить отдельно. Добавленные материалы станут доступны семье после
        публикации урока.
      </p>
    </section>
  );
}

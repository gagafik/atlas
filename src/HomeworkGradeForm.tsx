import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Assessment, Grade } from "./journal";

export interface ReviewGrade {
  journalVersion: number;
  revision: number;
  assessmentId: string;
  newAssessment?: Assessment;
  score: number | null;
  label: string;
  comment: string;
  replaceExisting: boolean;
}
export interface GradingEvidence {
  assessment: Assessment;
  grade: Grade;
  publishedVersion: number;
  journalVersion: number;
  gradedAt: string;
  grader: string;
}
interface Choice extends Assessment {
  selectable: boolean;
  reason: string;
  publishedGrade: Grade | null;
  draftGrade: Grade | null;
}
interface Options {
  journalVersion: number;
  revision: number;
  allowed: boolean;
  reason: string;
  hasDraftChanges: boolean;
  canCreate: boolean;
  assessments: Choice[];
}

function display(grade: Grade | null, maxScore?: number) {
  if (!grade) return "не выставлена";
  return (
    (grade.score !== null
      ? `${grade.score}${maxScore ? ` / ${maxScore}` : ""}`
      : grade.label || "не выставлена") +
    (grade.comment ? ` · ${grade.comment}` : "")
  );
}

export function RecordedHomeworkGrade({ value }: { value?: GradingEvidence }) {
  if (!value) return null;
  return (
    <div className="info-line">
      <strong>
        Оценка при проверке: {value.assessment.name} ·{" "}
        {display(value.grade, value.assessment.maxScore)}
      </strong>
      <p>
        {value.grader} · {new Date(value.gradedAt).toLocaleString("ru-RU")} ·
        редакция журнала {value.publishedVersion}
      </p>
      <small>
        Здесь сохранена оценка этой проверки. Последующие исправления смотрите в
        журнале. Возврат работы сам по себе не удаляет оценку.
      </small>
    </div>
  );
}

export default function HomeworkGradeForm({
  workspace,
  lessonKey,
  studentId,
  onChange,
  onEdited,
}: {
  workspace: string;
  lessonKey: string;
  studentId: string;
  onChange: (grade: ReviewGrade | null) => void;
  onEdited: () => void;
}) {
  const [options, setOptions] = useState<Options | null>(null),
    [error, setError] = useState(""),
    [epoch, setEpoch] = useState(0);
  const [selection, setSelection] = useState(""),
    [name, setName] = useState("Домашняя работа"),
    [kind, setKind] = useState<"points" | "label">("points");
  const [maxScore, setMaxScore] = useState("10"),
    [weight, setWeight] = useState("1"),
    [score, setScore] = useState(""),
    [label, setLabel] = useState(""),
    [comment, setComment] = useState("");
  const [replace, setReplace] = useState(false);
  const newId = useRef(`homework-${crypto.randomUUID()}`);
  useEffect(() => {
    let live = true;
    setOptions(null);
    setError("");
    setReplace(false);
    api(
      `journal/homework/${encodeURIComponent(lessonKey)}/grading?${new URLSearchParams({ studentId })}`,
      workspace,
    )
      .then((r) => {
        if (live) setOptions(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace, lessonKey, studentId, epoch]);
  const chosen = options?.assessments.find((a) => a.id === selection);
  const isNew = selection === "new";
  const activeKind = isNew ? kind : chosen?.kind;
  const maximum = isNew ? Number(maxScore) : chosen?.maxScore;
  const hasValue = (g?: Grade | null) =>
    !!g && (g.score !== null || !!g.label || !!g.comment);
  const needsReplace =
    !isNew &&
    (hasValue(chosen?.publishedGrade) || hasValue(chosen?.draftGrade));
  useEffect(() => {
    let value: ReviewGrade | null = null;
    const validNew =
      isNew &&
      options?.canCreate &&
      name.trim() &&
      (kind === "label" ||
        (Number.isFinite(Number(maxScore)) &&
          Number(maxScore) > 0 &&
          Number(maxScore) <= 10000 &&
          weight.trim() &&
          Number.isFinite(Number(weight)) &&
          Number(weight) >= 0 &&
          Number(weight) <= 100));
    const validMark =
      activeKind === "points"
        ? score.trim() &&
          Number.isFinite(Number(score)) &&
          Number(score) >= 0 &&
          Number(score) <= (maximum || 0)
        : activeKind === "label" && label.trim();
    if (
      options?.allowed &&
      (validNew || (!isNew && chosen?.selectable)) &&
      validMark &&
      (!needsReplace || replace)
    ) {
      value = {
        journalVersion: options.journalVersion,
        revision: options.revision,
        assessmentId: isNew ? newId.current : selection,
        score: activeKind === "points" ? Number(score) : null,
        label: activeKind === "label" ? label.trim() : "",
        comment,
        replaceExisting: replace,
        ...(isNew
          ? {
              newAssessment: {
                id: newId.current,
                name: name.trim(),
                kind,
                maxScore: kind === "points" ? Number(maxScore) : 10,
                weight: kind === "points" ? Number(weight) : 1,
              },
            }
          : {}),
      };
    }
    onChange(value);
  }, [
    options,
    selection,
    isNew,
    chosen,
    name,
    kind,
    maxScore,
    weight,
    score,
    label,
    comment,
    replace,
    activeKind,
    maximum,
    needsReplace,
    onChange,
  ]);
  return (
    <section className="homework-grade-form">
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {!options && !error && (
        <p role="status">Проверяю журнал и доступные работы…</p>
      )}
      {options && !options.allowed && (
        <p className="info-line">
          {options.reason} Работу можно принять без оценки.
        </p>
      )}
      <button
        type="button"
        onClick={() => {
          setEpoch((n) => n + 1);
          onEdited();
        }}
      >
        Обновить параметры оценки
      </button>
      {options?.allowed && (
        <>
          <p className="muted">
            При принятии оценка сразу появится у ученика и родителей. Остальные
            неопубликованные поля журнала сохранятся в черновике.
          </p>
          {options.hasDraftChanges && (
            <p className="info-line">
              В журнале есть черновые изменения. Будет опубликована только
              выбранная оценка.
            </p>
          )}
          <label className="field">
            <span>Работа для оценки</span>
            <select
              value={selection}
              onChange={(e) => {
                setSelection(e.target.value);
                setReplace(false);
                setScore("");
                setLabel("");
                onEdited();
              }}
            >
              <option value="">Выберите работу</option>
              {options.assessments.map((a) => (
                <option key={a.id} value={a.id} disabled={!a.selectable}>
                  {a.name}
                  {a.selectable ? "" : " · изменена в черновике"}
                </option>
              ))}
              {options.canCreate && (
                <option value="new">Создать работу для этого урока</option>
              )}
            </select>
          </label>
          {!options.canCreate && (
            <p className="muted">
              В уроке уже десять работ. Выберите существующую; изменения в
              журнале могут требовать публикации.
            </p>
          )}
          {isNew && (
            <>
              <p className="muted">
                Новая работа появится в журнале всего урока. Сейчас оценка будет
                записана только этому ученику.
              </p>
              <label className="field">
                <span>Название новой работы</span>
                <input
                  maxLength={100}
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    onEdited();
                  }}
                />
              </label>
              <label className="field">
                <span>Формат оценки</span>
                <select
                  value={kind}
                  onChange={(e) => {
                    setKind(e.target.value as "points" | "label");
                    onEdited();
                  }}
                >
                  <option value="points">Баллы</option>
                  <option value="label">Текстовая оценка</option>
                </select>
              </label>
              {kind === "points" && (
                <>
                  <label className="field">
                    <span>Максимум баллов</span>
                    <input
                      type="number"
                      min={0.01}
                      max={10000}
                      step="any"
                      value={maxScore}
                      onChange={(e) => {
                        setMaxScore(e.target.value);
                        onEdited();
                      }}
                    />
                  </label>
                  <label className="field">
                    <span>Вес новой работы</span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step="any"
                      value={weight}
                      onChange={(e) => {
                        setWeight(e.target.value);
                        onEdited();
                      }}
                    />
                  </label>
                </>
              )}
            </>
          )}
          {chosen && (
            <p className="muted">
              Опубликовано: {display(chosen.publishedGrade, chosen.maxScore)}
              <br />
              Черновик: {display(chosen.draftGrade, chosen.maxScore)}
            </p>
          )}
          {activeKind === "points" && (
            <label className="field">
              <span>Оценка за домашнюю работу (из {maximum})</span>
              <input
                type="number"
                min={0}
                max={maximum}
                step="any"
                value={score}
                onChange={(e) => {
                  setScore(e.target.value);
                  onEdited();
                }}
              />
            </label>
          )}
          {activeKind === "label" && (
            <label className="field">
              <span>Текстовая оценка за домашнюю работу</span>
              <input
                maxLength={30}
                value={label}
                onChange={(e) => {
                  setLabel(e.target.value);
                  onEdited();
                }}
              />
            </label>
          )}
          {(chosen || isNew) && (
            <label className="field">
              <span>Комментарий к оценке в журнале</span>
              <textarea
                maxLength={1000}
                rows={2}
                value={comment}
                onChange={(e) => {
                  setComment(e.target.value);
                  onEdited();
                }}
              />
            </label>
          )}
          {needsReplace && (
            <label className="check-line">
              <input
                type="checkbox"
                checked={replace}
                onChange={(e) => {
                  setReplace(e.target.checked);
                  onEdited();
                }}
              />
              Заменить показанную оценку в публикации и черновике
            </label>
          )}
        </>
      )}
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";
import {
  WEIGHT_PRESETS,
  useAssessmentCategories,
} from "./assessmentCategories";
import {
  defaultLesson,
  lessonChoices,
  newWorkBody,
  type NewWorkForm,
  type OfferedLesson,
} from "./courseTable";
import Popover from "./Popover";

export interface Created {
  lessonKey: string;
  day: string;
  assessmentId: string;
  title: string;
}

// «Новое назначение»: a work for ФО, СОР or СОЧ is added to a lesson of the course as a draft, the way EduPage adds an assignment to its gradebook.
export default function CourseAssignmentDialog({
  workspace,
  groupId,
  subjectId,
  start,
  end,
  year,
  onClose,
  onCreated,
}: {
  workspace: string;
  groupId: string;
  subjectId: string;
  // The period of the table the dialog was opened on.
  start: string;
  end: string;
  // The school year so far: the dialog widens its search to it when the period holds no lesson.
  year: { start: string; end: string };
  onClose: () => void;
  onCreated: (made: Created) => void;
}) {
  const categories = useAssessmentCategories(workspace);
  const [range, setRange] = useState({ start, end });
  const [found, setFound] = useState<{
    revision: number;
    lessons: OfferedLesson[];
  } | null>(null);
  const [loadError, setLoadError] = useState("");
  const [form, setForm] = useState<NewWorkForm>({
    lessonKey: "",
    title: "",
    category: "",
    kind: "points",
    maxScore: "10",
    weight: "1",
    customWeight: "1",
  });
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  // A retry after a lost answer replays the stored result instead of adding a second work.
  const requestId = useRef<{ body: string; id: string } | null>(null);
  useEffect(() => {
    let live = true;
    setFound(null);
    setLoadError("");
    api(
      `journal/course-table/lessons?${new URLSearchParams({ groupId, subjectId, start: range.start, end: range.end })}`,
      workspace,
    )
      .then((r: { revision: number; lessons: OfferedLesson[] }) => {
        if (!live) return;
        setFound(r);
        setForm((f) => ({
          ...f,
          lessonKey: r.lessons.some(
            (l) => l.lessonKey === f.lessonKey && !l.full,
          )
            ? f.lessonKey
            : defaultLesson(r.lessons),
        }));
      })
      .catch((e) => live && setLoadError(e.message));
    return () => {
      live = false;
    };
  }, [workspace, groupId, subjectId, range.start, range.end]);

  const active = (categories?.categories ?? []).filter((c) => c.active);
  const category = form.category || categories?.defaultCategory || "";
  const choices = lessonChoices(found?.lessons ?? []);
  const widened = range.start === year.start && range.end >= year.end;
  const patch = (change: Partial<NewWorkForm>) =>
    setForm((f) => ({ ...f, ...change }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !found) return;
    const made = newWorkBody({ ...form, category });
    if ("error" in made) return setError(made.error);
    const lesson = found.lessons.find((l) => l.lessonKey === form.lessonKey);
    if (!lesson) return setError("Выберите дату урока.");
    const body = {
      groupId,
      subjectId,
      revision: found.revision,
      day: lesson.day,
      version: lesson.version,
      ...made.body,
    };
    const signature = JSON.stringify(body);
    const id =
      requestId.current?.body === signature
        ? requestId.current.id
        : crypto.randomUUID();
    requestId.current = { body: signature, id };
    setBusy(true);
    setError("");
    try {
      const r = await api(
        "journal/course-table/assessments",
        workspace,
        "POST",
        { requestId: id, ...body },
      );
      onCreated({
        lessonKey: r.lessonKey,
        day: r.day,
        assessmentId: r.assessmentId,
        title: made.body.title,
      });
    } catch (x) {
      setError((x as Error).message);
      setBusy(false);
    }
  }

  return (
    <Popover title="Новое назначение" onClose={onClose} focus="input">
      <form className="ct-assign" onSubmit={(e) => void submit(e)}>
        <label className="field">
          <span>Название</span>
          <input
            value={form.title}
            maxLength={100}
            placeholder="Например, СОР 1"
            onChange={(e) => patch({ title: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Категория</span>
          <select
            value={category}
            onChange={(e) => patch({ category: e.target.value })}
          >
            {active.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
            {!active.some((c) => c.id === category) && (
              <option value={category}>{category || "…"}</option>
            )}
          </select>
        </label>
        <label className="field">
          <span>Формат</span>
          <select
            value={form.kind}
            onChange={(e) =>
              patch({ kind: e.target.value as NewWorkForm["kind"] })
            }
          >
            <option value="points">Баллы</option>
            <option value="label">Текстовая оценка</option>
          </select>
        </label>
        {form.kind === "points" && (
          <>
            <label className="field">
              <span>Максимум баллов</span>
              <input
                inputMode="decimal"
                value={form.maxScore}
                onChange={(e) => patch({ maxScore: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Вес работы</span>
              <select
                value={form.weight}
                onChange={(e) => patch({ weight: e.target.value })}
              >
                {WEIGHT_PRESETS.map((w) => (
                  <option key={w.value} value={String(w.value)}>
                    {w.label}
                  </option>
                ))}
                <option value="custom">Свой</option>
              </select>
            </label>
            {form.weight === "custom" && (
              <label className="field">
                <span>Свой вес (от 0 до 100)</span>
                <input
                  inputMode="decimal"
                  value={form.customWeight}
                  onChange={(e) => patch({ customWeight: e.target.value })}
                />
              </label>
            )}
          </>
        )}
        {found && !choices.length ? (
          <div className="ct-assign-empty" role="status">
            <p>
              В выбранном периоде у курса нет урока, в который можно добавить
              работу: уроки ещё не прошли, журнал закрыт или в курсе нет
              занятий.
            </p>
            {!widened && (
              <button
                type="button"
                onClick={() =>
                  setRange({
                    start: year.start,
                    end: year.end,
                  })
                }
              >
                Искать по всему учебному году
              </button>
            )}
          </div>
        ) : (
          <label className="field">
            <span>Дата урока</span>
            <select
              value={form.lessonKey}
              disabled={!found}
              onChange={(e) => patch({ lessonKey: e.target.value })}
            >
              {!found && <option value="">Загружаю уроки…</option>}
              {choices.map((c) => (
                <option key={c.value} value={c.value} disabled={c.disabled}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {loadError && (
          <p role="alert" className="error-banner">
            {loadError}
          </p>
        )}
        {error && (
          <p role="alert" className="error-banner">
            {error}
          </p>
        )}
        <p className="muted">
          Работа появится в таблице черновиком: ученики и родители увидят её
          после «Опубликовать».
        </p>
        <div className="ct-assign-actions">
          <button
            type="submit"
            className="primary"
            disabled={busy || !found || !form.lessonKey}
          >
            Добавить
          </button>
          <button type="button" onClick={onClose}>
            Отмена
          </button>
        </div>
      </form>
    </Popover>
  );
}

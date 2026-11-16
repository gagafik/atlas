import type { Dispatch, SetStateAction } from "react";
import {
  kindLabels,
  PUPIL_LIMIT,
  type Category,
  type NoteForm,
  type Options,
} from "./behaviourNotesModel";

// The form of a new record. The state and the request stay with the dialog.
export default function BehaviourNoteForm({
  options,
  form,
  setForm,
  usable,
  quick,
  course,
  forCourse,
  setForCourse,
  onSubmit,
}: {
  options: Options;
  form: NoteForm;
  setForm: Dispatch<SetStateAction<NoteForm>>;
  // The categories the account may use, and those of them that are quick marks.
  usable: Category[];
  quick: Category[];
  // The subject of the course the pupils were marked in.
  course: { id: string; name: string } | null;
  forCourse: boolean;
  setForCourse: (value: boolean) => void;
  onSubmit: (categoryId?: string, text?: string) => void;
}) {
  const visiblePupils = form.classId
    ? options.students.filter((s) => s.classId === form.classId)
    : options.students;
  return (
    <section className="bn-form" aria-label="Новая запись">
      <h3>Новая запись</h3>
      <fieldset className="bn-pupils">
        <legend>
          Ученики (выбрано {form.students.length}, не больше {PUPIL_LIMIT})
        </legend>
        <label className="bn-wide-row">
          Класс
          <select
            value={form.classId}
            onChange={(e) =>
              setForm((f) => ({ ...f, classId: e.target.value }))
            }
          >
            <option value="">Все мои ученики</option>
            {options.classes.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {visiblePupils.map((s) => (
          <label key={s.id}>
            <input
              type="checkbox"
              checked={form.students.includes(s.id)}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  students: e.target.checked
                    ? [...f.students, s.id]
                    : f.students.filter((id) => id !== s.id),
                }))
              }
            />
            {s.name}
            {s.className ? ` · ${s.className}` : ""}
          </label>
        ))}
      </fieldset>
      {quick.length > 0 && (
        <div className="bn-quick" aria-label="Быстрые отметки">
          <span>Быстрая отметка сегодня:</span>
          {quick.map((c) => (
            <button
              key={c.id}
              className={`bn-chip bn-${c.kind}`}
              disabled={
                !form.students.length || form.students.length > PUPIL_LIMIT
              }
              onClick={() => onSubmit(c.id, "")}
            >
              {c.kind === "praise" ? "＋ " : "− "}
              {c.title}
            </button>
          ))}
        </div>
      )}
      <div className="bn-controls">
        <label>
          Категория
          <select
            value={form.categoryId}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                categoryId: e.target.value,
                printed:
                  options.categories.find((c) => c.id === e.target.value)
                    ?.report ?? false,
              }))
            }
          >
            {options.myLevels.map((level) => (
              <optgroup key={level} label={options.levels[level]}>
                {usable
                  .filter((c) => c.level === level)
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {kindLabels[c.kind]}: {c.title}
                      {c.approval && !options.admin ? " · согласование" : ""}
                      {c.letter ? " · письмо" : ""}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
        </label>
        <label>
          Дата
          <input
            type="date"
            max={options.today}
            value={form.day}
            onChange={(e) => setForm((f) => ({ ...f, day: e.target.value }))}
          />
        </label>
        <label className="bn-check">
          <input
            type="checkbox"
            checked={form.visible}
            onChange={(e) =>
              setForm((f) => ({ ...f, visible: e.target.checked }))
            }
          />
          Показать семье
        </label>
        <label className="bn-check">
          <input
            type="checkbox"
            checked={form.printed}
            onChange={(e) =>
              setForm((f) => ({ ...f, printed: e.target.checked }))
            }
          />
          Печатать в табель
        </label>
        {course && (
          <label className="bn-check">
            <input
              type="checkbox"
              checked={forCourse}
              onChange={(e) => setForCourse(e.target.checked)}
            />
            По предмету «{course.name}»
          </label>
        )}
      </div>
      <label className="bn-wide">
        Комментарий или основание
        <textarea
          maxLength={2000}
          value={form.text}
          onChange={(e) => setForm((f) => ({ ...f, text: e.target.value }))}
        />
      </label>
      <button
        disabled={
          !form.students.length ||
          form.students.length > PUPIL_LIMIT ||
          !form.categoryId ||
          !form.day
        }
        onClick={() => onSubmit()}
      >
        Сохранить
      </button>
    </section>
  );
}

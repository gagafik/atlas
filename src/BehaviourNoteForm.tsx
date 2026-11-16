import { useState, type Dispatch, type SetStateAction } from "react";
import {
  addPupils,
  kindLabels,
  pickerPupils,
  PUPIL_LIMIT,
  togglePupil,
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
  onBack,
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
  // Back to the list; the dialog asks first when the form holds something.
  onBack: () => void;
}) {
  const [search, setSearch] = useState("");
  const { shown, hint } = pickerPupils(
    options.students,
    form.classId,
    search,
    PUPIL_LIMIT,
  );
  const chosen = form.students
    .map((id) => options.students.find((s) => s.id === id))
    .filter((s) => s !== undefined);
  const full = form.students.length >= PUPIL_LIMIT;
  const wholeClass =
    form.classId && !search.trim()
      ? addPupils(
          form.students,
          options.students
            .filter((s) => s.classId === form.classId)
            .map((s) => s.id),
          PUPIL_LIMIT,
        )
      : null;
  return (
    <section className="bn-form bn-note-form" aria-label="Новая запись">
      <div className="bn-form-head">
        <button type="button" onClick={onBack}>
          ← К записям
        </button>
        <h3>Новая запись</h3>
      </div>
      <fieldset className="bn-pick">
        <legend>
          Ученики (выбрано {form.students.length}, не больше {PUPIL_LIMIT})
        </legend>
        {chosen.length > 0 && (
          <ul className="bn-chosen" aria-label="Выбранные ученики">
            {chosen.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  className="bn-chosen-chip"
                  aria-label={`Убрать: ${s.name}${s.className ? `, ${s.className}` : ""}`}
                  onClick={() =>
                    setForm((f) => ({
                      ...f,
                      students: togglePupil(f.students, s.id, PUPIL_LIMIT),
                    }))
                  }
                >
                  {s.name}
                  {s.className ? ` · ${s.className}` : ""} ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="bn-pick-tools">
          <label>
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
          <label>
            Найти ученика
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          {form.classId && (
            <button
              type="button"
              disabled={!wholeClass}
              onClick={() =>
                wholeClass && setForm((f) => ({ ...f, students: wholeClass }))
              }
            >
              Выбрать весь класс
            </button>
          )}
          <button
            type="button"
            disabled={!form.students.length}
            onClick={() => setForm((f) => ({ ...f, students: [] }))}
          >
            Снять выбор
          </button>
        </div>
        <div className="bn-picker">
          {hint ? (
            <p>Выберите класс или найдите ученика</p>
          ) : shown.length === 0 ? (
            <p>Никого не найдено</p>
          ) : (
            shown.map((s) => {
              const on = form.students.includes(s.id);
              return (
                <label key={s.id}>
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={!on && full}
                    onChange={() =>
                      setForm((f) => ({
                        ...f,
                        students: togglePupil(f.students, s.id, PUPIL_LIMIT),
                      }))
                    }
                  />
                  {s.name}
                  {s.className ? ` · ${s.className}` : ""}
                </label>
              );
            })
          )}
        </div>
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
      <div className="bn-form-grid">
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
      <div className="bn-form-actions">
        <button
          className="primary"
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
        <button type="button" onClick={onBack}>
          Отмена
        </button>
      </div>
    </section>
  );
}

import { kinds, type Template } from "./documentTypes";

export default function DocumentTemplateEditor({
  value,
  onChange,
  onSave,
  onCancel,
}: {
  value: Template;
  onChange: (v: Template) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const d = value.definition;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
      className="document-template"
    >
      <h3>{value.id ? "Изменить шаблон" : "Новый шаблон"}</h3>
      <p>
        Текст и поля этого шаблона копируются при создании документа. Изменения
        шаблона применяются только к новым документам.
      </p>
      <div className="document-controls">
        <label>
          Название
          <input
            required
            maxLength={160}
            value={d.title}
            onChange={(e) =>
              onChange({
                ...value,
                definition: { ...d, title: e.target.value },
              })
            }
          />
        </label>
        <label>
          Получатель
          <select
            disabled={!!value.id}
            value={d.kind}
            onChange={(e) =>
              onChange({ ...value, definition: { ...d, kind: e.target.value } })
            }
          >
            {Object.entries(kinds).map(([v, l]) => (
              <option value={v} key={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label>
          Серия номера
          <input
            required
            maxLength={12}
            pattern={"[A-ZА-ЯЁ0-9\\-]{1,12}"}
            value={d.series}
            onChange={(e) =>
              onChange({
                ...value,
                definition: { ...d, series: e.target.value.toUpperCase() },
              })
            }
          />
        </label>
      </div>
      <label className="document-check">
        <input
          type="checkbox"
          checked={value.active}
          onChange={(e) => onChange({ ...value, active: e.target.checked })}
        />
        Разрешить создание и выдачу документов
      </label>
      <label>
        Текст документа
        <textarea
          required
          maxLength={12000}
          rows={10}
          value={d.text}
          onChange={(e) =>
            onChange({ ...value, definition: { ...d, text: e.target.value } })
          }
        />
      </label>
      <p>
        Вставьте нужное поле: {"{{schoolName}}"} — школа; {"{{personName}}"} —
        получатель; {"{{className}}"} — класс; {"{{schoolYear}}"} — учебный год;{" "}
        {"{{date}}"} — дата выдачи; {"{{number}}"} — номер. Оформление простое
        текстовое, без HTML.
      </p>
      <h4>Поля, которые заполняет ответственный</h4>
      {d.fields.map((f, i) => (
        <div className="document-controls" key={i}>
          <label>
            Имя в шаблоне
            <input
              required
              pattern="[A-Za-z][A-Za-z0-9_]{0,39}"
              value={f.key}
              onChange={(e) =>
                onChange({
                  ...value,
                  definition: {
                    ...d,
                    fields: d.fields.map((x, j) =>
                      i === j ? { ...x, key: e.target.value } : x,
                    ),
                  },
                })
              }
            />
          </label>
          <label>
            Подпись поля
            <input
              required
              maxLength={120}
              value={f.label}
              onChange={(e) =>
                onChange({
                  ...value,
                  definition: {
                    ...d,
                    fields: d.fields.map((x, j) =>
                      i === j ? { ...x, label: e.target.value } : x,
                    ),
                  },
                })
              }
            />
          </label>
          <label className="document-check">
            <input
              type="checkbox"
              checked={f.required}
              onChange={(e) =>
                onChange({
                  ...value,
                  definition: {
                    ...d,
                    fields: d.fields.map((x, j) =>
                      i === j ? { ...x, required: e.target.checked } : x,
                    ),
                  },
                })
              }
            />
            Обязательно
          </label>
          <button
            type="button"
            onClick={() =>
              onChange({
                ...value,
                definition: {
                  ...d,
                  fields: d.fields.filter((_, j) => i !== j),
                },
              })
            }
          >
            Убрать поле {i + 1}
          </button>
        </div>
      ))}
      <button
        type="button"
        disabled={d.fields.length >= 20}
        onClick={() =>
          onChange({
            ...value,
            definition: {
              ...d,
              fields: [
                ...d.fields,
                {
                  key: "field" + (d.fields.length + 1),
                  label: "Новое поле",
                  required: true,
                },
              ],
            },
          })
        }
      >
        Добавить поле
      </button>
      <p>
        Поле «purpose» вставляется в текст как {"{{purpose}}"}. Неиспользуемые
        поля не попадут в печатный документ.
      </p>
      <div className="document-actions">
        <button>Сохранить шаблон</button>
        <button type="button" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

import {
  conditions,
  copyStatuses,
  kinds,
  type Book,
  type CopyData,
  type Config,
  type Options,
} from "./libraryTypes";

export function BookEditor({
  value,
  change,
  save,
  cancel,
}: {
  value: Book;
  change: (b: Book) => void;
  save: () => void;
  cancel: () => void;
}) {
  const d = value.data;
  const field = (key: keyof typeof d, label: string, max: number) => (
    <label key={key}>
      {label}
      <input
        required={key === "title"}
        maxLength={max}
        value={String(d[key] ?? "")}
        onChange={(e) =>
          change({ ...value, data: { ...d, [key]: e.target.value } })
        }
      />
    </label>
  );
  return (
    <form
      className="library-book-editor"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <h3>{value.id ? "Изменить название" : "Новое название"}</h3>
      <div className="library-controls">
        {field("title", "Название", 200)}
        {field("author", "Автор", 200)}
        {field("isbn", "ISBN", 32)}
        {field("publisher", "Издательство", 160)}
        <label>
          Год издания
          <input
            type="number"
            min={1000}
            max={9999}
            value={d.year ?? ""}
            onChange={(e) =>
              change({
                ...value,
                data: {
                  ...d,
                  year: e.target.value ? Number(e.target.value) : null,
                },
              })
            }
          />
        </label>
        <label>
          Вид
          <select
            value={d.kind}
            onChange={(e) =>
              change({ ...value, data: { ...d, kind: e.target.value } })
            }
          >
            {Object.entries(kinds).map(([v, n]) => (
              <option key={v} value={v}>
                {n}
              </option>
            ))}
          </select>
        </label>
        {field("grade", "Для параллели", 40)}
        {field("subject", "Предмет", 100)}
      </div>
      <label>
        Описание
        <textarea
          maxLength={2000}
          value={d.description}
          onChange={(e) =>
            change({ ...value, data: { ...d, description: e.target.value } })
          }
        />
      </label>
      <label className="library-check">
        <input
          type="checkbox"
          checked={value.archived}
          onChange={(e) => change({ ...value, archived: e.target.checked })}
        />
        Убрать название в архив и запретить новые выдачи
      </label>
      <div className="library-actions">
        <button>Сохранить название</button>
        <button type="button" onClick={cancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}
export function CopyEditor({
  value,
  change,
}: {
  value: CopyData;
  change: (c: CopyData) => void;
}) {
  return (
    <div className="library-controls">
      <label>
        Инвентарный номер
        <input
          required
          maxLength={80}
          value={value.inventory}
          onChange={(e) => change({ ...value, inventory: e.target.value })}
        />
      </label>
      <label>
        Место хранения
        <input
          maxLength={160}
          value={value.location}
          onChange={(e) => change({ ...value, location: e.target.value })}
        />
      </label>
      <label>
        Состояние экземпляра
        <select
          value={value.condition}
          onChange={(e) => change({ ...value, condition: e.target.value })}
        >
          {Object.entries(conditions).map(([v, n]) => (
            <option key={v} value={v}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <label>
        Учётный статус
        <select
          value={value.status}
          onChange={(e) => change({ ...value, status: e.target.value })}
        >
          {Object.entries(copyStatuses).map(([v, n]) => (
            <option key={v} value={v}>
              {n}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
export function LibrarySettings({
  value,
  options,
  change,
  save,
  cancel,
}: {
  value: Config;
  options: Options;
  change: (c: Config) => void;
  save: () => void;
  cancel: () => void;
}) {
  return (
    <form
      className="library-settings"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <h3>Правила и ответственные</h3>
      <p>
        Доступ выдаётся только к библиотеке. Сотрудник с выбранными классами
        ведёт выдачи их учеников. Полный доступ также разрешает менять фонд и
        проводить инвентаризацию. Назначать права и согласовывать списание может
        администрация.
      </p>
      <div className="library-controls">
        {(
          [
            ["loanDays", "Срок по умолчанию, дней", 365],
            ["maxRenewals", "Разрешено продлений", 20],
            ["maxLoans", "Книг на одного читателя", 200],
          ] as const
        ).map(([key, label, max]) => (
          <label key={key}>
            {label}
            <input
              required
              type="number"
              min={key === "maxRenewals" ? 0 : 1}
              max={max}
              value={value[key]}
              onChange={(e) =>
                change({ ...value, [key]: Number(e.target.value) })
              }
            />
          </label>
        ))}
      </div>
      {value.assignments.map((a, i) => (
        <fieldset key={i} className="library-assignment">
          <legend>Ответственный {i + 1}</legend>
          <label>
            Сотрудник
            <select
              required
              value={a.accountId}
              onChange={(e) =>
                change({
                  ...value,
                  assignments: value.assignments.map((r, j) =>
                    i === j ? { ...r, accountId: e.target.value } : r,
                  ),
                })
              }
            >
              <option value="">Выберите сотрудника</option>
              {options.employees.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="library-check">
            <input
              type="checkbox"
              checked={a.wholeSchool}
              onChange={(e) =>
                change({
                  ...value,
                  assignments: value.assignments.map((r, j) =>
                    i === j
                      ? { ...r, wholeSchool: e.target.checked, classIds: [] }
                      : r,
                  ),
                })
              }
            />
            Вся школа и управление фондом
          </label>
          {!a.wholeSchool && (
            <div className="library-class-checks">
              {options.classes.map((c) => (
                <label className="library-check" key={c.id}>
                  <input
                    type="checkbox"
                    checked={a.classIds.includes(c.id)}
                    onChange={(e) =>
                      change({
                        ...value,
                        assignments: value.assignments.map((r, j) =>
                          i === j
                            ? {
                                ...r,
                                classIds: e.target.checked
                                  ? [...r.classIds, c.id]
                                  : r.classIds.filter((x) => x !== c.id),
                              }
                            : r,
                        ),
                      })
                    }
                  />
                  {c.name}
                </label>
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={() =>
              change({
                ...value,
                assignments: value.assignments.filter((_, j) => j !== i),
              })
            }
          >
            Убрать ответственного
          </button>
        </fieldset>
      ))}
      <div className="library-actions">
        <button
          type="button"
          disabled={value.assignments.length >= 100}
          onClick={() =>
            change({
              ...value,
              assignments: [
                ...value.assignments,
                { accountId: "", wholeSchool: false, classIds: [] },
              ],
            })
          }
        >
          Добавить ответственного
        </button>
        <button>Сохранить настройки</button>
        <button type="button" onClick={cancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

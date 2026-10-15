import type { Definition, Host, Options } from "./meetings";
import { schoolInput, schoolISO } from "./meetings";

export default function MeetingEditor({
  value,
  options,
  onChange,
}: {
  value: Definition;
  options: Options;
  onChange: (value: Definition) => void;
}) {
  const update = (changes: Partial<Definition>) =>
    onChange({ ...value, ...changes });
  const hostChange = (index: number, changes: Partial<Host>) =>
    update({
      hosts: value.hosts.map((h, i) =>
        i === index ? { ...h, ...changes } : h,
      ),
    });
  return (
    <div className="meeting-editor">
      <label>
        Название
        <input
          required
          maxLength={160}
          value={value.title}
          onChange={(e) => update({ title: e.target.value })}
        />
      </label>
      <label>
        Описание
        <textarea
          maxLength={4000}
          value={value.description}
          onChange={(e) => update({ description: e.target.value })}
        />
      </label>
      <div className="meeting-fields">
        {(
          ["startsAt", "endsAt", "bookingOpensAt", "bookingClosesAt"] as const
        ).map((key, i) => (
          <label key={key}>
            {
              [
                "Начало периода встреч",
                "Конец периода встреч",
                "Открыть запись с (необязательно)",
                "Закрыть запись в (необязательно)",
              ][i]
            }
            <input
              type="datetime-local"
              required={i < 2}
              value={value[key] ? schoolInput(value[key]!) : ""}
              onChange={(e) => {
                if (e.target.value)
                  update({ [key]: schoolISO(e.target.value) });
                else if (i > 1) update({ [key]: null });
              }}
            />
          </label>
        ))}
        {(
          [
            "slotMinutes",
            "gapMinutes",
            "cancelMinutes",
            "reminderMinutes",
          ] as const
        ).map((key, i) => (
          <label key={key}>
            {
              [
                "Длительность встречи, мин",
                "Перерыв между встречами, мин",
                "Отмена родителем не позднее, мин до встречи",
                "Напомнить за, мин (0 — отключить)",
              ][i]
            }
            <input
              required
              type="number"
              min={i === 0 ? 5 : 0}
              max={i === 0 ? 60 : i === 1 ? 30 : 10080}
              value={value[key]}
              onChange={(e) => update({ [key]: Number(e.target.value) })}
            />
          </label>
        ))}
      </div>
      <h4>Учителя и сотрудники</h4>
      <p>
        Предметного учителя увидят родители его учеников. Для остальных
        сотрудников школа выбирает классы. Кабинет проверяется на занятость;
        свободное описание места не резервирует помещение.
      </p>
      <label>
        Добавить ведущего
        <select
          value=""
          onChange={(e) => {
            const a = options.hosts.find((h) => h.id === e.target.value);
            if (a)
              update({
                hosts: [
                  ...value.hosts,
                  {
                    accountId: a.id,
                    teacherId: a.teachers[0]?.id || null,
                    scope: a.teachers.length ? "teaching" : "classes",
                    classIds: [],
                    roomId: null,
                    location: "",
                  },
                ],
              });
          }}
        >
          <option value="">Выберите сотрудника</option>
          {options.hosts
            .filter((a) => !value.hosts.some((h) => h.accountId === a.id))
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </select>
      </label>
      {value.hosts.map((host, i) => (
        <fieldset key={host.accountId} className="meeting-host-editor">
          <legend>
            {options.hosts.find((a) => a.id === host.accountId)?.name ||
              "Сотрудник недоступен"}
          </legend>
          <div className="meeting-fields">
            {!!options.hosts.find((a) => a.id === host.accountId)?.teachers
              .length && (
              <label>
                Преподаватель
                <select
                  value={host.teacherId || ""}
                  onChange={(e) => hostChange(i, { teacherId: e.target.value })}
                >
                  {options.hosts
                    .find((a) => a.id === host.accountId)
                    ?.teachers.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                </select>
              </label>
            )}
            <label>
              Кто может записаться
              <select
                value={host.scope}
                onChange={(e) =>
                  hostChange(i, {
                    scope: e.target.value as Host["scope"],
                    classIds: [],
                  })
                }
              >
                {host.teacherId && (
                  <option value="teaching">
                    Родители учеников этого учителя
                  </option>
                )}
                <option value="classes">Родители выбранных классов</option>
              </select>
            </label>
            <label>
              Кабинет
              <select
                value={host.roomId || ""}
                onChange={(e) =>
                  hostChange(i, { roomId: e.target.value || null })
                }
              >
                <option value="">Указать место текстом</option>
                {options.rooms.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Место / пояснение
              <input
                required={!host.roomId}
                maxLength={300}
                value={host.location}
                onChange={(e) => hostChange(i, { location: e.target.value })}
              />
            </label>
          </div>
          {host.scope === "classes" && (
            <div className="meeting-checks">
              {options.classes.map((c) => (
                <label key={c.id}>
                  <input
                    type="checkbox"
                    checked={host.classIds.includes(c.id)}
                    onChange={(e) =>
                      hostChange(i, {
                        classIds: e.target.checked
                          ? [...host.classIds, c.id]
                          : host.classIds.filter((id) => id !== c.id),
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
              update({ hosts: value.hosts.filter((_, n) => n !== i) })
            }
          >
            Убрать ведущего
          </button>
        </fieldset>
      ))}
    </div>
  );
}

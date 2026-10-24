import { useEffect, useState } from "react";
import { uid, named } from "./utils";
import type { State, ScheduleDay } from "./types";
import { DateField, Multi } from "./DatedFields";

// A date that runs the timetable of another weekday (and week), for the whole school or chosen classes.
export default function ScheduleDays({
  state: s,
  busy,
  onCheck,
  onOpenDate,
}: {
  state: State;
  busy: boolean;
  onCheck: (value?: ScheduleDay, removeId?: string) => void;
  onOpenDate: (day: string) => void;
}) {
  const [edit, setEdit] = useState<ScheduleDay | null>(null);
  useEffect(() => setEdit(null), [s.revision]);
  const patterns = s.cycles?.patterns || [];
  const items = (s.scheduleDays || [])
    .slice()
    .sort((a, b) => a.onDate.localeCompare(b.onDate));
  const today = new Date().toLocaleDateString("sv-SE");
  const bounds = { min: s.settings.yearStart, max: s.settings.yearEnd };
  const describe = (d: ScheduleDay) =>
    [
      d.onDate,
      `работает как «${s.settings.days[d.day] ?? d.day}»`,
      ...(d.cycleId ? [named(patterns, d.cycleId)] : []),
      d.classIds.length
        ? d.classIds.map((id) => named(s.classes, id)).join(", ")
        : "вся школа",
      d.name,
    ].join(" · ");
  return (
    <div className="dated-records">
      <div className="dated-toolbar">
        <button
          disabled={busy}
          onClick={() =>
            setEdit({
              id: uid(),
              name: "Отработка",
              onDate:
                today < bounds.min || today > bounds.max ? bounds.min : today,
              day: 0,
              cycleId: null,
              classIds: [],
            })
          }
        >
          Добавить день в расписании
        </button>
      </div>
      {!items.length && (
        <p>
          Дней в расписании пока нет. Они нужны, когда, например, суббота
          работает по расписанию понедельника.
        </p>
      )}
      {items.map((d) => (
        <article key={d.id}>
          <div>
            <strong>{describe(d)}</strong>
          </div>
          <button onClick={() => onOpenDate(d.onDate)}>Открыть дату</button>
          <button disabled={busy} onClick={() => setEdit(d)}>
            Изменить
          </button>
          <button disabled={busy} onClick={() => onCheck(undefined, d.id)}>
            Убрать
          </button>
        </article>
      ))}
      {edit && (
        <form
          className="dated-form"
          onSubmit={(e) => {
            e.preventDefault();
            onCheck(edit);
          }}
        >
          <h3>День в расписании</h3>
          <label className="field">
            <span>Причина / примечание</span>
            <input
              required
              maxLength={160}
              value={edit.name}
              onChange={(e) => setEdit({ ...edit, name: e.target.value })}
            />
          </label>
          <div className="dated-fields">
            <DateField
              label="Дата"
              value={edit.onDate}
              onChange={(v) => setEdit({ ...edit, onDate: v })}
              {...bounds}
            />
            <label className="field">
              <span>Работает по расписанию дня</span>
              <select
                value={edit.day}
                onChange={(e) =>
                  setEdit({ ...edit, day: Number(e.target.value) })
                }
              >
                {s.settings.days.map((name, index) => (
                  <option key={name} value={index}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Неделя расписания</span>
              <select
                value={edit.cycleId || ""}
                onChange={(e) =>
                  setEdit({ ...edit, cycleId: e.target.value || null })
                }
              >
                <option value="">По календарю</option>
                {patterns.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <Multi
            label="Классы (пусто — вся школа)"
            items={s.classes}
            values={edit.classIds}
            onChange={(v) => setEdit({ ...edit, classIds: v })}
          />
          <p className="info-line">
            Уроки выбранного дня недели появятся на эту дату во всех разделах:
            журнал, контроль заполнения, личные расписания, публикации.
            Изменения, уже сделанные для этой даты, придётся убрать или
            переназначить. Записи журнала за дату не проверяются: после удаления
            дня они останутся без урока.
          </p>
          <div className="modal-actions">
            <button type="button" disabled={busy} onClick={() => setEdit(null)}>
              Закрыть форму
            </button>
            <button className="primary" disabled={busy}>
              Проверить
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

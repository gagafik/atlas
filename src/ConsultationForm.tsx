import { useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { api } from "./api";
import type { Options, SeriesPreview } from "./meetings";
import { addDays, shortDate, todaySchool, weekdayNames } from "./meetings";

export default function ConsultationForm({
  workspace,
  admin,
  options,
  onDirty,
  onDone,
  onCancel,
}: {
  workspace: string;
  admin: boolean;
  options: Options;
  onDirty: (dirty: boolean) => void;
  onDone: (id: string) => void;
  onCancel: () => void;
}) {
  // An administrator picks any teacher account; a teacher can only hold their own hours.
  const choices = admin
    ? options.hosts.flatMap((h) =>
        h.teachers.map((t) => ({
          accountId: h.id,
          teacherId: t.id,
          label: `${h.name} · ${t.name}`,
        })),
      )
    : options.ownTeachers.map((t) => ({
        accountId: "",
        teacherId: t.id,
        label: t.name,
      }));
  const first = addDays(todaySchool(), 1);
  const [form, setForm] = useState({
    choice: 0,
    title: "Консультации",
    description: "",
    startDate: first,
    endDate: addDays(first, 28),
    weekdays: [] as number[],
    startTime: "15:00",
    endTime: "16:00",
    slotMinutes: 15,
    gapMinutes: 0,
    cancelMinutes: 60,
    reminderMinutes: 60,
    roomId: "",
    location: "",
  });
  const [preview, setPreview] = useState<SeriesPreview | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const replay = useRef({ key: "", id: "" });
  const change = (patch: Partial<typeof form>) => {
    setForm((old) => ({ ...old, ...patch }));
    setPreview(null);
    onDirty(true);
  };
  const number =
    (
      name: "slotMinutes" | "gapMinutes" | "cancelMinutes" | "reminderMinutes",
    ) =>
    (e: ChangeEvent<HTMLInputElement>) =>
      change({ [name]: Number(e.target.value) });
  function body() {
    const choice = choices[form.choice];
    if (!choice) throw new Error("Выберите преподавателя.");
    return {
      ...(choice.accountId ? { accountId: choice.accountId } : {}),
      teacherId: choice.teacherId,
      title: form.title,
      description: form.description,
      startDate: form.startDate,
      endDate: form.endDate,
      weekdays: [...form.weekdays].sort(),
      startTime: form.startTime,
      endTime: form.endTime,
      slotMinutes: form.slotMinutes,
      gapMinutes: form.gapMinutes,
      cancelMinutes: form.cancelMinutes,
      reminderMinutes: form.reminderMinutes,
      ...(form.roomId ? { roomId: form.roomId } : {}),
      location: form.location,
    };
  }
  async function act(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }
  const check = () =>
    act(async () =>
      setPreview(
        await api("meetings/consultations/preview", workspace, "POST", {
          ...body(),
          requestId: crypto.randomUUID(),
        }),
      ),
    );
  const create = () =>
    act(async () => {
      if (!preview) return;
      const payload = { ...body(), previewHash: preview.previewHash };
      const key = JSON.stringify(payload);
      if (replay.current.key !== key)
        replay.current = { key, id: crypto.randomUUID() };
      const result = await api("meetings/consultations", workspace, "POST", {
        ...payload,
        requestId: replay.current.id,
      });
      replay.current = { key: "", id: "" };
      onDirty(false);
      onDone(result.id);
    });
  return (
    <form
      className="consultation-form"
      onSubmit={(e) => {
        e.preventDefault();
        void check();
      }}
    >
      <fieldset disabled={busy}>
        <legend>Новые часы консультаций</legend>
        {error && (
          <p role="alert" className="meeting-error">
            {error}
          </p>
        )}
        {!choices.length && (
          <p className="meeting-error">
            К аккаунту не привязан преподаватель, поэтому часы консультаций
            создать нельзя.
          </p>
        )}
        <label>
          Преподаватель
          <select
            value={form.choice}
            onChange={(e) => change({ choice: Number(e.target.value) })}
          >
            {choices.map((c, i) => (
              <option key={c.accountId + c.teacherId} value={i}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Название
          <input
            required
            maxLength={160}
            value={form.title}
            onChange={(e) => change({ title: e.target.value })}
          />
        </label>
        <label>
          Описание (необязательно)
          <textarea
            maxLength={2000}
            value={form.description}
            onChange={(e) => change({ description: e.target.value })}
          />
        </label>
        <div className="meeting-fields">
          <label>
            Первый день
            <input
              type="date"
              required
              value={form.startDate}
              onChange={(e) => change({ startDate: e.target.value })}
            />
          </label>
          <label>
            Последний день
            <input
              type="date"
              required
              value={form.endDate}
              onChange={(e) => change({ endDate: e.target.value })}
            />
          </label>
        </div>
        <div className="meeting-checks meeting-weekdays" role="group">
          <span>Дни недели</span>
          {weekdayNames.slice(0, 6).map((name, day) => (
            <label key={name}>
              <input
                type="checkbox"
                checked={form.weekdays.includes(day)}
                onChange={(e) =>
                  change({
                    weekdays: e.target.checked
                      ? [...form.weekdays, day]
                      : form.weekdays.filter((d) => d !== day),
                  })
                }
              />
              {name}
            </label>
          ))}
        </div>
        <div className="meeting-fields">
          <label>
            Приём с
            <input
              type="time"
              required
              value={form.startTime}
              onChange={(e) => change({ startTime: e.target.value })}
            />
          </label>
          <label>
            Приём до
            <input
              type="time"
              required
              value={form.endTime}
              onChange={(e) => change({ endTime: e.target.value })}
            />
          </label>
          <label>
            Минут на ученика
            <input
              type="number"
              min={5}
              max={60}
              value={form.slotMinutes}
              onChange={number("slotMinutes")}
            />
          </label>
          <label>
            Перерыв между учениками, минут
            <input
              type="number"
              min={0}
              max={30}
              value={form.gapMinutes}
              onChange={number("gapMinutes")}
            />
          </label>
          <label>
            Отмена записи не позднее, чем за (минут)
            <input
              type="number"
              min={0}
              max={10080}
              value={form.cancelMinutes}
              onChange={number("cancelMinutes")}
            />
          </label>
          <label>
            Напоминание за (минут, 0 — без напоминания)
            <input
              type="number"
              min={0}
              max={10080}
              value={form.reminderMinutes}
              onChange={number("reminderMinutes")}
            />
          </label>
          <label>
            Кабинет
            <select
              value={form.roomId}
              onChange={(e) => change({ roomId: e.target.value })}
            >
              <option value="">Без кабинета</option>
              {options.rooms.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Или другое место
            <input
              maxLength={300}
              value={form.location}
              onChange={(e) => change({ location: e.target.value })}
            />
          </label>
        </div>
        <p>
          Интервалы не создаются поверх уроков, бронирований кабинета,
          отсутствий и закрытых дней школы: такое время пропускается.
        </p>
        <div className="meeting-toolbar">
          <button
            type="submit"
            disabled={!choices.length || !form.weekdays.length}
          >
            Проверить
          </button>
          <button
            type="button"
            onClick={() => {
              onDirty(false);
              onCancel();
            }}
          >
            Отмена
          </button>
        </div>
        {preview && (
          <div className="meeting-preview" role="status">
            <p>
              Будет создано интервалов: <strong>{preview.slotCount}</strong> в
              днях: <strong>{preview.days}</strong>. Пропущено по занятости и
              закрытым дням: <strong>{preview.skippedCount}</strong>. Время,
              которое уже прошло: <strong>{preview.skippedPast}</strong>.
            </p>
            {preview.skipped.map((s) => (
              <p key={s.date + s.reason}>
                {shortDate(s.date)} · {s.reason}
                {s.count > 1 ? ` (интервалов: ${s.count})` : ""}
              </p>
            ))}
            <button
              type="button"
              className="primary"
              disabled={!preview.slotCount}
              onClick={() => void create()}
            >
              Создать
            </button>
          </div>
        )}
      </fieldset>
    </form>
  );
}

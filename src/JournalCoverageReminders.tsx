import { useEffect, useState } from "react";
import { api } from "./api";
import {
  ruDate,
  runLine,
  schoolDays,
  weekdays,
  type Run,
} from "./journalCoverage";

type Settings = {
  version: number;
  enabled: boolean;
  afterDays: number;
  remindAt: string;
  lessonReminders: boolean;
  weeklyDigest: boolean;
  digestWeekday: number;
  since: string | null;
};
type Loaded = Settings & { lastRuns?: Run[]; today?: string };

/** School-wide reminder settings for administrators; the server refuses everyone else. */
export default function JournalCoverageReminders({
  workspace,
  onSaved,
}: {
  workspace: string;
  onSaved?: (value: {
    enabled: boolean;
    afterDays: number;
    weeklyDigest: boolean;
  }) => void;
}) {
  const [settings, setSettings] = useState<Loaded | null>(null),
    [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const load = async () => {
    setSettings(await api("journal/coverage/reminders", workspace));
  };
  useEffect(() => {
    let live = true;
    setSettings(null);
    api("journal/coverage/reminders", workspace)
      .then((value: Loaded) => {
        if (live) setSettings(value);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  async function save() {
    if (!settings || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      // since, the run list and today are the server's: the model refuses unknown fields, so they are not sent back.
      const { since, lastRuns, today, ...value } = settings;
      const saved: Settings = await api(
        "journal/coverage/reminders",
        workspace,
        "PUT",
        { ...value, reason },
      );
      setSettings({ ...settings, ...saved });
      onSaved?.({
        enabled: saved.enabled,
        afterDays: saved.afterDays,
        weeklyDigest: saved.weeklyDigest,
      });
      setReason("");
      setNotice("Настройки напоминаний сохранены.");
    } catch (e: any) {
      setError(e.message);
      // Someone else saved first: show their values, keep the explanation.
      if (e.status === 409) await load().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }
  if (!settings)
    return error ? (
      <p className="error-banner" role="alert">
        {error}
      </p>
    ) : (
      <p role="status">Загружаю настройки…</p>
    );
  const set = (values: Partial<Settings>) =>
    setSettings({ ...settings, ...values });
  return (
    <form
      className="dated-form coverage-reminders"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label className="check-line">
        <input
          type="checkbox"
          checked={settings.enabled}
          onChange={(e) => set({ enabled: e.target.checked })}
        />
        Напоминать о незаполненных журналах
      </label>
      {settings.since && (
        <p className="muted">
          Напоминания действуют для уроков с {ruDate(settings.since)}.
        </p>
      )}
      <div className="dated-fields">
        <label className="field">
          <span>Через сколько учебных дней</span>
          <input
            type="number"
            min={1}
            max={10}
            required
            value={settings.afterDays}
            onChange={(e) => set({ afterDays: Number(e.target.value) })}
          />
          <small>
            Сейчас: {schoolDays(settings.afterDays || 1)} после урока.
          </small>
        </label>
        <label className="field">
          <span>Во сколько (время школы)</span>
          <input
            type="time"
            required
            value={settings.remindAt}
            onChange={(e) => set({ remindAt: e.target.value })}
          />
        </label>
      </div>
      <label className="check-line">
        <input
          type="checkbox"
          checked={settings.lessonReminders}
          onChange={(e) => set({ lessonReminders: e.target.checked })}
        />
        Напоминание по каждому уроку
      </label>
      <label className="check-line">
        <input
          type="checkbox"
          checked={settings.weeklyDigest}
          onChange={(e) => set({ weeklyDigest: e.target.checked })}
        />
        Недельная сводка
      </label>
      <label className="field">
        <span>День сводки</span>
        <select
          value={settings.digestWeekday}
          disabled={!settings.weeklyDigest}
          onChange={(e) => set({ digestWeekday: Number(e.target.value) })}
        >
          {weekdays.map((name, day) => (
            <option key={day} value={day}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Причина изменения</span>
        <input
          type="text"
          maxLength={500}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      <p className="muted">
        Одно напоминание на урок, учителю, который ведёт урок сейчас (с учётом
        замен). Отменённые уроки, закрытые периоды и уроки до включения не
        напоминаются. Учителя без учётной записи напоминаний не получают — они
        видны в сводке по классам. Фоновое (push) уведомление не содержит
        подробностей: текст виден после входа.
      </p>
      {!!settings.lastRuns?.length && (
        <div>
          <h4>Последние запуски</h4>
          <ul>
            {settings.lastRuns.map((run) => (
              <li key={run.kind + run.onDate}>{runLine(run)}</li>
            ))}
          </ul>
        </div>
      )}
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="modal-actions">
        <button className="primary" disabled={busy}>
          Сохранить
        </button>
      </div>
    </form>
  );
}

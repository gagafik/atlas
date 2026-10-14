import { useEffect, useState } from "react";
import type { Profile, State } from "./types";
import { api } from "./api";
import { uid } from "./utils";
import ChangePreview from "./ChangePreview";
import type { Preview } from "./ChangePreview";

export default function BellsEditor({
  state: s,
  workspace,
  onState,
}: {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Profile | null>(null),
    [day, setDay] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    setDraft(null);
    setPreview(null);
    setError("");
  }, [s.revision, workspace]);
  const periods = draft ? draft.dayPeriods?.[day] || draft.periods : [];
  const override = day !== "" && !!draft?.dayPeriods?.[day];
  function update(patch: Partial<Profile>) {
    setDraft((current) => current && { ...current, ...patch });
    setPreview(null);
  }
  function setPeriods(value: Profile["periods"]) {
    if (!draft) return;
    update(
      day === ""
        ? { periods: value }
        : { dayPeriods: { ...draft.dayPeriods, [day]: value } },
    );
  }
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function resize(add: boolean) {
    if (!draft) return;
    const change = (values: Profile["periods"]) =>
      add
        ? [
            ...values,
            { label: String(values.length), start: "18:00", end: "18:40" },
          ]
        : values.slice(0, -1);
    update({
      periods: change(draft.periods),
      dayPeriods: Object.fromEntries(
        Object.entries(draft.dayPeriods || {}).map(([key, values]) => [
          key,
          change(values),
        ]),
      ),
    });
  }
  return (
    <section className="settings-card full-span bells-editor">
      <header>
        <div>
          <h2>Профили звонков</h2>
          <p>
            Общее время недели и отдельные звонки для любого учебного дня.
            Изменение проверяется вместе с текущим расписанием.
          </p>
        </div>
        <button
          disabled={busy}
          onClick={() => {
            setDay("");
            setPreview(null);
            setDraft({
              id: uid(),
              name: "Новый профиль",
              periods: [{ label: "0", start: "08:00", end: "08:40" }],
              dayPeriods: {},
            });
          }}
        >
          Добавить профиль
        </button>
      </header>
      <div className="bells-profile-list">
        {s.profiles.map((profile) => (
          <button
            key={profile.id}
            disabled={busy}
            onClick={() => {
              setDay("");
              setError("");
              setPreview(null);
              setDraft(structuredClone(profile));
            }}
          >
            <strong>{profile.name}</strong>
            <small>
              {profile.periods.length} периодов ·{" "}
              {Object.keys(profile.dayPeriods || {}).length} дней с отдельными
              звонками
            </small>
          </button>
        ))}
      </div>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {draft && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () =>
              setPreview(
                await api("editor/preview", workspace, "POST", {
                  revision: s.revision,
                  action: "profile",
                  profile: draft,
                }),
              ),
            );
          }}
        >
          <div className="planner-filters">
            <label>
              Название профиля
              <input
                required
                maxLength={160}
                value={draft.name}
                onChange={(e) => update({ name: e.target.value })}
              />
            </label>
            <label>
              Звонки для
              <select value={day} onChange={(e) => setDay(e.target.value)}>
                <option value="">Основное время недели</option>
                {s.settings.days.map((name, i) => (
                  <option key={i} value={i}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {day !== "" && (
            <div className="button-row">
              <p>
                {override
                  ? "У этого дня отдельные звонки."
                  : "Этот день использует основное время недели."}
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  const copy = { ...draft.dayPeriods };
                  if (override) delete copy[day];
                  else copy[day] = structuredClone(draft.periods);
                  update({ dayPeriods: copy });
                }}
              >
                {override ? "Вернуть основное время" : "Задать отдельное время"}
              </button>
            </div>
          )}
          <div className="bells-edit-scroll">
            <table>
              <thead>
                <tr>
                  <th>Номер</th>
                  <th>Подпись</th>
                  <th>Начало</th>
                  <th>Конец</th>
                </tr>
              </thead>
              <tbody>
                {periods.map((period, i) => (
                  <tr key={i}>
                    <th>{i}</th>
                    {(["label", "start", "end"] as const).map((key) => (
                      <td key={key}>
                        <input
                          aria-label={`${key === "label" ? "Подпись" : key === "start" ? "Начало" : "Конец"} урока ${i}`}
                          type={key === "label" ? "text" : "time"}
                          required
                          value={period[key]}
                          disabled={busy || (day !== "" && !override)}
                          onChange={(e) =>
                            setPeriods(
                              periods.map((p, pi) =>
                                pi === i ? { ...p, [key]: e.target.value } : p,
                              ),
                            )
                          }
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">
            Номера уроков остаются одинаковыми во все дни. Добавление или
            удаление последнего периода меняет все дни профиля. Удаление
            используемого периода или профиля блокируется.
          </p>
          <div className="button-row">
            <button
              type="button"
              disabled={busy || draft.periods.length >= 12}
              onClick={() => resize(true)}
            >
              Добавить период в конец
            </button>
            <button
              type="button"
              disabled={busy || draft.periods.length <= 1}
              onClick={() => resize(false)}
            >
              Убрать последний период
            </button>
            <button className="primary" disabled={busy}>
              Проверить звонки
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setDraft(null);
                setPreview(null);
              }}
            >
              Закрыть редактор
            </button>
          </div>
          {s.profiles.some((p) => p.id === draft.id) && (
            <details>
              <summary>Удаление профиля</summary>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  run(async () =>
                    setPreview(
                      await api("editor/preview", workspace, "POST", {
                        revision: s.revision,
                        action: "removeProfile",
                        ids: [draft.id],
                      }),
                    ),
                  )
                }
              >
                Проверить удаление профиля
              </button>
            </details>
          )}
        </form>
      )}
      {preview && (
        <ChangePreview
          key={preview.token}
          preview={preview}
          busy={busy}
          onCancel={() => setPreview(null)}
          onApply={() =>
            run(async () =>
              onState(
                await api("editor/apply", workspace, "POST", {
                  revision: s.revision,
                  token: preview.token,
                }),
              ),
            )
          }
        />
      )}
    </section>
  );
}

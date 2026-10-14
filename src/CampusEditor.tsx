import { useEffect, useState } from "react";
import type { Campus, State } from "./types";
import { api } from "./api";
import { uid } from "./utils";
import ChangePreview from "./ChangePreview";
import type { Preview } from "./ChangePreview";

export default function CampusEditor({
  state,
  workspace,
  onState,
}: {
  state: State;
  workspace: string;
  onState: (state: State) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Campus | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setDraft(null);
    setPreview(null);
    setError("");
  }, [state.revision, workspace]);
  const campus = state.campus || {
    buildings: [],
    transfers: [],
    defaultTravelMinutes: 0,
  };
  function change(value: Campus) {
    setDraft(value);
    setPreview(null);
  }
  async function prepare(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setPreview(
        await api("editor/preview", workspace, "POST", {
          revision: state.revision,
          action: "campus",
          campus: draft,
        }),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      const next = await api("editor/apply", workspace, "POST", {
        token: preview.token,
        revision: preview.revision,
      });
      await onState(next);
      setDraft(null);
      setPreview(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="campus-editor">
      <div className="section-heading">
        <div>
          <h3>Здания и переходы</h3>
          <p className="muted">
            {campus.buildings.length} зданий · {campus.transfers.length}{" "}
            направлений · общий переход {campus.defaultTravelMinutes} мин.
          </p>
        </div>
        {!draft && (
          <button
            onClick={() => {
              setDraft(structuredClone(campus));
              setError("");
            }}
          >
            Настроить здания
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {draft && !preview && (
        <form onSubmit={prepare}>
          <p>
            Переходы проверяются для всех учителей и учеников между уроками
            одного дня. Внутри здания переход равен нулю. Кабинет без здания не
            позволяет проверить переход.
          </p>
          <label className="field">
            <span>Переход между зданиями по умолчанию, минут</span>
            <input
              type="number"
              required
              min={0}
              max={180}
              value={draft.defaultTravelMinutes}
              onChange={(e) =>
                change({
                  ...draft,
                  defaultTravelMinutes: Number(e.target.value),
                })
              }
            />
          </label>
          <h4>Здания</h4>
          {draft.buildings.map((b, i) => (
            <div className="campus-row" key={b.id}>
              <label className="field">
                <span>Название здания {i + 1}</span>
                <input
                  required
                  maxLength={160}
                  value={b.name}
                  onChange={(e) =>
                    change({
                      ...draft,
                      buildings: draft.buildings.map((x) =>
                        x.id === b.id ? { ...x, name: e.target.value } : x,
                      ),
                    })
                  }
                />
              </label>
              <button
                type="button"
                disabled={state.rooms.some((r) => r.buildingId === b.id)}
                title={
                  state.rooms.some((r) => r.buildingId === b.id)
                    ? "Сначала измените связанные кабинеты"
                    : "Удалить здание"
                }
                onClick={() =>
                  change({
                    ...draft,
                    buildings: draft.buildings.filter((x) => x.id !== b.id),
                    transfers: draft.transfers.filter(
                      (t) => t.fromId !== b.id && t.toId !== b.id,
                    ),
                  })
                }
              >
                Удалить
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() =>
              change({
                ...draft,
                buildings: [...draft.buildings, { id: uid(), name: "" }],
              })
            }
          >
            Добавить здание
          </button>
          <h4>Отдельные направления</h4>
          <p className="muted small">
            Каждая строка действует только в указанном направлении. Для
            обратного пути добавьте ещё одну строку. Для остальных пар
            применяется общее время.
          </p>
          {draft.transfers.map((t, i) => (
            <div className="campus-row" key={i}>
              {(["fromId", "toId"] as const).map((field) => (
                <label className="field" key={field}>
                  <span>
                    {field === "fromId" ? "Откуда" : "Куда"} · {i + 1}
                  </span>
                  <select
                    required
                    value={t[field]}
                    onChange={(e) =>
                      change({
                        ...draft,
                        transfers: draft.transfers.map((x, n) =>
                          n === i ? { ...x, [field]: e.target.value } : x,
                        ),
                      })
                    }
                  >
                    <option value="">Выберите здание</option>
                    {draft.buildings.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name || "Новое здание"}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <label className="field">
                <span>Минут · {i + 1}</span>
                <input
                  type="number"
                  required
                  min={0}
                  max={180}
                  value={t.minutes}
                  onChange={(e) =>
                    change({
                      ...draft,
                      transfers: draft.transfers.map((x, n) =>
                        n === i ? { ...x, minutes: Number(e.target.value) } : x,
                      ),
                    })
                  }
                />
              </label>
              <button
                type="button"
                onClick={() =>
                  change({
                    ...draft,
                    transfers: draft.transfers.filter((_, n) => n !== i),
                  })
                }
              >
                Удалить
              </button>
            </div>
          ))}
          <button
            type="button"
            disabled={draft.buildings.length < 2}
            onClick={() =>
              change({
                ...draft,
                transfers: [
                  ...draft.transfers,
                  { fromId: "", toId: "", minutes: draft.defaultTravelMinutes },
                ],
              })
            }
          >
            Добавить направление
          </button>
          <p className="info-line">
            Можно сохранить новые требования с уже существующими нарушениями,
            затем исправить расписание или запустить генерацию. Перед
            сохранением показывается проверка.
          </p>
          <div className="modal-actions">
            <button type="button" onClick={() => setDraft(null)}>
              Отмена
            </button>
            <button className="primary" disabled={busy}>
              Проверить изменения
            </button>
          </div>
        </form>
      )}
      {preview && (
        <ChangePreview
          preview={preview}
          busy={busy}
          onApply={apply}
          onCancel={() => setPreview(null)}
          title="Изменение зданий и переходов"
        />
      )}
    </section>
  );
}

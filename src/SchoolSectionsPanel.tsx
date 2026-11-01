import { useEffect, useState } from "react";
import { api } from "./api";

interface Item {
  id: string;
  label: string;
  hidden: boolean;
}
interface Setting {
  version: number;
  hidden: string[];
  items: Item[];
}

/** The administrator chooses which optional sections the menu shows. Hiding removes only the button. */
export default function SchoolSectionsPanel({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: (hidden: string[]) => void;
}) {
  const [setting, setSetting] = useState<Setting | null>(null),
    [hidden, setHidden] = useState<string[]>([]),
    [reason, setReason] = useState("");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function load() {
    const r: Setting = await api("school-sections", "school");
    setSetting(r);
    setHidden(r.hidden);
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);
  async function save() {
    if (!setting) return;
    setBusy(true);
    setError("");
    try {
      const r: Setting = await api("school-sections", "school", "PUT", {
        version: setting.version,
        hidden,
        reason,
      });
      setSetting(r);
      setHidden(r.hidden);
      setReason("");
      onChanged(r.hidden);
    } catch (e: any) {
      setError(e.message);
      // Somebody saved first: show what is stored now.
      if (e.status === 409) await load().catch(() => {});
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="auth-password"
        role="dialog"
        aria-modal="true"
        aria-label="Разделы школы"
      >
        <h2>Разделы школы</h2>
        <p>
          Скрытие убирает только кнопку из меню. Данные, уведомления и права
          доступа не меняются, опубликованный сайт школы и ссылка на приём
          остаются доступными. Остальные пользователи увидят изменение после
          обновления страницы или нового входа.
        </p>
        {error && (
          <p className="error-banner" role="alert">
            {error}
          </p>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {setting?.items.map((item) => (
            <label key={item.id} className="homework-queue-history">
              <input
                type="checkbox"
                checked={!hidden.includes(item.id)}
                disabled={busy}
                onChange={(e) =>
                  setHidden(
                    e.target.checked
                      ? hidden.filter((id) => id !== item.id)
                      : [...hidden, item.id],
                  )
                }
              />{" "}
              Показывать: {item.label}
            </label>
          ))}
          <label className="field">
            <span>Причина изменения</span>
            <input
              required
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <div className="dated-toolbar">
            <button type="button" disabled={busy} onClick={onClose}>
              Закрыть
            </button>
            <button className="primary" disabled={busy || !setting}>
              Сохранить
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

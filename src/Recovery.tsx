import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import "./recovery.css";

type RecoveryStatus = {
  version: number;
  status: "none" | "active" | "revoked" | "expired" | "exhausted";
  remaining: number;
  expires: string | null;
  lastUsed: string | null;
  count?: number;
  days?: number;
};

const statuses = {
  none: "Резервные коды ещё не созданы.",
  active: "Резервные коды действуют.",
  revoked: "Прежние коды отозваны.",
  expired: "Срок действия прежних кодов истёк.",
  exhausted: "Все коды использованы.",
};
const dateLabel = (value: string) => new Date(value).toLocaleString("ru-RU");

export function RecoveryPanel({
  login,
  onClose,
  onAuthCheck,
}: {
  login: string;
  onClose: () => void;
  onAuthCheck: () => Promise<unknown>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(true),
    pending = useRef(false);
  const loadSequence = useRef(0);
  const [status, setStatus] = useState<RecoveryStatus | null>(null);
  const [action, setAction] = useState<"codes" | "revoke" | null>(null);
  const [password, setPassword] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [saved, setSaved] = useState(false),
    [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");

  async function load() {
    const sequence = ++loadSequence.current;
    try {
      const result = await api("auth/recovery/options", "school");
      if (alive.current && sequence === loadSequence.current) setStatus(result);
    } catch (e: any) {
      if (!alive.current || sequence !== loadSequence.current) return;
      if (alive.current) setError(e.message);
      if (e.status === 401) void onAuthCheck();
    }
  }
  useEffect(() => {
    alive.current = true;
    dialog.current?.showModal();
    void load();
    return () => {
      alive.current = false;
      ++loadSequence.current;
    };
    // This component is keyed by the current account and version in AuthShell.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function close() {
    if (pending.current) return;
    if (
      codes.length &&
      !saved &&
      !window.confirm(
        "Закрыть без сохранения? Увидеть эти коды повторно нельзя. Позже можно будет выпустить новый набор.",
      )
    )
      return;
    setCodes([]);
    setPassword("");
    onClose();
  }
  async function submit() {
    if (pending.current || !action || !status) return;
    pending.current = true;
    ++loadSequence.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api("auth/recovery/" + action, "school", "POST", {
        version: status.version,
        currentPassword: password,
      });
      if (!alive.current) return;
      setStatus(result);
      setCodes(result.codes || []);
      setSaved(false);
      setAction(null);
      if (action === "revoke") setNotice("Резервные коды отозваны.");
    } catch (e: any) {
      if (!alive.current) return;
      setError(
        e.status
          ? e.message
          : "Ответ сервера не получен. Обновите состояние: если набор был создан, выпустите новый, чтобы получить коды.",
      );
      if (e.status === 401) void onAuthCheck();
      // Never repeat a credential write automatically after an uncertain result.
      await load();
      if (alive.current) setAction(null);
    } finally {
      pending.current = false;
      if (alive.current) {
        setBusy(false);
        setPassword("");
      }
    }
  }
  function codeText() {
    return `Атлас · резервные коды\nЛогин: ${login}\nДействуют до: ${status?.expires ? dateLabel(status.expires) : ""}\nКаждый код можно использовать один раз. Храните отдельно от устройства входа.\n\n${codes.join("\n")}\n`;
  }
  function download() {
    const url = URL.createObjectURL(
      new Blob([codeText()], { type: "text/plain;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "atlas-recovery-codes.txt";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <dialog
      ref={dialog}
      className="recovery-dialog"
      aria-labelledby="recovery-title"
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
    >
      <div className="recovery-heading">
        <h2 id="recovery-title">Резервные коды</h2>
        <button type="button" disabled={busy} onClick={close}>
          Закрыть
        </button>
      </div>
      <p>
        Если забудете пароль, один из этих кодов поможет восстановить доступ к
        аккаунту <strong>{login}</strong>.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!status && (
        <>
          <p>Загружаю состояние…</p>
          <button type="button" onClick={() => void load()}>
            Повторить
          </button>
        </>
      )}
      {status &&
        (codes.length ? (
          <>
            <h3>Сохраните коды сейчас</h3>
            <p>
              Повторно посмотреть их нельзя. Каждый код действует один раз, до{" "}
              {dateLabel(status.expires!)}. Храните файл или распечатку отдельно
              от устройства входа и не передавайте другим.
            </p>
            <ol className="recovery-codes">
              {codes.map((code) => (
                <li key={code}>
                  <code>{code}</code>
                </li>
              ))}
            </ol>
            <div className="recovery-actions">
              <button
                type="button"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(codeText());
                    if (alive.current)
                      setNotice(
                        "Коды скопированы. Сохраните их в надёжном месте.",
                      );
                  } catch {
                    if (alive.current)
                      setError(
                        "Не удалось скопировать. Скачайте коды или выделите и скопируйте их вручную.",
                      );
                  }
                }}
              >
                Скопировать коды
              </button>
              <button type="button" onClick={download}>
                Скачать коды
              </button>
            </div>
            <label className="recovery-saved">
              <input
                type="checkbox"
                checked={saved}
                onChange={(e) => setSaved(e.target.checked)}
              />
              Я сохранил коды
            </label>
            <button
              type="button"
              className="primary"
              disabled={!saved}
              onClick={close}
            >
              Готово
            </button>
          </>
        ) : (
          <>
            <p role="status">
              {statuses[status.status]}{" "}
              {status.status === "active" && `Осталось: ${status.remaining}.`}
            </p>
            {status.expires && (
              <p>Срок набора: до {dateLabel(status.expires)}.</p>
            )}
            {status.lastUsed && (
              <p>Последнее восстановление: {dateLabel(status.lastUsed)}.</p>
            )}
            <p>
              Новый набор заменяет прежний. Обычная смена пароля или сброс
              администратором также отзывают коды. Восстановление завершает все
              сеансы аккаунта.
            </p>
            {action ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void submit();
                }}
              >
                <h3>
                  {action === "codes"
                    ? "Выпустить новый набор"
                    : "Отозвать резервные коды"}
                </h3>
                <p>
                  {action === "codes"
                    ? `Будут созданы ${status.count || 8} кодов на ${status.days || 365} дней. Все прежние коды перестанут работать.`
                    : "Все оставшиеся коды перестанут работать. Если забудете пароль, потребуется помощь администратора."}
                </p>
                <label className="field">
                  <span>Текущий пароль</span>
                  <input
                    type="password"
                    autoComplete="current-password"
                    required
                    maxLength={128}
                    disabled={busy}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoFocus
                  />
                </label>
                <div className="recovery-actions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setAction(null);
                      setPassword("");
                    }}
                  >
                    Отмена
                  </button>
                  <button className="primary" disabled={busy}>
                    {busy
                      ? "Сохраняю…"
                      : action === "codes"
                        ? "Создать коды"
                        : "Подтвердить отзыв"}
                  </button>
                </div>
              </form>
            ) : (
              <div className="recovery-actions">
                <button
                  type="button"
                  className="primary"
                  onClick={() => {
                    setAction("codes");
                    setError("");
                  }}
                >
                  {status.status === "active"
                    ? "Заменить набор"
                    : "Создать резервные коды"}
                </button>
                {status.status === "active" && (
                  <button
                    type="button"
                    onClick={() => {
                      setAction("revoke");
                      setError("");
                    }}
                  >
                    Отозвать коды
                  </button>
                )}
              </div>
            )}
          </>
        ))}
    </dialog>
  );
}

export function RecoveryForm({
  initialLogin,
  onComplete,
  onCancel,
}: {
  initialLogin: string;
  onComplete: (login: string) => void;
  onCancel: () => void;
}) {
  const [login, setLogin] = useState(initialLogin),
    [code, setCode] = useState("");
  const [password, setPassword] = useState(""),
    [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const pending = useRef(false),
    alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  async function submit() {
    if (pending.current) return;
    setError("");
    if (password !== repeat) {
      setError("Новые пароли не совпадают.");
      return;
    }
    pending.current = true;
    setBusy(true);
    try {
      await api("auth/recover", "school", "POST", { login, code, password });
      if (!alive.current) return;
      setCode("");
      setPassword("");
      setRepeat("");
      onComplete(login.trim().toLowerCase());
    } catch (e: any) {
      if (alive.current)
        setError(
          e.status
            ? e.message
            : "Ответ сервера не получен. Попробуйте войти с новым паролем. Если не получится, повторите восстановление с неиспользованным кодом.",
        );
    } finally {
      pending.current = false;
      if (alive.current) setBusy(false);
    }
  }
  return (
    <form
      className="recovery-form"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <h1>Восстановить доступ</h1>
      <p>
        Введите код из набора, который вы заранее сохранили в своём аккаунте.
        Если кодов нет, обратитесь к администратору школы.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <label className="field">
        <span>Логин</span>
        <input
          autoComplete="username"
          required
          maxLength={64}
          disabled={busy}
          value={login}
          onChange={(e) => setLogin(e.target.value)}
        />
      </label>
      <label className="field">
        <span>Резервный код</span>
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          required
          maxLength={128}
          disabled={busy}
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <small>Один код целиком, с дефисами или без них.</small>
      </label>
      <label className="field">
        <span>Новый пароль</span>
        <input
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={128}
          disabled={busy}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <small>От 12 до 128 символов. Можно использовать длинную фразу.</small>
      </label>
      <label className="field">
        <span>Повторите новый пароль</span>
        <input
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={128}
          disabled={busy}
          value={repeat}
          onChange={(e) => setRepeat(e.target.value)}
        />
      </label>
      <p>
        После восстановления все сеансы завершатся. Этот код станет
        недействительным, остальные сохранённые коды останутся доступны.
      </p>
      <div className="recovery-actions">
        <button type="button" disabled={busy} onClick={onCancel}>
          Вернуться ко входу
        </button>
        <button className="primary" disabled={busy}>
          {busy ? "Восстанавливаю…" : "Сохранить новый пароль"}
        </button>
      </div>
    </form>
  );
}

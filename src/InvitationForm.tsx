import { useState } from "react";
import { api } from "./api";
import type { AuthStatus } from "./auth";

export default function InvitationForm({
  existing = false,
  onComplete,
  onCancel,
}: {
  existing?: boolean;
  onComplete: (session: AuthStatus) => void;
  onCancel: () => void;
}) {
  const [code, setCode] = useState(""),
    [name, setName] = useState("");
  const [login, setLogin] = useState(""),
    [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <form
      className="invitation-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (busy) return;
        setError("");
        if (!existing && password !== repeat) {
          setError("Пароли не совпадают.");
          return;
        }
        setBusy(true);
        try {
          const result = await api(
            existing ? "auth/invitations/accept" : "auth/register",
            "school",
            "POST",
            existing
              ? { code: code.trim(), currentPassword: password }
              : { code: code.trim(), name, login, password },
          );
          setCode("");
          setPassword("");
          setRepeat("");
          onComplete(result);
        } catch (e: any) {
          setError(e.message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <h2>{existing ? "Принять приглашение" : "Создать аккаунт"}</h2>
      <p>
        {existing
          ? "Введите код школы, чтобы добавить доступ к своему аккаунту, например ко второму ребёнку. Роль приглашения должна совпадать с вашей."
          : "Получите персональный код у администратора школы. Школа заранее указывает вашу роль и доступные расписания."}
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      <fieldset className="access-fields" disabled={busy}>
        <label className="field">
          <span>Код приглашения</span>
          <input
            name="invitation-code"
            autoComplete="off"
            spellCheck={false}
            autoCapitalize="none"
            required
            minLength={32}
            maxLength={128}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        {!existing && (
          <>
            <label className="field">
              <span>Ваше имя и фамилия</span>
              <input
                name="name"
                autoComplete="name"
                required
                maxLength={160}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Придумайте логин</span>
              <input
                name="login"
                autoComplete="username"
                autoCapitalize="none"
                required
                pattern={"[a-z0-9][a-z0-9._\\-]{2,63}"}
                maxLength={64}
                value={login}
                onChange={(e) => setLogin(e.target.value.toLowerCase())}
              />
              <small>
                3–64 символа: латинские буквы, цифры, точка, дефис,
                подчёркивание.
              </small>
            </label>
          </>
        )}
        <label className="field">
          <span>{existing ? "Ваш текущий пароль" : "Придумайте пароль"}</span>
          <input
            name="password"
            type="password"
            autoComplete={existing ? "current-password" : "new-password"}
            required
            minLength={existing ? 1 : 12}
            maxLength={128}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {!existing && (
            <small>
              От 12 до 128 символов. Можно использовать длинную фразу.
            </small>
          )}
        </label>
        {!existing && (
          <label className="field">
            <span>Повторите пароль</span>
            <input
              name="repeat-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
            />
          </label>
        )}
        <div className="dated-toolbar">
          <button type="button" onClick={onCancel}>
            {existing ? "Отмена" : "Уже есть аккаунт — войти"}
          </button>
          <button className="primary">
            {busy
              ? "Сохраняю…"
              : existing
                ? "Добавить доступ"
                : "Зарегистрироваться"}
          </button>
        </div>
      </fieldset>
    </form>
  );
}

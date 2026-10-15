import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import Portal from "./Portal";
import { api } from "./api";
import type { AuthStatus } from "./auth";
import NotificationCenter from "./NotificationCenter";
import DeviceTools from "./DeviceTools";
import { startPolling } from "./polling";
import InvitationForm from "./InvitationForm";
import { canLeaveHomework } from "./homeworkNavigation";
const App = lazy(() => import("./App"));

export default function AuthShell() {
  const [session, setSession] = useState<AuthStatus | null>(null),
    [error, setError] = useState("");
  const [login, setLogin] = useState(""),
    [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false),
    [passwordForm, setPasswordForm] = useState(false);
  const [oldPassword, setOldPassword] = useState(""),
    [newPassword, setNewPassword] = useState("");
  const [accountError, setAccountError] = useState("");
  const [registration, setRegistration] = useState(false),
    [invitationForm, setInvitationForm] = useState(false);
  const refreshSequence = useRef(0);
  useEffect(() => {
    setPasswordForm(false);
    setOldPassword("");
    setNewPassword("");
    setAccountError("");
    setInvitationForm(false);
  }, [session?.user?.id]);
  function acceptInvitation(result: AuthStatus) {
    ++refreshSequence.current;
    setSession(result);
    setRegistration(false);
    setInvitationForm(false);
    setError("");
    setAccountError("");
  }
  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current;
    try {
      const result = await api("auth/status", "school");
      if (sequence !== refreshSequence.current) return true;
      setSession(result);
      setError("");
      return true;
    } catch (e: any) {
      if (sequence === refreshSequence.current) setError(e.message);
      return false;
    }
  }, []);
  useEffect(() => {
    const polling = startPolling(refresh, { interval: 60000 });
    const update = () => polling.refresh();
    window.addEventListener("atlas-auth-required", update);
    return () => {
      polling.stop();
      ++refreshSequence.current;
      window.removeEventListener("atlas-auth-required", update);
    };
  }, [refresh]);
  async function signIn() {
    ++refreshSequence.current;
    setBusy(true);
    setError("");
    try {
      const result = await api("auth/login", "school", "POST", {
        login,
        password,
      });
      ++refreshSequence.current;
      setSession(result);
      setPassword("");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function signOut() {
    if (!canLeaveHomework()) return;
    ++refreshSequence.current;
    setBusy(true);
    setAccountError("");
    try {
      await api("auth/logout", "school", "POST");
      ++refreshSequence.current;
      setPasswordForm(false);
      setOldPassword("");
      setNewPassword("");
      await refresh();
    } catch (e: any) {
      setAccountError(e.message);
    } finally {
      setBusy(false);
    }
  }
  if (!session)
    return (
      <main className="auth-screen">
        <h1>Атлас</h1>
        <p>{error || "Проверяю доступ…"}</p>
        <DeviceTools />
        {error && <button onClick={() => void refresh()}>Повторить</button>}
      </main>
    );
  if (session.enabled && !session.user)
    return (
      <main className="auth-screen">
        {registration ? (
          <InvitationForm
            onComplete={acceptInvitation}
            onCancel={() => setRegistration(false)}
          />
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void signIn();
            }}
          >
            <h1>Войти в Атлас</h1>
            <p>
              Войдите в свой аккаунт или зарегистрируйтесь по приглашению школы.
            </p>
            {error && (
              <p role="alert" className="error-banner">
                {error}
              </p>
            )}
            <label className="field">
              <span>Логин</span>
              <input
                autoComplete="username"
                required
                maxLength={64}
                value={login}
                onChange={(e) => setLogin(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Пароль</span>
              <input
                type="password"
                autoComplete="current-password"
                required
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <button className="primary" disabled={busy}>
              {busy ? "Вхожу…" : "Войти"}
            </button>
            <p>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setError("");
                  setRegistration(true);
                }}
              >
                Создать аккаунт по приглашению
              </button>
            </p>
          </form>
        )}
        <DeviceTools />
      </main>
    );
  const controls = (
    <div className="account-controls">
      <DeviceTools />
      <NotificationCenter
        key={session.user?.id + ":" + session.user?.version}
        identity={
          (session.user?.id || "local") + ":" + (session.user?.version || 0)
        }
      />
      {session.user && (
        <>
          <span>{session.user.name}</span>
          {["parent", "student", "teacher"].includes(session.user.role) && (
            <button onClick={() => setInvitationForm(true)}>
              Принять приглашение
            </button>
          )}
          <button
            onClick={() => {
              setPasswordForm(true);
              setAccountError("");
            }}
          >
            Пароль
          </button>
          <button disabled={busy} onClick={() => void signOut()}>
            Выйти
          </button>
        </>
      )}
    </div>
  );
  return (
    <>
      {session.user && !["admin", "editor"].includes(session.user.role) ? (
        <Portal
          key={session.user.id + ":" + session.user.version}
          session={session}
          controls={controls}
        />
      ) : (
        <Suspense
          fallback={
            <main className="auth-screen">Открываю рабочее место…</main>
          }
        >
          <App
            key={session.user?.id || "local"}
            session={session}
            onAuthChange={async () => {
              await refresh();
            }}
            sessionControls={controls}
          />
        </Suspense>
      )}
      {!passwordForm && (error || accountError) && (
        <p className="auth-error" role="alert">
          {accountError || error}
        </p>
      )}
      {invitationForm && (
        <div className="modal-backdrop">
          <section
            className="auth-password"
            role="dialog"
            aria-modal="true"
            aria-label="Принять приглашение"
          >
            <InvitationForm
              existing
              onComplete={acceptInvitation}
              onCancel={() => setInvitationForm(false)}
            />
          </section>
        </div>
      )}
      {passwordForm && (
        <div className="modal-backdrop">
          <section
            className="auth-password"
            role="dialog"
            aria-modal="true"
            aria-label="Сменить пароль"
          >
            <h2>Сменить пароль</h2>
            <p>После смены остальные сеансы этой учётной записи завершатся.</p>
            {accountError && (
              <p className="error-banner" role="alert">
                {accountError}
              </p>
            )}
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                setAccountError("");
                try {
                  await api("auth/password", "school", "POST", {
                    currentPassword: oldPassword,
                    password: newPassword,
                  });
                  setPasswordForm(false);
                  setOldPassword("");
                  setNewPassword("");
                  await refresh();
                } catch (e: any) {
                  setAccountError(e.message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <label className="field">
                <span>Текущий пароль</span>
                <input
                  type="password"
                  autoComplete="current-password"
                  required
                  maxLength={128}
                  value={oldPassword}
                  onChange={(e) => setOldPassword(e.target.value)}
                />
              </label>
              <label className="field">
                <span>Новый пароль</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={128}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                />
                <small>
                  От 12 до 128 символов. Можно использовать длинную фразу.
                </small>
              </label>
              <div className="dated-toolbar">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setPasswordForm(false);
                    setOldPassword("");
                    setNewPassword("");
                  }}
                >
                  Отмена
                </button>
                <button className="primary" disabled={busy}>
                  Сохранить пароль
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </>
  );
}

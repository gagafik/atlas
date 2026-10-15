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
import { logoutPushFingerprint, unsubscribeBrowser } from "./push";
import { canLeaveHomework } from "./homeworkNavigation";
const App = lazy(() => import("./App"));
const OperationsPanel = lazy(() => import("./OperationsPanel"));
const SchoolForms = lazy(() => import("./SchoolForms"));
const ParentMeetings = lazy(() => import("./ParentMeetings"));
const StaffTime = lazy(() => import("./StaffTime"));
const SchoolDocuments = lazy(() => import("./SchoolDocuments"));
const ClassStaffWorkspace = lazy(() => import("./ClassStaffWorkspace"));
const RecoveryForm = lazy(() =>
  import("./Recovery").then((module) => ({ default: module.RecoveryForm })),
);
const RecoveryPanel = lazy(() =>
  import("./Recovery").then((module) => ({ default: module.RecoveryPanel })),
);

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
  const [recoveryForm, setRecoveryForm] = useState(false),
    [recoveryPanel, setRecoveryPanel] = useState(false),
    [loginNotice, setLoginNotice] = useState("");
  const [operationsPanel, setOperationsPanel] = useState(false);
  const [formsWorkspace, setFormsWorkspace] = useState<string | null>(null);
  const [documentsWorkspace, setDocumentsWorkspace] = useState<string | null>(
    null,
  );
  useEffect(() => {
    const open = (event: Event) =>
      setDocumentsWorkspace(
        (event as CustomEvent).detail?.workspace || "school",
      );
    window.addEventListener("atlas-open-documents", open);
    return () => window.removeEventListener("atlas-open-documents", open);
  }, []);
  const [staffTimeWorkspace, setStaffTimeWorkspace] = useState<string | null>(
    null,
  );
  useEffect(() => {
    const open = (event: Event) =>
      setStaffTimeWorkspace(
        (event as CustomEvent).detail?.workspace || "school",
      );
    window.addEventListener("atlas-open-staff-time", open);
    return () => window.removeEventListener("atlas-open-staff-time", open);
  }, []);
  const [meetingsWorkspace, setMeetingsWorkspace] = useState<string | null>(
    null,
  );
  useEffect(() => {
    const open = (event: Event) =>
      setMeetingsWorkspace(
        (event as CustomEvent).detail?.workspace || "school",
      );
    window.addEventListener("atlas-open-meetings", open);
    return () => window.removeEventListener("atlas-open-meetings", open);
  }, []);
  useEffect(() => {
    const open = (event: Event) =>
      setFormsWorkspace((event as CustomEvent).detail?.workspace || "school");
    window.addEventListener("atlas-open-forms", open);
    return () => window.removeEventListener("atlas-open-forms", open);
  }, []);
  const refreshSequence = useRef(0);
  useEffect(() => {
    setPasswordForm(false);
    setOldPassword("");
    setNewPassword("");
    setAccountError("");
    setInvitationForm(false);
    setRecoveryPanel(false);
    setOperationsPanel(false);
    setFormsWorkspace(null);
    setMeetingsWorkspace(null);
    setStaffTimeWorkspace(null);
    setDocumentsWorkspace(null);
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
      setLoginNotice("");
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
      await api("auth/logout", "school", "POST", {
        pushEndpointHash: await logoutPushFingerprint(),
      });
      await unsubscribeBrowser();
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
        {recoveryForm ? (
          <Suspense fallback={<p>Открываю восстановление доступа…</p>}>
            <RecoveryForm
              initialLogin={login}
              onCancel={() => setRecoveryForm(false)}
              onComplete={(name) => {
                ++refreshSequence.current;
                setLogin(name);
                setPassword("");
                setError("");
                setRecoveryForm(false);
                setLoginNotice(
                  "Пароль изменён. Войдите с новым паролем. Все прежние сеансы завершены.",
                );
                void refresh();
              }}
            />
          </Suspense>
        ) : registration ? (
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
            {loginNotice && <p role="status">{loginNotice}</p>}
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
                  setPassword("");
                  setLoginNotice("");
                  setRecoveryForm(true);
                }}
              >
                Забыли пароль?
              </button>
            </p>
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
          <button onClick={() => setRecoveryPanel(true)}>Резервные коды</button>
          <button onClick={() => setFormsWorkspace("school")}>
            Формы и опросы
          </button>
          <button onClick={() => setDocumentsWorkspace("school")}>
            Документы и архив
          </button>
          {["admin", "editor", "teacher", "staff"].includes(
            session.user.role,
          ) && (
            <button onClick={() => setStaffTimeWorkspace("school")}>
              Табель и отсутствия
            </button>
          )}
          {["admin", "editor", "teacher", "staff", "parent"].includes(
            session.user.role,
          ) && (
            <button onClick={() => setMeetingsWorkspace("school")}>
              Родительские встречи
            </button>
          )}
          {session.user.role === "admin" && (
            <button onClick={() => setOperationsPanel(true)}>
              Состояние сервера
            </button>
          )}
        </>
      )}
    </div>
  );
  return (
    <>
      {session.user?.role === "staff" ? (
        <Suspense
          fallback={
            <main className="auth-screen">Открываю рабочее место…</main>
          }
        >
          <ClassStaffWorkspace
            key={session.user.id + ":" + session.user.version}
            controls={controls}
          />
        </Suspense>
      ) : session.user && !["admin", "editor"].includes(session.user.role) ? (
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
      {recoveryPanel && session.user && (
        <Suspense
          fallback={
            <p className="auth-error" role="status">
              Открываю резервные коды…
            </p>
          }
        >
          <RecoveryPanel
            key={session.user.id + ":" + session.user.version}
            login={session.user.login}
            onClose={() => setRecoveryPanel(false)}
            onAuthCheck={refresh}
          />
        </Suspense>
      )}
      {operationsPanel && session.user?.role === "admin" && (
        <Suspense fallback={<p role="status">Открываю состояние сервера…</p>}>
          <OperationsPanel
            key={session.user.id + ":" + session.user.version}
            onClose={() => setOperationsPanel(false)}
          />
        </Suspense>
      )}
      {formsWorkspace && session.user && (
        <Suspense fallback={<p role="status">Открываю формы…</p>}>
          <SchoolForms
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={formsWorkspace}
            onClose={() => setFormsWorkspace(null)}
          />
        </Suspense>
      )}
      {documentsWorkspace && session.user && (
        <Suspense fallback={<p role="status">Открываю документы…</p>}>
          <SchoolDocuments
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={documentsWorkspace}
            onClose={() => setDocumentsWorkspace(null)}
          />
        </Suspense>
      )}
      {staffTimeWorkspace &&
        session.user &&
        ["admin", "editor", "teacher", "staff"].includes(session.user.role) && (
          <Suspense fallback={<p role="status">Открываю табель…</p>}>
            <StaffTime
              key={session.user.id + ":" + session.user.version}
              user={session.user}
              initialWorkspace={staffTimeWorkspace}
              onClose={() => setStaffTimeWorkspace(null)}
            />
          </Suspense>
        )}
      {meetingsWorkspace && session.user && (
        <Suspense
          fallback={<p role="status">Открываю родительские встречи…</p>}
        >
          <ParentMeetings
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={meetingsWorkspace}
            onClose={() => setMeetingsWorkspace(null)}
          />
        </Suspense>
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
            <p>
              После смены остальные сеансы этой учётной записи завершатся, а
              резервные коды будут отозваны. Новый набор можно создать после
              смены пароля.
            </p>
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

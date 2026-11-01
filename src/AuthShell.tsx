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
import { menuRoles } from "./assessmentCalendar";
import { sectionVisible } from "./schoolSections";
const App = lazy(() => import("./App"));
const OperationsPanel = lazy(() => import("./OperationsPanel"));
const SchoolSectionsPanel = lazy(() => import("./SchoolSectionsPanel"));
const SchoolForms = lazy(() => import("./SchoolForms"));
const ParentMeetings = lazy(() => import("./ParentMeetings"));
const StaffTime = lazy(() => import("./StaffTime"));
const TeacherWorkload = lazy(() => import("./TeacherWorkload"));
const SchoolLibrary = lazy(() => import("./SchoolLibrary"));
const LostFound = lazy(() => import("./LostFound"));
const BehaviourNotes = lazy(() => import("./BehaviourNotes"));
const AssessmentPanel = lazy(() => import("./AssessmentPanel"));
const Clubs = lazy(() => import("./Clubs"));
const SchoolSite = lazy(() => import("./SchoolSite"));
const Admissions = lazy(() => import("./AdmissionOffice"));
const SchoolPayments = lazy(() => import("./SchoolPayments"));
const Canteen = lazy(() => import("./Canteen"));
const Handover = lazy(() => import("./Handover"));
const LiveLessons = lazy(() => import("./LiveLessons"));
const LiveProjector = lazy(() => import("./LiveProjector"));
const HallScreens = lazy(() => import("./HallScreens"));
const SchoolDocuments = lazy(() => import("./SchoolDocuments"));
const StaffWorkspace = lazy(() => import("./StaffWorkspace"));
const Registers = lazy(() => import("./Registers"));
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
  // Optional sections of the menu; null until the setting is loaded, and then they stay out of sight.
  const [hiddenSections, setHiddenSections] = useState<string[] | null>(null),
    [sectionsPanel, setSectionsPanel] = useState(false);
  const [formsWorkspace, setFormsWorkspace] = useState<string | null>(null);
  const [liveWorkspace, setLiveWorkspace] = useState<string | null>(null);
  const [libraryWorkspace, setLibraryWorkspace] = useState<string | null>(null);
  const [canteenWorkspace, setCanteenWorkspace] = useState<string | null>(null);
  const [handoverWorkspace, setHandoverWorkspace] = useState<string | null>(
    null,
  );
  useEffect(() => {
    const open = (event: Event) =>
      setHandoverWorkspace(
        (event as CustomEvent).detail?.workspace || "school",
      );
    window.addEventListener("atlas-open-handover", open);
    return () => window.removeEventListener("atlas-open-handover", open);
  }, []);
  const [paymentsWorkspace, setPaymentsWorkspace] = useState<string | null>(
    null,
  );
  const [clubsWorkspace, setClubsWorkspace] = useState<string | null>(null);
  const [siteWorkspace, setSiteWorkspace] = useState<string | null>(null);
  const [admissionsWorkspace, setAdmissionsWorkspace] = useState<string | null>(
    null,
  );
  useEffect(() => {
    const open = (event: Event) =>
      setClubsWorkspace((event as CustomEvent).detail?.workspace || "school");
    window.addEventListener("atlas-open-clubs", open);
    return () => window.removeEventListener("atlas-open-clubs", open);
  }, []);
  const [lostFoundWorkspace, setLostFoundWorkspace] = useState<string | null>(
    null,
  );
  const [behaviourWorkspace, setBehaviourWorkspace] = useState<string | null>(
    null,
  );
  useEffect(() => {
    const open = (event: Event) =>
      setBehaviourWorkspace(
        (event as CustomEvent).detail?.workspace || "school",
      );
    window.addEventListener("atlas-open-behaviour", open);
    return () => window.removeEventListener("atlas-open-behaviour", open);
  }, []);
  const [assessmentsWorkspace, setAssessmentsWorkspace] = useState<
    string | null
  >(null);
  useEffect(() => {
    const open = (event: Event) =>
      setAssessmentsWorkspace(
        (event as CustomEvent).detail?.workspace || "school",
      );
    window.addEventListener("atlas-open-assessments", open);
    return () => window.removeEventListener("atlas-open-assessments", open);
  }, []);
  useEffect(() => {
    const open = (event: Event) =>
      setLostFoundWorkspace(
        (event as CustomEvent).detail?.workspace || "school",
      );
    window.addEventListener("atlas-open-lost-found", open);
    return () => window.removeEventListener("atlas-open-lost-found", open);
  }, []);
  useEffect(() => {
    const open = (event: Event) =>
      setLibraryWorkspace((event as CustomEvent).detail?.workspace || "school");
    window.addEventListener("atlas-open-library", open);
    return () => window.removeEventListener("atlas-open-library", open);
  }, []);
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
  const [workloadWorkspace, setWorkloadWorkspace] = useState<string | null>(
    null,
  );
  const [hallWorkspace, setHallWorkspace] = useState<string | null>(null);
  useEffect(() => {
    const open = (event: Event) =>
      setHallWorkspace((event as CustomEvent).detail?.workspace || "school");
    window.addEventListener("atlas-open-hall", open);
    return () => window.removeEventListener("atlas-open-hall", open);
  }, []);
  const [registersWorkspace, setRegistersWorkspace] = useState<string | null>(
    null,
  );
  useEffect(() => {
    const open = (event: Event) =>
      setRegistersWorkspace(
        (event as CustomEvent).detail?.workspace || "school",
      );
    window.addEventListener("atlas-open-registers", open);
    return () => window.removeEventListener("atlas-open-registers", open);
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
    if (!session?.user) {
      setHiddenSections(null);
      return;
    }
    let live = true;
    api("school-sections", "school")
      .then((r) => {
        if (live) setHiddenSections(r.hidden);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [session?.user?.id, session?.user?.version]);
  useEffect(() => {
    setPasswordForm(false);
    setOldPassword("");
    setNewPassword("");
    setAccountError("");
    setInvitationForm(false);
    setRecoveryPanel(false);
    setOperationsPanel(false);
    setSectionsPanel(false);
    setFormsWorkspace(null);
    setMeetingsWorkspace(null);
    setHallWorkspace(null);
    setStaffTimeWorkspace(null);
    setWorkloadWorkspace(null);
    setRegistersWorkspace(null);
    setDocumentsWorkspace(null);
    setLibraryWorkspace(null);
    setCanteenWorkspace(null);
    setHandoverWorkspace(null);
    setLiveWorkspace(null);
    setPaymentsWorkspace(null);
    setLostFoundWorkspace(null);
    setBehaviourWorkspace(null);
    setAssessmentsWorkspace(null);
    setClubsWorkspace(null);
    setSiteWorkspace(null);
    setAdmissionsWorkspace(null);
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
  const projection = new URLSearchParams(window.location.search);
  if (projection.has("projectLesson"))
    return (
      <Suspense fallback={<p>Загрузка экрана занятия…</p>}>
        <LiveProjector
          key={`${session.user?.id}:${session.user?.version}`}
          lesson={projection.get("projectLesson") || ""}
          workspace={projection.get("workspace") || "school"}
        />
      </Suspense>
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
          {["admin", "teacher", "student", "parent"].includes(
            session.user.role,
          ) && (
            <button onClick={() => setLiveWorkspace("school")}>
              Интерактивные занятия
            </button>
          )}
          {sectionVisible(hiddenSections, "library") && (
            <button onClick={() => setLibraryWorkspace("school")}>
              Библиотека и учебники
            </button>
          )}
          {sectionVisible(hiddenSections, "lost_found") && (
            <button onClick={() => setLostFoundWorkspace("school")}>
              Потерянные вещи
            </button>
          )}
          {session.user.role !== "editor" && (
            <button onClick={() => setBehaviourWorkspace("school")}>
              Похвалы и замечания
            </button>
          )}
          {menuRoles.includes(session.user.role) && (
            <button onClick={() => setAssessmentsWorkspace("school")}>
              Календарь контрольных
            </button>
          )}
          <button onClick={() => setClubsWorkspace("school")}>Кружки</button>
          {sectionVisible(hiddenSections, "canteen") && (
            <button onClick={() => setCanteenWorkspace("school")}>
              Столовая
            </button>
          )}
          {session.user.role !== "student" && (
            <button onClick={() => setHandoverWorkspace("school")}>
              Уход детей
            </button>
          )}
          {session.user.role !== "student" &&
            sectionVisible(hiddenSections, "payments") && (
              <button onClick={() => setPaymentsWorkspace("school")}>
                Платежи
              </button>
            )}
          {["admin", "editor", "teacher", "staff"].includes(
            session.user.role,
          ) && (
            <>
              {sectionVisible(hiddenSections, "admissions") && (
                <button onClick={() => setAdmissionsWorkspace("school")}>
                  Приём в школу
                </button>
              )}
              {sectionVisible(hiddenSections, "site") && (
                <button onClick={() => setSiteWorkspace("school")}>
                  Сайт школы
                </button>
              )}
            </>
          )}
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
          {["admin", "teacher"].includes(session.user.role) && (
            <button onClick={() => setWorkloadWorkspace("school")}>
              {session.user.role === "admin"
                ? "Нагрузка учителей"
                : "Моя нагрузка"}
            </button>
          )}
          {["admin", "editor", "teacher", "staff", "parent"].includes(
            session.user.role,
          ) && (
            <button onClick={() => setRegistersWorkspace("school")}>
              {session.user.role === "parent"
                ? "Сообщения о происшествиях"
                : "Журналы регистрации"}
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
            <>
              <button onClick={() => setHallWorkspace("school")}>
                Информационные экраны
              </button>
              <button onClick={() => setSectionsPanel(true)}>
                Разделы школы
              </button>
              <button onClick={() => setOperationsPanel(true)}>
                Состояние сервера
              </button>
            </>
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
          <StaffWorkspace
            key={session.user.id + ":" + session.user.version}
            session={session}
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
      {sectionsPanel && session.user?.role === "admin" && (
        <Suspense fallback={<p role="status">Открываю разделы школы…</p>}>
          <SchoolSectionsPanel
            key={session.user.id + ":" + session.user.version}
            onClose={() => setSectionsPanel(false)}
            onChanged={setHiddenSections}
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
      {libraryWorkspace && session.user && (
        <Suspense fallback={<p role="status">Открываю библиотеку…</p>}>
          <SchoolLibrary
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={libraryWorkspace}
            onClose={() => setLibraryWorkspace(null)}
          />
        </Suspense>
      )}
      {paymentsWorkspace && session.user && (
        <Suspense fallback={<p>Открываем платежи…</p>}>
          <SchoolPayments
            key={`${session.user.id}:${session.user.version}`}
            user={session.user}
            initialWorkspace={paymentsWorkspace}
            onClose={() => setPaymentsWorkspace(null)}
          />
        </Suspense>
      )}
      {handoverWorkspace && session.user && session.user.role !== "student" && (
        <Suspense fallback={<p role="status">Открываем уход детей…</p>}>
          <Handover
            key={`${session.user.id}:${session.user.version}`}
            user={session.user}
            initialWorkspace={handoverWorkspace}
            onClose={() => setHandoverWorkspace(null)}
          />
        </Suspense>
      )}
      {liveWorkspace && session.user && (
        <Suspense fallback={<p role="status">Открываем занятия…</p>}>
          <LiveLessons
            key={`${session.user.id}:${session.user.version}`}
            user={session.user}
            initialWorkspace={liveWorkspace}
            onClose={() => setLiveWorkspace(null)}
          />
        </Suspense>
      )}
      {canteenWorkspace && session.user && (
        <Suspense fallback={<p role="status">Открываю столовую…</p>}>
          <Canteen
            key={`${session.user.id}:${session.user.version}`}
            user={session.user}
            initialWorkspace={canteenWorkspace}
            onClose={() => setCanteenWorkspace(null)}
          />
        </Suspense>
      )}
      {admissionsWorkspace && session.user && (
        <Suspense fallback={<p role="status">Открываю приёмную комиссию…</p>}>
          <Admissions
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={admissionsWorkspace}
            onClose={() => setAdmissionsWorkspace(null)}
          />
        </Suspense>
      )}
      {siteWorkspace && session.user && (
        <Suspense fallback={<div role="status">Открываю редактор сайта…</div>}>
          <SchoolSite
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={siteWorkspace}
            onClose={() => setSiteWorkspace(null)}
          />
        </Suspense>
      )}
      {clubsWorkspace && session.user && (
        <Suspense fallback={<p role="status">Открываю кружки…</p>}>
          <Clubs
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={clubsWorkspace}
            onClose={() => setClubsWorkspace(null)}
          />
        </Suspense>
      )}
      {lostFoundWorkspace && session.user && (
        <Suspense fallback={<p role="status">Открываю потерянные вещи…</p>}>
          <LostFound
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={lostFoundWorkspace}
            onClose={() => setLostFoundWorkspace(null)}
          />
        </Suspense>
      )}
      {behaviourWorkspace && session.user && (
        <Suspense fallback={<p role="status">Открываю похвалы и замечания…</p>}>
          <BehaviourNotes
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={behaviourWorkspace}
            onClose={() => setBehaviourWorkspace(null)}
          />
        </Suspense>
      )}
      {assessmentsWorkspace && session.user && (
        <Suspense
          fallback={<p role="status">Открываю календарь контрольных…</p>}
        >
          <AssessmentPanel
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={assessmentsWorkspace}
            onClose={() => setAssessmentsWorkspace(null)}
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
      {workloadWorkspace &&
        session.user &&
        ["admin", "teacher"].includes(session.user.role) && (
          <Suspense fallback={<p role="status">Открываю нагрузку…</p>}>
            <TeacherWorkload
              key={session.user.id + ":" + session.user.version}
              user={session.user}
              initialWorkspace={workloadWorkspace}
              onClose={() => setWorkloadWorkspace(null)}
            />
          </Suspense>
        )}
      {registersWorkspace &&
        session.user &&
        ["admin", "editor", "teacher", "staff", "parent"].includes(
          session.user.role,
        ) && (
          <Suspense fallback={<p role="status">Открываю журналы…</p>}>
            <Registers
              key={session.user.id + ":" + session.user.version}
              user={session.user}
              initialWorkspace={registersWorkspace}
              onClose={() => setRegistersWorkspace(null)}
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
      {hallWorkspace && session.user?.role === "admin" && (
        <Suspense
          fallback={<p role="status">Открываю информационные экраны…</p>}
        >
          <HallScreens
            key={session.user.id + ":" + session.user.version}
            user={session.user}
            initialWorkspace={hallWorkspace}
            onClose={() => setHallWorkspace(null)}
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

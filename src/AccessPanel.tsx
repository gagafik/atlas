import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import InvitationPanel from "./InvitationPanel";
import FamilyPanel from "./FamilyPanel";
import ClassStaffPanel from "./ClassStaffPanel";
import AccessUserForm from "./AccessUserForm";
import AccessUsersTable from "./AccessUsersTable";
import AccessAuditTable from "./AccessAuditTable";
import "./classStaff.css";
import "./AccessPanel.css";
import type { State } from "./types";
import {
  type Account,
  type Role,
  type AuthStatus,
  type Grant,
  type AccessView,
} from "./auth";
import {
  AUDIT_PAGE,
  USERS_PAGE,
  accessTabs,
  accountFormDirty,
  auditMatches,
  blankForm,
  filterAccounts,
  pageOf,
  roleOptions,
  savedRowPlace,
  stateCounts,
  viewForRole,
  type AccessForm as Form,
  type AccessTab,
  type AuditEntry as Audit,
  type RoleFilter,
  type StateFilter,
} from "./accessModel";

const leaveQuestion =
  "В учётной записи есть несохранённые изменения. Уйти без сохранения?";
const stateFilters: { id: StateFilter; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "active", label: "Активные" },
  { id: "inactive", label: "Отключённые" },
];
export default function AccessPanel({
  session,
  state,
  workspace,
  onAuthChange,
}: {
  session: AuthStatus;
  state: State;
  workspace: string;
  onAuthChange: () => Promise<void>;
}) {
  const [users, setUsers] = useState<Account[]>([]),
    [audit, setAudit] = useState<Audit[]>([]),
    [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState({ name: "", login: "", password: "" }),
    [repeat, setRepeat] = useState("");
  const [grantSpace, setGrantSpace] = useState<"school" | "demo">(
      workspace as "school" | "demo",
    ),
    [grantView, setGrantView] = useState<AccessView>("class"),
    [targets, setTargets] = useState<string[]>([]);
  const [grantData, setGrantData] = useState<State | null>(null),
    [query, setQuery] = useState("");
  // The form as it was opened, to tell whether it holds changes.
  const [formBase, setFormBase] = useState<Form | null>(null);
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all"),
    [stateFilter, setStateFilter] = useState<StateFilter>("all"),
    [shown, setShown] = useState(USERS_PAGE),
    [highlightId, setHighlightId] = useState<string | null>(null);
  const [auditQuery, setAuditQuery] = useState(""),
    [auditShown, setAuditShown] = useState(AUDIT_PAGE);
  // The button that gets the focus when the list comes back, and the accounts as the last reload brought them.
  const returnFocus = useRef<string | null>(null),
    loaded = useRef<Account[]>([]);
  const [tab, setTab] = useState<AccessTab>("users"),
    [visited, setVisited] = useState<AccessTab[]>(["users"]);
  const openTab = (id: AccessTab) => {
    setTab(id);
    setVisited((v) => (v.includes(id) ? v : [...v, id]));
  };
  async function reload() {
    const [u, a] = await Promise.all([
      api("access/users", workspace),
      api("access/audit", workspace),
    ]);
    loaded.current = u;
    setUsers(u);
    setAudit(a);
  }
  useEffect(() => {
    let alive = true;
    if (session.enabled)
      Promise.all([
        api("access/users", workspace),
        api("access/audit", workspace),
      ])
        .then(([u, a]) => {
          if (alive) {
            loaded.current = u;
            setUsers(u);
            setAudit(a);
          }
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    return () => {
      alive = false;
    };
  }, [session.enabled, workspace]);
  useEffect(() => {
    let live = true;
    setGrantData(null);
    setTargets([]);
    if (grantSpace === workspace) {
      setGrantData(state);
    } else if (session.enabled) {
      api("state", grantSpace)
        .then((s) => {
          if (live) setGrantData(s);
        })
        .catch((e) => {
          if (live) setError(e.message);
        });
    }
    return () => {
      live = false;
    };
  }, [grantSpace, workspace, state.revision, session.enabled]);
  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const allowedViews: AccessView[] =
    form?.role === "teacher"
      ? ["teacher"]
      : form?.role === "student" || form?.role === "parent"
        ? ["student"]
        : ["class", "teacher", "room", "group", "student"];
  const roles = roleOptions(users),
    roleValue =
      roleFilter === "all" || roles.some((o) => o.role === roleFilter)
        ? roleFilter
        : "all",
    counts = stateCounts(users),
    filtered = filterAccounts(users, {
      role: roleValue,
      state: stateFilter,
      query,
    }),
    page = pageOf(filtered, shown),
    auditPage = pageOf(
      audit.filter((a) => auditMatches(a, auditQuery)),
      auditShown,
    );
  const resetShown = () => {
    setShown(USERS_PAGE);
    setHighlightId(null);
  };
  const dirty = accountFormDirty(form, formBase, repeat, targets);
  // The page asks before the form with changes is lost by leaving the screen, the workspace or the program.
  useEffect(() => {
    if (!dirty) return;
    const leave = (e: Event) => {
      if (!window.confirm(leaveQuestion)) e.preventDefault();
    };
    const unload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("atlas-homework-leave", leave);
    window.addEventListener("beforeunload", unload);
    return () => {
      window.removeEventListener("atlas-homework-leave", leave);
      window.removeEventListener("beforeunload", unload);
    };
  }, [dirty]);
  // The list comes back: the button that opened the form (or the saved account's button) gets the focus.
  useEffect(() => {
    if (form || busy || !returnFocus.current) return;
    const el = document.getElementById(returnFocus.current);
    returnFocus.current = null;
    el?.focus({ preventScroll: true });
    el?.scrollIntoView?.({ block: "nearest" });
  }, [form, busy]);
  const table =
    grantData &&
    {
      class: grantData.classes,
      teacher: grantData.teachers,
      room: grantData.rooms,
      group: grantData.groups,
      student: grantData.students,
    }[grantView];
  // Opens the form in place of the list; asks first when another form with changes is open.
  function openForm(next: Form, view: AccessView) {
    if (!leaveForm()) return;
    setHighlightId(null);
    setForm(next);
    setFormBase(next);
    setRepeat("");
    setTargets([]);
    setGrantView(view);
  }
  function leaveForm() {
    return !dirty || window.confirm(leaveQuestion);
  }
  function closeForm() {
    if (!leaveForm()) return;
    setForm(null);
    setFormBase(null);
    setRepeat("");
  }
  async function editGrant(g: Grant) {
    await run(async () => {
      setGrantSpace(g.workspace);
      setGrantView(g.view);
      const fresh = await api("state", g.workspace);
      setGrantData(fresh);
      setTargets(g.entityIds);
    });
  }
  function role(value: Role) {
    if (!form) return;
    setForm({ ...form, role: value, grants: [] });
    setTargets([]);
    setGrantView(viewForRole(value));
  }
  function addGrant() {
    if (!form || !targets.length) return;
    const grant: Grant = {
      workspace: grantSpace,
      view: grantView,
      entityIds: targets,
    };
    setForm({
      ...form,
      grants: [
        ...form.grants.filter(
          (g) => g.workspace !== grantSpace || g.view !== grantView,
        ),
        grant,
      ],
    });
    setTargets([]);
  }
  async function save() {
    if (targets.length)
      throw new Error(
        "Сначала добавьте выбранные записи в область доступа или очистите выбор.",
      );
    if (!form) return;
    const { id, password, ...rest } = form;
    await api(
      id ? `access/users/${id}` : "access/users",
      workspace,
      id ? "PUT" : "POST",
      { ...rest, password: password || null },
    );
    setForm(null);
    setFormBase(null);
    setRepeat("");
    await reload();
    await onAuthChange();
    // The saved row must be visible; when the filters hide it, the notice says so instead of changing them.
    const visible = filterAccounts(loaded.current, {
      role: roleValue,
      state: stateFilter,
      query,
    });
    const place = savedRowPlace(visible, form.login, shown);
    let hidden = "";
    if (place.index < 0) hidden = " Выбранные фильтры её не показывают.";
    else {
      setShown(place.shown);
      setHighlightId(visible[place.index].id);
      returnFocus.current = `access-edit-${visible[place.index].id}`;
    }
    setNotice(
      "Учётная запись сохранена. Её прежние сеансы завершены." + hidden,
    );
  }
  return (
    <div className="access-panel dated-panel">
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="info-line" role="status">
          {notice}
        </p>
      )}
      {!session.enabled ? (
        <form
          className="dated-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              if (setup.password !== repeat)
                throw new Error("Пароли не совпадают.");
              await api("auth/setup", workspace, "POST", setup);
              setSetup({ name: "", login: "", password: "" });
              setRepeat("");
              await onAuthChange();
            });
          }}
        >
          <h3>Включить вход по учётным записям</h3>
          <p>
            Создайте первого администратора. После включения доступ к рабочей
            базе потребует входа; расписание и история сохранятся. Публичные
            ссылки останутся доступны своим получателям.
          </p>
          <label className="field">
            <span>Имя администратора</span>
            <input
              required
              maxLength={160}
              value={setup.name}
              onChange={(e) => setSetup({ ...setup, name: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Логин администратора</span>
            <input
              required
              pattern={"[a-z0-9][a-z0-9._\\-]{2,63}"}
              autoComplete="username"
              value={setup.login}
              onChange={(e) =>
                setSetup({ ...setup, login: e.target.value.toLowerCase() })
              }
            />
            <small>
              3–64 символа: латинские буквы, цифры, точка, дефис, подчёркивание.
            </small>
          </label>
          <label className="field">
            <span>Пароль администратора</span>
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
              value={setup.password}
              onChange={(e) => setSetup({ ...setup, password: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Повторите пароль</span>
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
            />
          </label>
          <button className="primary" disabled={busy || !session.canSetup}>
            Создать администратора и включить вход
          </button>
        </form>
      ) : (
        <>
          <div
            className="segmented access-tabs"
            role="group"
            aria-label="Разделы"
          >
            {accessTabs.map((t) => (
              <button
                key={t.id}
                type="button"
                className={tab === t.id ? "selected" : ""}
                aria-pressed={tab === t.id}
                onClick={() => openTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="access-tab" hidden={tab !== "invitations"}>
            <InvitationPanel
              key={workspace}
              state={state}
              workspace={workspace}
              onChange={reload}
            />
          </div>
          {visited.includes("families") && (
            <div className="access-tab" hidden={tab !== "families"}>
              <FamilyPanel
                key={`family:${workspace}`}
                workspace={workspace}
                state={state}
                users={users}
                onChanged={reload}
                bare
              />
            </div>
          )}
          {visited.includes("classStaff") && (
            <div className="access-tab" hidden={tab !== "classStaff"}>
              <ClassStaffPanel
                key={`class-staff:${workspace}`}
                workspace={workspace}
                bare
              />
            </div>
          )}
          <div className="access-tab dated-panel" hidden={tab !== "users"}>
            <div className="access-browse dated-panel" hidden={!!form}>
              <div className="access-toolbar">
                <label>
                  <span>Поиск пользователей</span>
                  <input
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      resetShown();
                    }}
                    placeholder="Имя или логин"
                  />
                </label>
                <label>
                  <span>Роль</span>
                  <select
                    value={roleValue}
                    onChange={(e) => {
                      setRoleFilter(e.target.value as RoleFilter);
                      resetShown();
                    }}
                  >
                    <option value="all">Все ({users.length})</option>
                    {roles.map((o) => (
                      <option key={o.role} value={o.role}>
                        {o.label} ({o.count})
                      </option>
                    ))}
                  </select>
                </label>
                <div
                  className="segmented"
                  role="group"
                  aria-label="Состояние учётной записи"
                >
                  {stateFilters.map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      className={stateFilter === f.id ? "selected" : ""}
                      aria-pressed={stateFilter === f.id}
                      onClick={() => {
                        setStateFilter(f.id);
                        resetShown();
                      }}
                    >
                      {f.label} <span>{counts[f.id]}</span>
                    </button>
                  ))}
                </div>
                <button
                  className="access-reload"
                  disabled={busy}
                  onClick={() => void run(reload)}
                >
                  Обновить список
                </button>
                <button
                  id="access-add"
                  className="primary"
                  disabled={busy}
                  onClick={() => {
                    returnFocus.current = "access-add";
                    openForm(blankForm(workspace), "class");
                  }}
                >
                  ＋ Добавить учётную запись
                </button>
              </div>
              <details className="access-roles">
                <summary>Что может каждая роль</summary>
                <p>
                  Администратор управляет доступом и всей базой. Составитель
                  редактирует выбранные пространства. Сотруднику отдельно
                  назначаются функции по классам. Остальные роли видят только
                  назначенные расписания текущей рабочей версии. Пароли хранятся
                  в виде защищённых проверочных значений.
                </p>
              </details>
              <AccessUsersTable
                rows={page.rows}
                busy={busy}
                highlightId={highlightId}
                onEdit={(u) => {
                  returnFocus.current = `access-edit-${u.id}`;
                  openForm({ ...u, password: "" }, viewForRole(u.role));
                }}
              >
                {!!page.total && (
                  <div className="access-more">
                    <span>
                      Показано {page.shown} из {page.total}
                    </span>
                    {page.more && (
                      <button onClick={() => setShown((n) => n + USERS_PAGE)}>
                        Показать ещё
                      </button>
                    )}
                  </div>
                )}
              </AccessUsersTable>
            </div>
            {form && (
              <AccessUserForm
                key={form.id ?? "new"}
                form={form}
                setForm={setForm}
                repeat={repeat}
                onRepeat={setRepeat}
                busy={busy}
                error={error}
                grantSpace={grantSpace}
                onGrantSpace={setGrantSpace}
                grantView={grantView}
                onGrantView={setGrantView}
                targets={targets}
                onTargets={setTargets}
                table={table}
                allowedViews={allowedViews}
                onRole={role}
                onAddGrant={addGrant}
                onEditGrant={editGrant}
                onRemoveGrant={(i) =>
                  setForm({
                    ...form,
                    grants: form.grants.filter((_, n) => n !== i),
                  })
                }
                onSubmit={() =>
                  void run(async () => {
                    if (form.password && form.password !== repeat)
                      throw new Error("Пароли не совпадают.");
                    await save();
                  })
                }
                onBack={closeForm}
              />
            )}
          </div>
          <section
            className="access-tab dated-panel"
            hidden={tab !== "audit"}
            aria-label="Журнал действий"
          >
            <div className="access-toolbar access-audit-toolbar">
              <label>
                <span>Поиск в журнале</span>
                <input
                  value={auditQuery}
                  onChange={(e) => {
                    setAuditQuery(e.target.value);
                    setAuditShown(AUDIT_PAGE);
                  }}
                  placeholder="Кто или что сделал"
                />
              </label>
              <button disabled={busy} onClick={() => void run(reload)}>
                Обновить
              </button>
              <p className="muted access-audit-note">
                Сохранения расписания, отмена/повтор и управление доступом.
                Последние 10 000 записей хранятся отдельно от школьного
                расписания.
              </p>
            </div>
            <AccessAuditTable rows={auditPage.rows}>
              <div className="access-more">
                <span>
                  Показано {auditPage.shown} из {auditPage.total} загруженных
                </span>
                {auditPage.more && (
                  <button onClick={() => setAuditShown((n) => n + AUDIT_PAGE)}>
                    Показать ещё
                  </button>
                )}
                {!auditPage.more && !!audit.length && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        const next = await api(
                          `access/audit?before=${audit[audit.length - 1].id}`,
                          workspace,
                        );
                        setAudit([...audit, ...next]);
                        setAuditShown((n) => n + AUDIT_PAGE);
                        if (!next.length)
                          setNotice("Более ранних записей нет.");
                      })
                    }
                  >
                    Показать более ранние
                  </button>
                )}
              </div>
            </AccessAuditTable>
          </section>
        </>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { api } from "./api";
import { Multi } from "./DatedSchedule";
import InvitationPanel from "./InvitationPanel";
import FamilyPanel from "./FamilyPanel";
import type { State } from "./types";
import {
  roleNames,
  type Account,
  type Role,
  type AuthStatus,
  type Grant,
  type AccessView,
} from "./auth";
const viewNames: Record<AccessView, string> = {
  class: "Классы",
  teacher: "Учителя",
  room: "Кабинеты",
  group: "Группы",
  student: "Ученики",
};
type Form = Omit<Account, "id"> & { id?: string; password: string };
interface Audit {
  id: number;
  created: string;
  actorName: string;
  action: string;
  workspace: string | null;
  revision: number | null;
  detail: string;
}
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
  async function reload() {
    const [u, a] = await Promise.all([
      api("access/users", workspace),
      api("access/audit", workspace),
    ]);
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
  const table =
    grantData &&
    {
      class: grantData.classes,
      teacher: grantData.teachers,
      room: grantData.rooms,
      group: grantData.groups,
      student: grantData.students,
    }[grantView];
  function role(value: Role) {
    if (!form) return;
    setForm({ ...form, role: value, grants: [] });
    setTargets([]);
    setGrantView(
      value === "teacher"
        ? "teacher"
        : ["student", "parent"].includes(value)
          ? "student"
          : "class",
    );
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
    setRepeat("");
    await reload();
    await onAuthChange();
    setNotice("Учётная запись сохранена. Её прежние сеансы завершены.");
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
          <InvitationPanel
            key={workspace}
            state={state}
            workspace={workspace}
            onChange={reload}
          />
          <FamilyPanel
            key={`family:${workspace}`}
            workspace={workspace}
            state={state}
            users={users}
            onChanged={reload}
          />
          <p className="info-line">
            Администратор управляет доступом и всей базой. Составитель
            редактирует выбранные пространства. Остальные роли видят только
            назначенные расписания текущей рабочей версии. Пароли хранятся в
            виде защищённых проверочных значений.
          </p>
          <div className="dated-toolbar">
            <button
              className="primary"
              disabled={busy}
              onClick={() => {
                setForm({
                  name: "",
                  login: "",
                  role: "editor",
                  active: true,
                  version: 0,
                  workspaces: [workspace],
                  grants: [],
                  password: "",
                });
                setRepeat("");
                setTargets([]);
                setGrantView("class");
              }}
            >
              Добавить учётную запись
            </button>
            <button disabled={busy} onClick={() => void run(reload)}>
              Обновить список
            </button>
          </div>
          {form && (
            <form
              className="dated-form"
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  if (form.password && form.password !== repeat)
                    throw new Error("Пароли не совпадают.");
                  await save();
                });
              }}
            >
              <h3>
                {form.id ? "Изменить учётную запись" : "Новая учётная запись"}
              </h3>
              <fieldset className="access-fields" disabled={busy}>
                <div className="dated-fields">
                  <label className="field">
                    <span>Имя пользователя</span>
                    <input
                      required
                      maxLength={160}
                      value={form.name}
                      onChange={(e) =>
                        setForm({ ...form, name: e.target.value })
                      }
                    />
                  </label>
                  <label className="field">
                    <span>Логин пользователя</span>
                    <input
                      autoComplete="off"
                      required
                      pattern={"[a-z0-9][a-z0-9._\\-]{2,63}"}
                      value={form.login}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          login: e.target.value.toLowerCase(),
                        })
                      }
                    />
                  </label>
                  <label className="field">
                    <span>Роль</span>
                    <select
                      value={form.role}
                      onChange={(e) => role(e.target.value as Role)}
                    >
                      {Object.entries(roleNames).map(([id, name]) => (
                        <option key={id} value={id}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={form.active}
                    onChange={(e) =>
                      setForm({ ...form, active: e.target.checked })
                    }
                  />
                  Учётная запись активна
                </label>
                <div className="dated-fields">
                  <label className="field">
                    <span>
                      {form.id
                        ? "Новый пароль (необязательно)"
                        : "Пароль пользователя"}
                    </span>
                    <input
                      type="password"
                      autoComplete="new-password"
                      required={!form.id}
                      minLength={12}
                      maxLength={128}
                      value={form.password}
                      onChange={(e) =>
                        setForm({ ...form, password: e.target.value })
                      }
                    />
                    <small>
                      От 12 символов. При редактировании пустое поле сохраняет
                      текущий пароль.
                    </small>
                  </label>
                  <label className="field">
                    <span>Повторите пароль</span>
                    <input
                      type="password"
                      autoComplete="new-password"
                      required={!!form.password}
                      minLength={12}
                      maxLength={128}
                      value={repeat}
                      onChange={(e) => setRepeat(e.target.value)}
                    />
                  </label>
                </div>
                {form.role === "admin" ? (
                  <p>
                    Администратору доступны школа, пример и управление
                    пользователями.
                  </p>
                ) : form.role === "editor" ? (
                  <Multi
                    label="Разрешённые пространства"
                    items={[
                      { id: "school", name: "Школа" },
                      { id: "demo", name: "Пример" },
                    ]}
                    values={form.workspaces}
                    onChange={(v) => setForm({ ...form, workspaces: v })}
                  />
                ) : (
                  <section>
                    <h4>Разрешённые расписания</h4>
                    <p>
                      Добавьте одну или несколько областей. Выбранные записи
                      нужно включить кнопкой «Добавить область доступа».
                    </p>
                    <div className="dated-fields">
                      <label className="field">
                        <span>Пространство доступа</span>
                        <select
                          value={grantSpace}
                          onChange={(e) =>
                            setGrantSpace(e.target.value as "school" | "demo")
                          }
                        >
                          <option value="school">Школа</option>
                          <option value="demo">Пример</option>
                        </select>
                      </label>
                      <label className="field">
                        <span>Вид доступа</span>
                        <select
                          value={grantView}
                          onChange={(e) => {
                            setGrantView(e.target.value as AccessView);
                            setTargets([]);
                          }}
                        >
                          {allowedViews.map((v) => (
                            <option key={v} value={v}>
                              {viewNames[v]}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    {table ? (
                      <Multi
                        label="Записи для доступа"
                        items={table}
                        values={targets}
                        onChange={setTargets}
                      />
                    ) : (
                      <p>Загружаю справочник…</p>
                    )}
                    <button
                      type="button"
                      disabled={
                        !targets.length ||
                        targets.length > 200 ||
                        (form.role === "student" && targets.length !== 1)
                      }
                      onClick={addGrant}
                    >
                      Добавить область доступа
                    </button>
                    {form.grants.map((g, i) => (
                      <div className="access-grant" key={g.workspace + g.view}>
                        <span>
                          {g.workspace === "school" ? "Школа" : "Пример"} ·{" "}
                          {viewNames[g.view]} · записей {g.entityIds.length}
                        </span>
                        <button
                          type="button"
                          onClick={() =>
                            void run(async () => {
                              setGrantSpace(g.workspace);
                              setGrantView(g.view);
                              const fresh = await api("state", g.workspace);
                              setGrantData(fresh);
                              setTargets(g.entityIds);
                            })
                          }
                        >
                          Посмотреть и изменить
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setForm({
                              ...form,
                              grants: form.grants.filter((_, n) => n !== i),
                            });
                          }}
                        >
                          Удалить область
                        </button>
                      </div>
                    ))}
                  </section>
                )}
                <p className="muted">
                  Изменение доступа завершает все сеансы этой записи. Последнего
                  действующего администратора нельзя отключить.
                </p>
                <div className="dated-toolbar">
                  <button
                    type="button"
                    onClick={() => {
                      setForm(null);
                      setRepeat("");
                    }}
                  >
                    Закрыть форму
                  </button>
                  <button className="primary">Сохранить учётную запись</button>
                </div>
              </fieldset>
            </form>
          )}
          <label className="field">
            <span>Поиск пользователей</span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Имя или логин"
            />
          </label>
          <div className="access-users">
            {users
              .filter((u) =>
                (u.name + " " + u.login)
                  .toLocaleLowerCase()
                  .includes(query.toLocaleLowerCase()),
              )
              .map((u) => (
                <article className="export-card" key={u.id}>
                  <h4>{u.name}</h4>
                  <p>
                    {u.login} · {roleNames[u.role]} ·{" "}
                    {u.active ? "Активна" : "Отключена"}
                  </p>
                  <p className="muted">
                    {u.role === "admin"
                      ? "Все пространства"
                      : u.role === "editor"
                        ? u.workspaces
                            .map((w) => (w === "school" ? "Школа" : "Пример"))
                            .join(", ")
                        : `Областей доступа: ${u.grants.length}`}
                  </p>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setForm({ ...u, password: "" });
                      setRepeat("");
                      setTargets([]);
                      setGrantView(
                        u.role === "teacher"
                          ? "teacher"
                          : ["student", "parent"].includes(u.role)
                            ? "student"
                            : "class",
                      );
                    }}
                  >
                    Редактировать доступ
                  </button>
                </article>
              ))}
          </div>
          <section className="access-audit">
            <h3>Журнал действий</h3>
            <p className="muted">
              Сохранения расписания, отмена/повтор и управление доступом.
              Последние 10 000 записей хранятся отдельно от школьного
              расписания.
            </p>
            {audit.map((a) => (
              <article key={a.id}>
                <strong>{a.actorName}</strong>
                <span>{new Date(a.created).toLocaleString("ru")}</span>
                <p>
                  {(
                    {
                      "state.saved": "Сохранено расписание",
                      "absence.submitted": "Подана объяснительная",
                      "absence.approved": "Принята объяснительная",
                      "absence.rejected": "Отклонена объяснительная",
                      "absence.revoked": "Отменено решение по объяснительной",
                      "absence.withdrawn": "Родитель отозвал объяснительную",
                      "absence.read": "Просмотрена причина отсутствия",
                      "attendance.summary_read":
                        "Просмотрена сводная посещаемость",
                      "attendance.details_read":
                        "Просмотрена посещаемость уроков ученика",
                      "attendance.exported": "Выгружена посещаемость",
                      "journal.coverage_read": "Проверено заполнение журналов",
                      "journal.period_created": "Создан учебный период",
                      "journal.period_updated": "Изменён учебный период",
                      "journal.period_closed": "Журналы периода закрыты",
                      "journal.period_reopened":
                        "Период открыт для исправлений",
                      "journal.period_archive_read":
                        "Просмотрен архив журналов",
                      "journal.coverage_exported":
                        "Выгружена проверка журналов",
                      "absence.bulk_preview":
                        "Подготовлено общее решение по объяснительным",
                      "absence.bulk_applied":
                        "Сохранено общее решение по объяснительным",
                      "absence.history_read":
                        "Просмотрена история объяснительной",
                      "absence.file_read": "Скачан файл объяснительной",
                      "absence.file_uploaded": "Загружен файл объяснительной",
                      "absence.reviewers_updated":
                        "Назначены ответственные за объяснительные",
                      "family.updated":
                        "Изменены семейная связь и доступ к ребёнку",
                      "family.history_read":
                        "Просмотрена история семейной связи",
                      "family.evidence_read":
                        "Просмотрены основания семейной связи",
                      "message.reported": "Отправлена жалоба на сообщение",
                      "message.evidence_read": "Просмотрены материалы жалобы",
                      "message.evidence_file_read": "Скачано вложение жалобы",
                      "message.moderation_hide": "Сообщение скрыто по жалобе",
                      "message.moderation_restore": "Сообщение восстановлено",
                      "message.moderation_dismiss": "Жалоба отклонена",
                      "message.manager_recovered":
                        "Восстановлено управление обсуждением",
                      "state.undo": "Отмена изменения",
                      "state.redo": "Повтор изменения",
                      "journal.draft_saved":
                        "Сохранён черновик учебного журнала",
                      "journal.final_scale": "Изменена шкала итоговых оценок",
                      "journal.final_prepared":
                        "Подготовлена итоговая ведомость",
                      "journal.final_assign": "Назначен ответственный за итоги",
                      "journal.final_saved": "Сохранён черновик итога",
                      "journal.final_submitted":
                        "Итог отправлен на утверждение",
                      "journal.final_approve": "Утверждён итог",
                      "journal.final_return": "Итог возвращён на исправление",
                      "journal.final_publish": "Опубликован итог",
                      "journal.final_reopen": "Открыто исправление итога",
                      "journal.final_withdraw": "Отозван итог",
                      "journal.published":
                        "Опубликована запись учебного журнала",
                      "journal.withdrawn": "Отозвана запись учебного журнала",
                      "courses.intake_configured":
                        "Изменены условия приёма заявок на курсы",
                      "courses.preference_submitted":
                        "Получены предпочтения по курсам",
                      "courses.preference_returned":
                        "Заявка возвращена для уточнения",
                      "courses.preference_accepted":
                        "Предпочтения перенесены в план заявок",
                      "access.enabled": "Включён вход",
                      "account.created": "Создана учётная запись",
                      "account.updated": "Изменён доступ",
                      "account.password_changed": "Сменён пароль",
                      "account.password_reset_locally":
                        "Пароль сброшен на сервере",
                      "account.signed_in": "Вход в систему",
                      "account.signed_out": "Выход из системы",
                      "account.login_failed": "Неудачная попытка входа",
                    } as Record<string, string>
                  )[a.action] || a.action}
                  {a.workspace
                    ? ` · ${a.workspace === "school" ? "Школа" : "Пример"}`
                    : ""}
                  {a.revision !== null ? ` · версия ${a.revision}` : ""}
                  {a.detail ? " · " + a.detail : ""}
                </p>
              </article>
            ))}
            {!!audit.length && (
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const next = await api(
                      `access/audit?before=${audit[audit.length - 1].id}`,
                      workspace,
                    );
                    setAudit([...audit, ...next]);
                    if (!next.length) setNotice("Более ранних записей нет.");
                  })
                }
              >
                Показать более ранние
              </button>
            )}
          </section>
        </>
      )}
    </div>
  );
}

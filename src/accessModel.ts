// Pure rules of the «Пользователи и доступ» page: the tabs, the filters of the account table, the account form checks and the audit log wording.
import {
  roleNames,
  type Account,
  type AccessView,
  type Grant,
  type Role,
} from "./auth";

export type AccessTab =
  "users" | "invitations" | "families" | "classStaff" | "audit";
export const accessTabs: { id: AccessTab; label: string }[] = [
  { id: "users", label: "Учётные записи" },
  { id: "invitations", label: "Приглашения" },
  { id: "families", label: "Семьи" },
  { id: "classStaff", label: "Права по классам" },
  { id: "audit", label: "Журнал действий" },
];

export const viewNames: Record<AccessView, string> = {
  class: "Классы",
  teacher: "Учителя",
  room: "Кабинеты",
  group: "Группы",
  student: "Ученики",
};

export type AccessForm = Omit<Account, "id"> & {
  id?: string;
  password: string;
};
export interface AuditEntry {
  id: number;
  created: string;
  actorName: string;
  action: string;
  workspace: string | null;
  revision: number | null;
  detail: string;
}

export type RoleFilter = Role | "all";
/** Whether the lists of the page have arrived: until then «nothing found» must not be said. */
export type LoadStatus = "loading" | "ready" | "failed";
export type StateFilter = "all" | "active" | "inactive";
export const USERS_PAGE = 100;
export const AUDIT_PAGE = 50;

const workspaceName = (w: string) => (w === "school" ? "Школа" : "Пример");

/**
 * Roles that are present in the loaded list, in the order of roleNames. The count of a role is how many accounts of it match
 * the other conditions (state and search), so that the number on the control agrees with what the table shows after pressing it.
 */
export function roleOptions(
  users: Account[],
  f: { state?: StateFilter; query?: string } = {},
): { role: Role; label: string; count: number }[] {
  const base = filterAccounts(users, {
    role: "all",
    state: f.state ?? "all",
    query: f.query ?? "",
  });
  return (Object.keys(roleNames) as Role[])
    .filter((role) => users.some((u) => u.role === role))
    .map((role) => ({
      role,
      label: roleNames[role],
      count: base.filter((u) => u.role === role).length,
    }));
}

/** How many accounts each state filter would show, given the role filter and the search. */
export function stateCounts(
  users: Account[],
  f: { role?: RoleFilter; query?: string } = {},
) {
  const base = filterAccounts(users, {
    role: f.role ?? "all",
    state: "all",
    query: f.query ?? "",
  });
  const active = base.filter((u) => u.active).length;
  return { all: base.length, active, inactive: base.length - active };
}

/** The role filter that still makes sense for the list: a role nobody has any more falls back to «all». */
export function effectiveRole(
  filter: RoleFilter,
  users: Account[],
): RoleFilter {
  return filter === "all" || users.some((u) => u.role === filter)
    ? filter
    : "all";
}

/** The search of the page: a substring of the name and the login together, ignoring case. */
export function accountMatches(u: Account, query: string): boolean {
  return (u.name + " " + u.login)
    .toLocaleLowerCase()
    .includes(query.toLocaleLowerCase());
}

/** Keeps the order the server gave. */
export function filterAccounts(
  users: Account[],
  f: { role: RoleFilter; state: StateFilter; query: string },
): Account[] {
  return users.filter(
    (u) =>
      (f.role === "all" || u.role === f.role) &&
      (f.state === "all" || (f.state === "active") === u.active) &&
      accountMatches(u, f.query),
  );
}

/** The first `shown` items of a list and whether there is more. */
export function pageOf<T>(list: T[], shown: number) {
  const rows = list.slice(0, Math.max(0, shown));
  return {
    rows,
    shown: rows.length,
    total: list.length,
    more: rows.length < list.length,
  };
}

/** What the account has access to, as the list shows it. */
export function accessText(u: Account): string {
  return u.role === "admin"
    ? "Все пространства"
    : ["editor", "staff"].includes(u.role)
      ? u.workspaces.map(workspaceName).join(", ")
      : `Областей доступа: ${u.grants.length}`;
}

export function blankForm(workspace: string): AccessForm {
  return {
    name: "",
    login: "",
    role: "editor",
    active: true,
    version: 0,
    workspaces: [workspace],
    grants: [],
    password: "",
  };
}

/** The view of the grants editor that suits a role. */
export function viewForRole(role: Role): AccessView {
  return role === "teacher"
    ? "teacher"
    : ["student", "parent"].includes(role)
      ? "student"
      : "class";
}

const grantKey = (grants: Grant[]) =>
  JSON.stringify(
    grants
      .map((g) => ({
        workspace: g.workspace,
        view: g.view,
        entityIds: [...g.entityIds].sort(),
      }))
      .sort((a, b) =>
        (a.workspace + a.view).localeCompare(b.workspace + b.view),
      ),
  );

/**
 * The records selected in the grants editor that are not part of the form yet. Records of a grant the form already holds,
 * shown by «Посмотреть и изменить» and left as they are, are not pending: nothing was changed and nothing is lost.
 */
export function pendingTargets(
  form: AccessForm | null,
  space: string,
  view: AccessView,
  targets: string[],
): string[] {
  const held = form?.grants.find(
    (g) => g.workspace === space && g.view === view,
  );
  if (
    held &&
    held.entityIds.length === targets.length &&
    JSON.stringify([...held.entityIds].sort()) ===
      JSON.stringify([...targets].sort())
  )
    return [];
  return targets;
}

/** True when the form differs from the account it was opened with, or holds a typed password or pending selected records. */
export function accountFormDirty(
  form: AccessForm | null,
  base: AccessForm | null,
  repeat: string,
  pending: string[],
): boolean {
  if (!form) return false;
  if (form.password || repeat || pending.length) return true;
  if (!base) return false;
  return (
    form.name !== base.name ||
    form.login !== base.login ||
    form.role !== base.role ||
    form.active !== base.active ||
    [...form.workspaces].sort().join() !== [...base.workspaces].sort().join() ||
    grantKey(form.grants) !== grantKey(base.grants)
  );
}

/** What leaving the form (to the list, another tab, another account) does: nothing while it saves, one question when it holds changes. */
export function leaveDecision(
  dirty: boolean,
  busy: boolean,
): "blocked" | "ask" | "go" {
  return busy ? "blocked" : dirty ? "ask" : "go";
}

/** Where a just saved account is in the filtered list, and how many rows must be shown for it to be visible. */
export function savedRowPlace(
  filtered: Account[],
  login: string,
  shown: number,
): { index: number; shown: number } {
  const index = filtered.findIndex((u) => u.login === login);
  if (index < 0 || index < shown) return { index, shown };
  return { index, shown: Math.ceil((index + 1) / USERS_PAGE) * USERS_PAGE };
}

/**
 * What the list does after a save, judged on the list as the reload brought it: the role filter that is still valid,
 * whether the filters hide the saved account, how many rows to show and which row to mark.
 */
export function savedRowOutcome(
  loaded: Account[],
  filters: { role: RoleFilter; state: StateFilter; query: string },
  login: string,
  shown: number,
): { role: RoleFilter; hidden: boolean; shown: number; id: string | null } {
  const role = effectiveRole(filters.role, loaded);
  const visible = filterAccounts(loaded, { ...filters, role });
  const place = savedRowPlace(visible, login, shown);
  return {
    role,
    hidden: place.index < 0,
    shown: place.shown,
    id: place.index < 0 ? null : visible[place.index].id,
  };
}

export const auditActionNames: Record<string, string> = {
  "state.saved": "Сохранено расписание",
  "timetable.versioned": "Сохранена прежняя версия расписания",
  "absence.submitted": "Подана объяснительная",
  "absence.approved": "Принята объяснительная",
  "absence.rejected": "Отклонена объяснительная",
  "absence.revoked": "Отменено решение по объяснительной",
  "absence.withdrawn": "Родитель отозвал объяснительную",
  "absence.read": "Просмотрена причина отсутствия",
  "attendance.summary_read": "Просмотрена сводная посещаемость",
  "attendance.details_read": "Просмотрена посещаемость уроков ученика",
  "attendance.exported": "Выгружена посещаемость",
  "journal.coverage_read": "Проверено заполнение журналов",
  "journal.period_created": "Создан учебный период",
  "journal.period_updated": "Изменён учебный период",
  "journal.period_closed": "Журналы периода закрыты",
  "journal.period_reopened": "Период открыт для исправлений",
  "journal.period_archive_read": "Просмотрен архив журналов",
  "journal.coverage_exported": "Выгружена проверка журналов",
  "staff-time.workload_exported": "Выгружена нагрузка учителей",
  "absence.bulk_preview": "Подготовлено общее решение по объяснительным",
  "absence.bulk_applied": "Сохранено общее решение по объяснительным",
  "absence.history_read": "Просмотрена история объяснительной",
  "absence.file_read": "Скачан файл объяснительной",
  "absence.file_uploaded": "Загружен файл объяснительной",
  "absence.reviewers_updated": "Назначены ответственные за объяснительные",
  "absence.recorded": "Школа внесла записку об отсутствии",
  "absence.notes_preview": "Подготовлены записки об отсутствии от школы",
  "absence.notes_applied": "Внесены записки об отсутствии от школы",
  "absence.codes_saved": "Изменены типы отсутствия",
  "family.updated": "Изменены семейная связь и доступ к ребёнку",
  "family.history_read": "Просмотрена история семейной связи",
  "family.evidence_read": "Просмотрены основания семейной связи",
  "message.reported": "Отправлена жалоба на сообщение",
  "message.evidence_read": "Просмотрены материалы жалобы",
  "message.evidence_file_read": "Скачано вложение жалобы",
  "message.moderation_hide": "Сообщение скрыто по жалобе",
  "message.moderation_restore": "Сообщение восстановлено",
  "message.moderation_dismiss": "Жалоба отклонена",
  "message.manager_recovered": "Восстановлено управление обсуждением",
  "message.channels_updated": "Изменены каналы сообщений",
  "school.sections_updated": "Изменены разделы школы",
  "message.saved_group_saved": "Сохранена группа получателей",
  "message.saved_group_deleted": "Удалена группа получателей",
  "message.feed_replied": "Ответ под объявлением",
  "state.undo": "Отмена изменения",
  "state.redo": "Повтор изменения",
  "journal.draft_saved": "Сохранён черновик учебного журнала",
  "journal.final_scale": "Изменена шкала итоговых оценок",
  "journal.final_prepared": "Подготовлена итоговая ведомость",
  "journal.gradebook_queued": "Подготовка ведомости поставлена в очередь",
  "journal.gradebook_cancel_requested": "Запрошена отмена подготовки ведомости",
  "journal.gradebook_cancelled": "Подготовка ведомости отменена",
  "journal.gradebook_failed": "Подготовка ведомости не завершена",
  "journal.gradebook_stale":
    "Изменился источник или доступ к подготовке ведомости",
  "journal.gradebook_prepared": "Подготовлена итоговая ведомость",
  "journal.final_assign": "Назначен ответственный за итоги",
  "journal.final_saved": "Сохранён черновик итога",
  "journal.final_submitted": "Итог отправлен на утверждение",
  "journal.final_approve": "Утверждён итог",
  "journal.final_return": "Итог возвращён на исправление",
  "journal.final_publish": "Опубликован итог",
  "journal.final_reopen": "Открыто исправление итога",
  "journal.final_withdraw": "Отозван итог",
  "journal.final_report_settings": "Изменены настройки табеля школы",
  "journal.final_layout": "Изменён порядок предметов в табеле класса",
  "journal.published": "Опубликована запись учебного журнала",
  "journal.withdrawn": "Отозвана запись учебного журнала",
  "courses.intake_configured": "Изменены условия приёма заявок на курсы",
  "courses.preference_submitted": "Получены предпочтения по курсам",
  "courses.preference_returned": "Заявка возвращена для уточнения",
  "courses.preference_accepted": "Предпочтения перенесены в план заявок",
  "access.enabled": "Включён вход",
  "account.created": "Создана учётная запись",
  "account.updated": "Изменён доступ",
  "account.password_changed": "Сменён пароль",
  "account.password_reset_locally": "Пароль сброшен на сервере",
  "account.signed_in": "Вход в систему",
  "account.signed_out": "Выход из системы",
  "account.login_failed": "Неудачная попытка входа",
  "safety.briefing_created": "Записан инструктаж",
  "safety.briefing_updated": "Изменён инструктаж",
  "safety.briefing_withdrawn": "Инструктаж отозван",
};

/** The Russian name of an action; an unknown one stays as its code. */
export const auditAction = (a: Pick<AuditEntry, "action">): string =>
  auditActionNames[a.action] || a.action;

const DETAIL_LABELS: Record<string, string> = {
  reason: "причина",
  version: "версия записи",
  closureVersion: "версия закрытия",
  classId: "класс",
};
const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const OPAQUE_ID = /^[0-9a-f]{16,64}$/i;
const day = (value: string) => value.replace(ISO_DAY, "$3.$2.$1");

/**
 * The detail of an entry as text. Some actions store a small JSON object: its internal ids and empty values are
 * left out, dates are written as ДД.ММ.ГГГГ, «start» and «end» read as a period. Anything else comes back as it was.
 */
export function auditDetailText(detail: string): string {
  if (!detail.startsWith("{")) return detail;
  let data: unknown;
  try {
    data = JSON.parse(detail);
  } catch {
    return detail;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return detail;
  const fields = data as Record<string, unknown>;
  const out: string[] = [];
  const text = (v: unknown) =>
    typeof v === "string"
      ? day(v.trim())
      : typeof v === "number"
        ? String(v)
        : "";
  const start = text(fields.start),
    end = text(fields.end);
  if (start && end) out.push(start === end ? start : `${start} — ${end}`);
  for (const [key, value] of Object.entries(fields)) {
    if ((key === "start" || key === "end") && start && end) continue;
    const shown = text(value);
    if (!shown || OPAQUE_ID.test(shown)) continue;
    out.push(`${DETAIL_LABELS[key] ?? key}: ${shown}`);
  }
  return out.join(", ");
}

/** Space, version and detail of an entry, joined as the log showed them. */
export function auditDetails(a: AuditEntry): string {
  return [
    a.workspace ? workspaceName(a.workspace) : "",
    a.revision !== null ? `версия ${a.revision}` : "",
    auditDetailText(a.detail || ""),
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Local date and time, ДД.ММ.ГГГГ ЧЧ:ММ:СС. */
export function auditStamp(created: string): string {
  const d = new Date(created);
  if (Number.isNaN(d.getTime())) return created;
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(d.getDate())}.${two(d.getMonth() + 1)}.${d.getFullYear()} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

/** The search of the log: person, action name or details, ignoring case. */
export function auditMatches(a: AuditEntry, query: string): boolean {
  const q = query.trim().toLocaleLowerCase();
  if (!q) return true;
  return [a.actorName, auditAction(a), a.action, auditDetails(a)]
    .join(" ")
    .toLocaleLowerCase()
    .includes(q);
}

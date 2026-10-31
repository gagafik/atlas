import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import {
  classAssignmentChanges,
  classCapabilities,
  type ClassAssignment,
  type ClassAssignments,
  type ClassCapability,
} from "./classStaff";

type Options = {
  version: number;
  revision: number;
  legacy: boolean;
  assignments: ClassAssignments;
  classes: {
    id: string;
    name: string;
    classTeacherId: string | null;
    classTeacherName: string | null;
    classTeacherAccounts: string[];
  }[];
  accounts: {
    id: string;
    name: string;
    login: string;
    role: string;
    eligible: boolean;
  }[];
};
type History = {
  id: number;
  version: number;
  created: string;
  actor: string;
  reason: string;
  before: ClassAssignments;
  after: ClassAssignments;
};
type TeacherHistory = {
  id: number;
  created: string;
  actor: string;
  action: "saved" | "undo" | "redo";
  className: string;
  before: { id: string; name: string | null } | null;
  after: { id: string; name: string | null } | null;
};
const actionNames = { saved: "", undo: " · отмена", redo: " · повтор" };
// A row without revoked duties carries no such key: it must equal what the server stored.
const tidy = (row: ClassAssignment): ClassAssignment => {
  const { revoked, ...rest } = row;
  return revoked?.length ? { ...rest, revoked } : rest;
};
const allCapabilities = Object.keys(classCapabilities) as ClassCapability[];
const capsDiffer = (a: ClassCapability[], b: ClassCapability[]) =>
  [...a].sort().join() !== [...b].sort().join();

export default function ClassStaffPanel({ workspace }: { workspace: string }) {
  const [expanded, setExpanded] = useState(false),
    [options, setOptions] = useState<Options | null>(null);
  const [assignments, setAssignments] = useState<ClassAssignments>({}),
    [classId, setClassId] = useState(""),
    [accountId, setAccountId] = useState("");
  const [reason, setReason] = useState(""),
    [reviewed, setReviewed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [history, setHistory] = useState<History[]>([]),
    [next, setNext] = useState<number | null>(null);
  const [leaderHistory, setLeaderHistory] = useState<TeacherHistory[]>([]),
    [leaderNext, setLeaderNext] = useState<number | null>(null);
  const lock = useRef(false),
    alive = useRef(true),
    guarded = useRef(false);
  const changes = classAssignmentChanges(
    options?.assignments || {},
    assignments,
  );
  guarded.current =
    JSON.stringify(assignments) !== JSON.stringify(options?.assignments || {});
  useEffect(() => {
    alive.current = true;
    const leave = (e: Event) => {
      if (
        lock.current ||
        (guarded.current &&
          !confirm("Изменения полномочий не сохранены. Закрыть их?"))
      )
        e.preventDefault();
    };
    const unload = (e: BeforeUnloadEvent) => {
      if (lock.current || guarded.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("atlas-homework-leave", leave);
    window.addEventListener("beforeunload", unload);
    return () => {
      alive.current = false;
      window.removeEventListener("atlas-homework-leave", leave);
      window.removeEventListener("beforeunload", unload);
    };
  }, []);
  async function run(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function reload() {
    const value: Options = await api("access/class-staff", workspace);
    if (!alive.current) return;
    setOptions(value);
    setAssignments(value.assignments);
    setReviewed(false);
    setReason("");
    setClassId((old) => old || value.classes[0]?.id || "");
  }
  const name = (id: string) =>
    options?.accounts.find((a) => a.id === id)?.name ||
    `Недоступная учётная запись (${id})`;
  const className = (id: string) =>
    options?.classes.find((c) => c.id === id)?.name ||
    `Недоступный класс (${id})`;
  const labels = (caps: ClassCapability[]) =>
    caps.map((c) => classCapabilities[c]).join(", ") || "Нет полномочий";
  const selected = assignments[classId] || [];
  const current = options?.classes.find((c) => c.id === classId);
  const leaders = current?.classTeacherAccounts || [];
  const revokedList = (caps: ClassCapability[]) =>
    caps.length ? labels(caps) : "ничего не снято";
  // Switch one default duty of a class teacher account on or off; an empty row disappears.
  const setDuty = (id: string, cap: ClassCapability, on: boolean) => {
    const row = selected.find((r) => r.accountId === id) || {
      accountId: id,
      capabilities: [],
    };
    const revoked = (row.revoked || []).filter((c) => c !== cap);
    const updated = tidy({
      ...row,
      capabilities: on
        ? row.capabilities
        : row.capabilities.filter((c) => c !== cap),
      revoked: on ? revoked : [...revoked, cap],
    });
    const rows = (
      selected.some((r) => r.accountId === id)
        ? selected.map((r) => (r.accountId === id ? updated : r))
        : [...selected, updated]
    ).filter(
      (r) =>
        r !== updated || updated.capabilities.length || updated.revoked?.length,
    );
    const others = Object.fromEntries(
      Object.entries(assignments).filter(([key]) => key !== classId),
    );
    change(rows.length ? { ...assignments, [classId]: rows } : others);
  };
  const change = (value: ClassAssignments) => {
    setAssignments(value);
    setReviewed(false);
    setNotice("");
  };
  const invalid = Object.entries(assignments).some(
    ([id, rows]) =>
      rows.length > 0 &&
      (!options?.classes.some((c) => c.id === id) ||
        rows.some(
          (r) =>
            (!r.capabilities.length && !r.revoked?.length) ||
            (r.capabilities.length > 0 &&
              !options?.accounts.some(
                (a) => a.id === r.accountId && a.eligible,
              )),
        )),
  );
  async function loadHistory(before?: number) {
    const result = await api(
      `access/class-staff/history${before ? `?before=${before}` : ""}`,
      workspace,
    );
    if (alive.current) {
      setHistory((old) => (before ? [...old, ...result.items] : result.items));
      setNext(result.nextBefore);
    }
  }
  async function loadLeaderHistory(before?: number) {
    const result = await api(
      `access/class-staff/teachers/history${before ? `?before=${before}` : ""}`,
      workspace,
    );
    if (alive.current) {
      setLeaderHistory((old) =>
        before ? [...old, ...result.items] : result.items,
      );
      setLeaderNext(result.nextBefore);
    }
  }
  return (
    <details
      className="class-staff-panel"
      open={expanded}
      onToggle={(e) => {
        const open = e.currentTarget.open;
        setExpanded(open);
        if (open && !options) void run(reload);
      }}
    >
      <summary>Права работы с классами</summary>
      <p>
        Классный руководитель по умолчанию получает все пять полномочий своего
        класса. Здесь можно снять отдельное полномочие у него или назначить
        заместителя, психолога и других сотрудников. Они не открывают оценки,
        чужие журналы для редактирования или управление расписанием.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {options && (
        <>
          {options.legacy && (
            <p className="info-line">
              Прежние ответственные за объяснительные имели все три полномочия.
              Они сохранены в списке; проверьте каждое перед сохранением.
            </p>
          )}
          <button
            disabled={busy}
            onClick={() => {
              if (
                !guarded.current ||
                confirm(
                  "Сбросить несохранённые изменения и загрузить актуальные полномочия?",
                )
              )
                void run(reload);
            }}
          >
            Обновить полномочия
          </button>
          <fieldset className="access-fields" disabled={busy}>
            <label className="field">
              <span>Класс для назначения полномочий</span>
              <select
                value={classId}
                onChange={(e) => setClassId(e.target.value)}
              >
                {options.classes.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
                {Object.keys(assignments)
                  .filter((id) => !options.classes.some((c) => c.id === id))
                  .map((id) => (
                    <option key={id} value={id}>
                      {className(id)}
                    </option>
                  ))}
              </select>
            </label>
            <section className="export-card" aria-label="Классный руководитель">
              <h4>
                Классный руководитель:{" "}
                {current?.classTeacherName ||
                  "не назначен — назначается в карточке класса администратором"}
              </h4>
              {current?.classTeacherId && !leaders.length && (
                <p>
                  Ни одна действующая учётная запись учителя не связана с этой
                  записью, поэтому полномочия по умолчанию пока никому не
                  выданы.
                </p>
              )}
              {leaders.map((id) => {
                const row = selected.find((r) => r.accountId === id);
                return (
                  <div key={id}>
                    <strong>{name(id)}</strong>
                    {allCapabilities.map((cap) => (
                      <label className="class-staff-check" key={cap}>
                        <input
                          type="checkbox"
                          checked={!row?.revoked?.includes(cap)}
                          onChange={(e) => setDuty(id, cap, e.target.checked)}
                        />
                        {classCapabilities[cap]} — действует по умолчанию
                      </label>
                    ))}
                  </div>
                );
              })}
            </section>
            <label className="field">
              <span>Добавить ответственного сотрудника</span>
              <select
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
              >
                <option value="">Выберите сотрудника</option>
                {options.accounts
                  .filter(
                    (a) =>
                      a.eligible &&
                      !leaders.includes(a.id) &&
                      !selected.some((r) => r.accountId === a.id),
                  )
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} · {a.login}
                    </option>
                  ))}
              </select>
            </label>
            <button
              disabled={
                !accountId ||
                selected.length >= 20 ||
                !options.classes.some((c) => c.id === classId)
              }
              onClick={() => {
                change({
                  ...assignments,
                  [classId]: [...selected, { accountId, capabilities: [] }],
                });
                setAccountId("");
              }}
            >
              Добавить сотрудника в класс
            </button>
            {selected
              .filter((row) => !leaders.includes(row.accountId))
              .map((row) => (
                <article className="export-card" key={row.accountId}>
                  <h4>{name(row.accountId)}</h4>
                  {row.capabilities.length > 0 &&
                    !options.accounts.some(
                      (a) => a.id === row.accountId && a.eligible,
                    ) && (
                      <p>
                        Назначение сейчас не действует. Удалите его или
                        восстановите доступ сотрудника перед сохранением.
                      </p>
                    )}
                  {row.revoked?.length ? (
                    <p>
                      Снятие не действует: сотрудник сейчас не классный
                      руководитель этого класса ({labels(row.revoked)}).
                    </p>
                  ) : null}
                  {(Object.keys(classCapabilities) as ClassCapability[]).map(
                    (cap) => (
                      <label className="class-staff-check" key={cap}>
                        <input
                          type="checkbox"
                          checked={row.capabilities.includes(cap)}
                          onChange={(e) =>
                            change({
                              ...assignments,
                              [classId]: selected.map((r) =>
                                r.accountId === row.accountId
                                  ? tidy({
                                      ...r,
                                      capabilities: e.target.checked
                                        ? [...r.capabilities, cap]
                                        : r.capabilities.filter(
                                            (c) => c !== cap,
                                          ),
                                      revoked: (r.revoked || []).filter(
                                        (c) => c !== cap,
                                      ),
                                    })
                                  : r,
                              ),
                            })
                          }
                        />
                        {classCapabilities[cap]}
                      </label>
                    ),
                  )}
                  <button
                    onClick={() =>
                      change({
                        ...assignments,
                        [classId]: selected.filter(
                          (r) => r.accountId !== row.accountId,
                        ),
                      })
                    }
                  >
                    Убрать назначение: {name(row.accountId)}
                  </button>
                </article>
              ))}
            {!selected.some((row) => !leaders.includes(row.accountId)) && (
              <p>В этом классе пока нет назначенных сотрудников.</p>
            )}
            <p>
              Рассмотрение объяснительных открывает причины и приложенные файлы.
              Два остальных полномочия показывают только посещаемость и
              состояние заполнения.
            </p>
            {invalid && (
              <p role="alert">
                Выберите хотя бы одно полномочие каждому сотруднику и удалите
                недействующие назначения и пустые строки.
              </p>
            )}
            <button
              disabled={invalid || (!guarded.current && !options.legacy)}
              onClick={() => setReviewed(true)}
            >
              Проверить изменения полномочий
            </button>
            {reviewed && (
              <section aria-label="Проверка полномочий">
                <h4>Изменения перед сохранением</h4>
                {changes.map((r) => (
                  <article
                    className="export-card"
                    key={r.classId + r.accountId}
                  >
                    <strong>
                      {className(r.classId)} · {name(r.accountId)}
                    </strong>
                    {capsDiffer(r.before, r.after) && (
                      <>
                        <p>Сейчас: {labels(r.before)}</p>
                        <p>После сохранения: {labels(r.after)}</p>
                      </>
                    )}
                    {capsDiffer(r.beforeRevoked, r.afterRevoked) && (
                      <>
                        <p>
                          Снято у классного руководителя сейчас:{" "}
                          {revokedList(r.beforeRevoked)}
                        </p>
                        <p>
                          Снято у классного руководителя после сохранения:{" "}
                          {revokedList(r.afterRevoked)}
                        </p>
                      </>
                    )}
                  </article>
                ))}
                {!changes.length && (
                  <p>
                    Текущие полномочия сохранятся. Прежние назначения перейдут в
                    единый реестр.
                  </p>
                )}
                <label className="field">
                  <span>Основание изменения полномочий</span>
                  <input
                    maxLength={500}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </label>
                <button
                  className="primary"
                  disabled={!reason.trim() || invalid}
                  onClick={() =>
                    void run(async () => {
                      await api("access/class-staff", workspace, "PUT", {
                        version: options.version,
                        revision: options.revision,
                        assignments,
                        reason,
                      });
                      await reload();
                      setNotice(
                        "Полномочия сохранены. Следующие запросы сотрудников используют новый доступ.",
                      );
                      await loadHistory();
                    })
                  }
                >
                  Сохранить полномочия
                </button>
              </section>
            )}
          </fieldset>
          <details>
            <summary>История полномочий</summary>
            <button
              disabled={busy}
              onClick={() => void run(() => loadHistory())}
            >
              Загрузить историю полномочий
            </button>
            {history.map((h) => (
              <article className="export-card" key={h.id}>
                <strong>
                  Редакция {h.version} · {h.actor}
                </strong>
                <p>
                  {new Date(h.created).toLocaleString("ru-RU")} · {h.reason}
                </p>
                {classAssignmentChanges(h.before, h.after).map((r) => (
                  <p key={r.classId + r.accountId}>
                    {className(r.classId)} · {name(r.accountId)}:{" "}
                    {capsDiffer(r.before, r.after) &&
                      `${labels(r.before)} → ${labels(r.after)}`}
                    {capsDiffer(r.beforeRevoked, r.afterRevoked) &&
                      ` Снято у классного руководителя: ${revokedList(r.beforeRevoked)} → ${revokedList(r.afterRevoked)}`}
                  </p>
                ))}
              </article>
            ))}
            {next && (
              <button
                disabled={busy}
                onClick={() => void run(() => loadHistory(next))}
              >
                Более ранние изменения полномочий
              </button>
            )}
          </details>
          <details>
            <summary>История классных руководителей</summary>
            <button
              disabled={busy}
              onClick={() => void run(() => loadLeaderHistory())}
            >
              Загрузить историю классных руководителей
            </button>
            {leaderHistory.map((h) => (
              <p key={h.id}>
                {h.className}: {h.before?.name || "не назначен"} →{" "}
                {h.after?.name || "не назначен"} · {h.actor} ·{" "}
                {new Date(h.created).toLocaleString("ru-RU")}
                {actionNames[h.action]}
              </p>
            ))}
            {leaderNext && (
              <button
                disabled={busy}
                onClick={() => void run(() => loadLeaderHistory(leaderNext))}
              >
                Более ранние смены классных руководителей
              </button>
            )}
          </details>
        </>
      )}
    </details>
  );
}

import { useEffect, useState } from "react";
import { api } from "./api";
import { Multi } from "./DatedSchedule";
import { roleNames, type Grant } from "./auth";
import type { State } from "./types";

type InviteRole = "parent" | "student" | "teacher";
interface Invitation {
  id: number;
  recipient: string;
  role: InviteRole;
  grants: Grant[];
  created: string;
  expires: string;
  status: "pending" | "accepted" | "revoked" | "expired";
  accountId: string | null;
}
const statuses = {
  pending: "Ожидает принятия",
  accepted: "Принято",
  revoked: "Отозвано",
  expired: "Срок истёк",
};

export default function InvitationPanel({
  state,
  workspace,
  onChange,
}: {
  state: State;
  workspace: string;
  onChange: () => Promise<void>;
}) {
  const [items, setItems] = useState<Invitation[]>([]),
    [next, setNext] = useState<number | null>(null);
  const [recipient, setRecipient] = useState(""),
    [role, setRole] = useState<InviteRole>("parent");
  const [targets, setTargets] = useState<string[]>([]),
    [days, setDays] = useState(7);
  const [issued, setIssued] = useState<(Invitation & { code: string }) | null>(
    null,
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [copied, setCopied] = useState(false);
  const [creating, setCreating] = useState(false);
  const options =
    role === "teacher"
      ? state.teachers
      : state.students
          .filter((s) => s.active !== false)
          .map((s) => ({
            id: s.id,
            name:
              s.name +
              " · " +
              (state.classes.find((c) => c.id === s.classId)?.name ||
                s.classId),
          }));
  async function reload(before?: number) {
    const result = await api(
      `access/invitations${before ? `?before=${before}` : ""}`,
      workspace,
    );
    setItems((old) => (before ? [...old, ...result.items] : result.items));
    setNext(result.nextBefore);
  }
  useEffect(() => {
    let alive = true;
    api("access/invitations", workspace)
      .then((result) => {
        if (alive) {
          setItems(result.items);
          setNext(result.nextBefore);
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [workspace]);
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function targetName(grant: Grant, id: string) {
    const label =
      grant.workspace === workspace
        ? (grant.view === "teacher" ? state.teachers : state.students).find(
            (s) => s.id === id,
          )?.name
        : null;
    return label || id;
  }
  return (
    <section className="invitation-panel">
      <h3>Приглашения в школу</h3>
      <p>
        Выберите конкретного получателя и его доступ. Передайте код этому
        человеку после проверки в школе. Каждый представитель ребёнка получает
        отдельное приглашение. Код действует один раз.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      <div className="dated-toolbar">
        <button disabled={busy} onClick={() => setCreating(!creating)}>
          {creating ? "Закрыть форму приглашения" : "Пригласить пользователя"}
        </button>
        <button disabled={busy} onClick={() => void run(() => reload())}>
          Обновить приглашения
        </button>
      </div>
      {issued && (
        <div className="invitation-code" role="status">
          <strong>Код для {issued.recipient}</strong>
          <p>
            Скопируйте сейчас: после закрытия код больше не показывается.
            Действует до {new Date(issued.expires).toLocaleString("ru-RU")}.
          </p>
          <input
            aria-label="Созданный код приглашения"
            readOnly
            value={issued.code}
            onFocus={(e) => e.target.select()}
          />
          <div className="dated-toolbar">
            <button
              onClick={() =>
                void run(async () => {
                  await navigator.clipboard.writeText(issued.code);
                  setCopied(true);
                })
              }
            >
              {copied ? "Скопировано" : "Скопировать код"}
            </button>
            <button onClick={() => setIssued(null)}>Скрыть код</button>
          </div>
          <p>
            Получателю: откройте этот сайт и выберите «Создать аккаунт по
            приглашению». Если аккаунт уже есть, войдите и нажмите «Принять
            приглашение».
          </p>
        </div>
      )}
      {creating && (
        <form
          className="dated-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const result = await api(
                "access/invitations",
                workspace,
                "POST",
                {
                  recipient,
                  role,
                  days,
                  grants: [
                    {
                      workspace,
                      view: role === "teacher" ? "teacher" : "student",
                      entityIds: targets,
                    },
                  ],
                },
              );
              setIssued(result);
              setCopied(false);
              setRecipient("");
              setTargets([]);
              setCreating(false);
              await reload();
              await onChange();
            });
          }}
        >
          <fieldset className="access-fields" disabled={busy}>
            <div className="dated-fields">
              <label className="field">
                <span>Кому выдать приглашение</span>
                <input
                  name="invite-recipient"
                  required
                  maxLength={160}
                  value={recipient}
                  onChange={(e) => setRecipient(e.target.value)}
                  placeholder="Имя и фамилия получателя"
                />
              </label>
              <label className="field">
                <span>Роль приглашённого</span>
                <select
                  value={role}
                  onChange={(e) => {
                    setRole(e.target.value as InviteRole);
                    setTargets([]);
                  }}
                >
                  <option value="parent">Родитель</option>
                  <option value="student">Ученик</option>
                  <option value="teacher">Учитель</option>
                </select>
              </label>
              <label className="field">
                <span>Срок, дней</span>
                <input
                  type="number"
                  required
                  min={1}
                  max={30}
                  value={days}
                  onChange={(e) => setDays(Number(e.target.value))}
                />
              </label>
            </div>
            <Multi
              label={
                role === "teacher"
                  ? "Расписание преподавателя"
                  : "Дети, к которым выдать доступ"
              }
              items={options}
              values={targets}
              onChange={setTargets}
            />
            {role === "student" && targets.length > 1 && (
              <p role="alert">
                Для ученика выберите только его собственную запись.
              </p>
            )}
            <button
              className="primary"
              disabled={
                !targets.length ||
                targets.length > 200 ||
                (role === "student" && targets.length !== 1)
              }
            >
              Создать приглашение
            </button>
          </fieldset>
        </form>
      )}
      {items.length === 0 && <p className="muted">Приглашений пока нет.</p>}
      <div className="access-users">
        {items.map((item) => (
          <article className="export-card" key={item.id}>
            <h4>{item.recipient}</h4>
            <p>
              {roleNames[item.role]} · {statuses[item.status]}
            </p>
            <p>
              {item.grants
                .map(
                  (g) =>
                    `${g.workspace === "school" ? "Школа" : "Пример"}: ${g.entityIds.map((id) => targetName(g, id)).join(", ")}`,
                )
                .join("; ")}
            </p>
            <small>
              Срок до {new Date(item.expires).toLocaleString("ru-RU")}
            </small>
            {item.status === "pending" && (
              <p>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(
                        `access/invitations/${item.id}/revoke`,
                        workspace,
                        "POST",
                      );
                      if (issued?.id === item.id) setIssued(null);
                      await reload();
                      await onChange();
                    })
                  }
                >
                  Отозвать приглашение
                </button>
              </p>
            )}
            {item.status === "accepted" && (
              <p className="muted">
                Доступ изменяется в учётной записи пользователя.
              </p>
            )}
          </article>
        ))}
      </div>
      {next && (
        <button disabled={busy} onClick={() => void run(() => reload(next))}>
          Показать более ранние
        </button>
      )}
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { State } from "./types";
import { roleNames } from "./auth";
import { download } from "./utils";
import {
  invitationBatchCsv,
  targetLabel,
  type BatchRow,
  type BatchPreview,
  type BatchReceipt,
  type BatchIssue,
  type InviteRole,
} from "./bulkInvitations";
import "./bulkInvitations.css";

const base = "access/invitations/batches";
const statuses = {
  pending: "Ожидает принятия",
  accepted: "Принято",
  revoked: "Отозвано",
  expired: "Срок истёк",
};

export default function BulkInvitationPanel({
  state,
  workspace,
  onChange,
}: {
  state: State;
  workspace: string;
  onChange: () => Promise<void>;
}) {
  const [rows, setRows] = useState<BatchRow[]>([]),
    [days, setDays] = useState(7);
  const [role, setRole] = useState<InviteRole>("student"),
    [classId, setClassId] = useState(""),
    [search, setSearch] = useState(""),
    [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string[]>([]),
    [parentName, setParentName] = useState("");
  const [preview, setPreview] = useState<BatchPreview | null>(null),
    [acknowledged, setAcknowledged] = useState<string[]>([]);
  const [pending, setPending] = useState<BatchIssue | null>(null),
    [receipt, setReceipt] = useState<BatchReceipt | null>(null);
  const [batches, setBatches] = useState<
      { id: number; created: string; count: number }[]
    >([]),
    [next, setNext] = useState<number | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const lock = useRef(false),
    alive = useRef(true),
    guarded = useRef(false);
  guarded.current =
    rows.length > 0 || !!pending || !!receipt?.items.some((i) => i.code);
  useEffect(() => {
    alive.current = true;
    const leave = (e: Event) => {
      if (
        lock.current ||
        (guarded.current &&
          !window.confirm(
            "Список приглашений или показанные коды будут закрыты. Коды повторно не показываются. Продолжить?",
          ))
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
    void reload().catch((e) => {
      if (alive.current) setError(e.message);
    });
    return () => {
      alive.current = false;
      window.removeEventListener("atlas-homework-leave", leave);
      window.removeEventListener("beforeunload", unload);
    };
  }, []);
  async function reload(before?: number) {
    const result = await api(
      `${base}${before ? `?before=${before}` : ""}`,
      workspace,
    );
    if (alive.current) {
      setBatches((old) => (before ? [...old, ...result.items] : result.items));
      setNext(result.nextBefore);
    }
  }
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
  function change(nextRows: BatchRow[]) {
    setRows(nextRows);
    setPreview(null);
    setAcknowledged([]);
  }
  const entities =
    role === "teacher"
      ? state.teachers.map((t) => ({
          id: t.id,
          name: t.name,
          className: "",
          classId: "",
        }))
      : state.students
          .filter((s) => s.active !== false)
          .map((s) => ({
            id: s.id,
            name: s.name,
            classId: s.classId,
            className:
              state.classes.find((c) => c.id === s.classId)?.name || s.classId,
          }));
  const filtered = entities.filter(
    (e) =>
      (!classId || role === "teacher" || e.classId === classId) &&
      targetLabel(e).toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  );
  const shown = filtered.slice(page * 50, (page + 1) * 50);
  function add(groupParent = false) {
    const targets = entities.filter((e) => selected.includes(e.id));
    if (!targets.length) return;
    const added: BatchRow[] = groupParent
      ? [
          {
            key: crypto.randomUUID(),
            recipient: parentName.trim(),
            role: "parent",
            entityIds: targets.map((e) => e.id),
          },
        ]
      : targets.map((e) => ({
          key: crypto.randomUUID(),
          recipient: role === "parent" ? "" : e.name,
          role,
          entityIds: [e.id],
        }));
    if (rows.length + added.length > 50) {
      setError("В одном пакете может быть до 50 получателей. Уменьшите выбор.");
      return;
    }
    change([...rows, ...added]);
    setSelected([]);
    setParentName("");
    setError("");
  }
  async function send(payload: BatchIssue) {
    // Keep the identical request after an uncertain network result. The receipt
    // prevents reissuing codes, including after another account accepted one.
    try {
      const result: BatchReceipt = await api(base, workspace, "POST", payload);
      if (!alive.current) return;
      setReceipt(result);
      setPending(null);
      setRows([]);
      setPreview(null);
      setAcknowledged([]);
      setNotice(
        result.replayed
          ? "Пакет уже создан. Повторный запрос не создал новых приглашений. Коды восстановить нельзя; при потере отзовите непринятые приглашения и создайте новый пакет."
          : `Создано приглашений: ${result.items.length}. Сохраните коды сейчас и передайте каждому только его код.`,
      );
      await reload();
      await onChange();
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (alive.current && status && status < 500) {
        setPending(null);
        setPreview(null);
        setAcknowledged([]);
      }
      throw e;
    }
  }
  function clearCodes() {
    if (
      !receipt?.items.some((i) => i.code) ||
      window.confirm("Скрыть коды? Повторно получить их нельзя.")
    )
      setReceipt(null);
  }
  function targetName(row: BatchRow, id: string) {
    if (row.role === "teacher")
      return state.teachers.find((t) => t.id === id)?.name || id;
    const student = state.students.find((s) => s.id === id);
    return student
      ? `${student.name} · ${state.classes.find((c) => c.id === student.classId)?.name || student.classId}`
      : id;
  }
  const warnings =
    preview?.items.filter((i) => i.warnings.length).map((i) => i.key) || [];
  const codesVisible = !!receipt?.items.some((i) => i.code);
  return (
    <section className="bulk-invitations" aria-label="Массовые приглашения">
      <h3>Массовые приглашения</h3>
      <p>
        До 50 получателей за одну выдачу. Для каждого — отдельный код. Сначала
        проверьте имена и доступ; передавайте код только проверенному
        получателю.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {pending && (
        <div className="invitation-code">
          <p>
            Ответ на выдачу не получен. Повторите тот же запрос: сервер
            проверит, был ли создан пакет. Список заблокирован до получения
            результата.
          </p>
          <button disabled={busy} onClick={() => void run(() => send(pending))}>
            Проверить результат выдачи
          </button>
        </div>
      )}
      {receipt && (
        <section aria-label="Выданный пакет">
          <h4>Пакет №{receipt.id}</h4>
          <p>
            Получателю: откройте сайт школы и выберите «Создать аккаунт по
            приглашению». Если аккаунт уже есть, войдите и нажмите «Принять
            приглашение».
          </p>
          <p>
            Коды видны только в ответе создания и не сохраняются в браузере.
            Файл с кодами храните в закрытом месте; не отправляйте весь файл
            одному получателю.
          </p>
          {codesVisible && (
            <button
              disabled={busy}
              onClick={() =>
                download(
                  `atlas-invitations-${receipt.id}.csv`,
                  invitationBatchCsv(receipt),
                  "text/csv;charset=utf-8",
                )
              }
            >
              Скачать коды CSV
            </button>
          )}
          <button disabled={busy} onClick={clearCodes}>
            {codesVisible ? "Скрыть коды пакета" : "Закрыть пакет"}
          </button>
          <button
            disabled={
              busy || !receipt.items.some((i) => i.status === "pending")
            }
            onClick={() => {
              if (
                !window.confirm(
                  "Отозвать все ещё не принятые приглашения этого пакета? Доступ зарегистрированных пользователей сохранится.",
                )
              )
                return;
              void run(async () => {
                const result = await api(
                  `${base}/${receipt.id}/revoke`,
                  workspace,
                  "POST",
                );
                if (alive.current) {
                  setReceipt(result);
                  setNotice(`Отозвано приглашений: ${result.revokedCount}.`);
                }
                await onChange();
              });
            }}
          >
            Отозвать непринятые приглашения пакета
          </button>
          {receipt.items.map((item) => (
            <article className="export-card" key={item.id}>
              <h4>
                {item.recipient} · {roleNames[item.role]}
              </h4>
              <p>{item.targets.map(targetLabel).join("; ")}</p>
              <p>
                {statuses[item.status]} · до{" "}
                {new Date(item.expires).toLocaleString("ru-RU")}
              </p>
              {item.code && item.status === "pending" && (
                <>
                  <input
                    readOnly
                    aria-label={`Код для ${item.recipient}`}
                    value={item.code}
                    onFocus={(e) => e.target.select()}
                  />
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await navigator.clipboard.writeText(item.code!);
                        setNotice(`Код для ${item.recipient} скопирован.`);
                      })
                    }
                  >
                    Скопировать код для {item.recipient}
                  </button>
                </>
              )}
            </article>
          ))}
        </section>
      )}
      <fieldset disabled={busy || !!pending || codesVisible}>
        <div className="dated-fields">
          <label className="field">
            <span>Кого пригласить пакетом</span>
            <select
              value={role}
              onChange={(e) => {
                setRole(e.target.value as InviteRole);
                setSelected([]);
                setPage(0);
              }}
            >
              <option value="student">Учеников</option>
              <option value="parent">Родителей</option>
              <option value="teacher">Учителей</option>
            </select>
          </label>
          {role !== "teacher" && (
            <label className="field">
              <span>Класс для выбора</span>
              <select
                value={classId}
                onChange={(e) => {
                  setClassId(e.target.value);
                  setPage(0);
                  setSelected([]);
                }}
              >
                <option value="">Все классы</option>
                {state.classes.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            <span>Поиск школьной записи</span>
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
            />
          </label>
        </div>
        <p>
          Выбрано записей: {selected.length}.{" "}
          {role === "parent" &&
            "Имена родителей заполняются отдельно; имя ребёнка не подтверждает полномочия получателя."}
        </p>
        <button
          onClick={() =>
            setSelected((old) => [
              ...new Set([...old, ...shown.map((e) => e.id)]),
            ])
          }
        >
          Выбрать страницу
        </button>
        <button onClick={() => setSelected([])}>Снять выбор</button>
        <div className="bulk-targets">
          {shown.map((entity) => (
            <label key={entity.id}>
              <input
                type="checkbox"
                checked={selected.includes(entity.id)}
                onChange={(e) =>
                  setSelected((old) =>
                    e.target.checked
                      ? [...old, entity.id]
                      : old.filter((id) => id !== entity.id),
                  )
                }
              />
              {targetLabel(entity)}
            </label>
          ))}
        </div>
        <div className="dated-toolbar">
          <button disabled={page === 0} onClick={() => setPage((n) => n - 1)}>
            Предыдущие записи
          </button>
          <span>Страница {page + 1}</span>
          <button
            disabled={(page + 1) * 50 >= filtered.length}
            onClick={() => setPage((n) => n + 1)}
          >
            Следующие записи
          </button>
        </div>
        <button
          disabled={!selected.length || rows.length + selected.length > 50}
          onClick={() => add()}
        >
          Добавить выбранных по отдельности
        </button>
        {role === "parent" && (
          <div className="dated-fields">
            <label className="field">
              <span>Один родитель выбранных детей</span>
              <input
                maxLength={160}
                value={parentName}
                onChange={(e) => setParentName(e.target.value)}
                placeholder="Имя и фамилия проверенного получателя"
              />
            </label>
            <button
              disabled={
                !selected.length ||
                selected.length > 200 ||
                !parentName.trim() ||
                rows.length >= 50
              }
              onClick={() => add(true)}
            >
              Добавить одному родителю
            </button>
          </div>
        )}
        <h4>Получатели пакета: {rows.length}/50</h4>
        {rows.map((row, i) => (
          <article className="export-card" key={row.key}>
            <label className="field">
              <span>
                Получатель {i + 1} · {roleNames[row.role]}
              </span>
              <input
                maxLength={160}
                value={row.recipient}
                onChange={(e) =>
                  change(
                    rows.map((r) =>
                      r.key === row.key
                        ? { ...r, recipient: e.target.value }
                        : r,
                    ),
                  )
                }
              />
            </label>
            <p>
              Доступ:{" "}
              {row.entityIds.map((id) => targetName(row, id)).join("; ")}
            </p>
            <button
              onClick={() => change(rows.filter((r) => r.key !== row.key))}
            >
              Убрать получателя {i + 1}
            </button>
          </article>
        ))}
        {rows.length > 0 && (
          <>
            <label className="field">
              <span>Срок приглашений, дней</span>
              <input
                type="number"
                min={1}
                max={30}
                value={days}
                onChange={(e) => {
                  setDays(Number(e.target.value));
                  setPreview(null);
                  setAcknowledged([]);
                }}
              />
            </label>
            <button
              disabled={
                rows.some((r) => !r.recipient.trim()) ||
                !Number.isInteger(days) ||
                days < 1 ||
                days > 30
              }
              onClick={() =>
                void run(async () => {
                  const result = await api(
                    `${base}/preview`,
                    workspace,
                    "POST",
                    { workspace, days, rows },
                  );
                  if (alive.current) {
                    setPreview(result);
                    setAcknowledged([]);
                  }
                })
              }
            >
              Проверить список приглашений
            </button>
            <button
              onClick={() => {
                if (
                  window.confirm(
                    "Очистить список получателей? Приглашения ещё не созданы.",
                  )
                )
                  change([]);
              }}
            >
              Очистить список
            </button>
          </>
        )}
        {preview && (
          <section aria-label="Проверка пакета">
            <h4>Проверьте доступ перед выдачей</h4>
            {preview.items.map((item) => (
              <article className="export-card" key={item.key}>
                <strong>
                  {item.recipient} · {roleNames[item.role]}
                </strong>
                <p>{item.targets.map(targetLabel).join("; ")}</p>
                {item.warnings.map((w) => (
                  <p key={w.kind}>
                    {w.kind === "accounts"
                      ? "Уже есть действующие аккаунты"
                      : "Есть ожидающие приглашения"}
                    : {w.count}. {w.names.join(", ")}
                  </p>
                ))}
                {item.warnings.length > 0 && (
                  <label>
                    <input
                      type="checkbox"
                      checked={acknowledged.includes(item.key)}
                      onChange={(e) =>
                        setAcknowledged((old) =>
                          e.target.checked
                            ? [...old, item.key]
                            : old.filter((key) => key !== item.key),
                        )
                      }
                    />
                    Проверил существующий доступ; отдельное приглашение этому
                    получателю необходимо.
                  </label>
                )}
              </article>
            ))}
            <button
              className="primary"
              disabled={warnings.some((key) => !acknowledged.includes(key))}
              onClick={() => {
                if (lock.current) return;
                const payload: BatchIssue = {
                  workspace,
                  days,
                  rows,
                  previewToken: preview.previewToken,
                  acknowledgedRows: acknowledged,
                  requestId: crypto.randomUUID(),
                };
                setPending(payload);
                void run(() => send(payload));
              }}
            >
              Выдать коды: {rows.length}
            </button>
          </section>
        )}
      </fieldset>
      <details>
        <summary>Ранее выданные пакеты</summary>
        <p>
          Здесь сохраняются получатели и статусы. Коды повторно не показываются.
        </p>
        <button disabled={busy} onClick={() => void run(() => reload())}>
          Обновить пакеты
        </button>
        {batches.map((batch) => (
          <p key={batch.id}>
            <button
              disabled={busy || codesVisible || !!pending}
              onClick={() =>
                void run(async () => {
                  const result = await api(`${base}/${batch.id}`, workspace);
                  if (alive.current) setReceipt(result);
                })
              }
            >
              Пакет №{batch.id} · Получателей: {batch.count} ·{" "}
              {new Date(batch.created).toLocaleString("ru-RU")}
            </button>
          </p>
        ))}
        {next && (
          <button disabled={busy} onClick={() => void run(() => reload(next))}>
            Более ранние пакеты
          </button>
        )}
      </details>
    </section>
  );
}

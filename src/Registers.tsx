import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { roleNames, type Account } from "./auth";
import { displayTime } from "./meetings";
import { HistoryList } from "./RegisterParts";
import RegisterIncidents from "./RegisterIncidents";
import RegisterInventory from "./RegisterInventory";
import RegisterVisitors from "./RegisterVisitors";
import {
  capabilityNames,
  incidentKinds,
  type Capability,
  type Ctx,
  type History,
  type Options,
} from "./registerTypes";
import "./Registers.css";

const order: Capability[] = ["visitors", "incidents", "inventory"];
const tabsOf = (o: Options): [string, string][] => [
  ...order
    .filter((c) => o.capabilities.includes(c))
    .map((c): [string, string] => [c, capabilityNames[c]]),
  ...(o.admin ? [["settings", "Настройка"] as [string, string]] : []),
  ...(o.family
    ? [["family", "Сообщения о происшествиях"] as [string, string]]
    : []),
];

function Settings({ ctx }: { ctx: Ctx }) {
  const { options } = ctx;
  const config = options.config!,
    eligible = options.eligible ?? [];
  const [matrix, setMatrix] = useState<Record<string, Capability[]>>(() =>
    Object.fromEntries(
      config.assignments
        .filter((a) => eligible.some((e) => e.id === a.accountId))
        .map((a) => [a.accountId, a.capabilities]),
    ),
  );
  const [family, setFamily] = useState(config.familySummaries),
    [reason, setReason] = useState(""),
    [history, setHistory] = useState<History | null>(null);
  const stale = new Map(
    config.assignments
      .filter((a) => a.current === false)
      .map((a) => [a.accountId, a]),
  );
  const former = config.assignments.filter(
    (a) => !eligible.some((e) => e.id === a.accountId),
  );
  const toggle = (id: string, cap: Capability, on: boolean) => {
    const rest = (matrix[id] ?? []).filter((c) => c !== cap);
    setMatrix({ ...matrix, [id]: on ? [...rest, cap] : rest });
    ctx.dirty(true);
  };
  async function save() {
    if (reason.trim().length < 3)
      throw new Error("Укажите причину изменения прав.");
    await ctx.mutate(
      "config",
      {
        version: config.version,
        reason: reason.trim(),
        familySummaries: family,
        assignments: eligible
          .filter((e) => matrix[e.id]?.length)
          .map((e) => ({
            accountId: e.id,
            capabilities: order.filter((c) => matrix[e.id].includes(c)),
          })),
      },
      "PUT",
    );
    ctx.dirty(false);
    await ctx.refresh();
    ctx.say("Права журналов сохранены.");
  }
  async function showHistory(more = false) {
    const r: History = await api(
      `registers/history/config/config${more && history?.nextBefore ? "?before=" + history.nextBefore : ""}`,
      ctx.workspace,
    );
    setHistory(
      more && history ? { ...r, items: [...history.items, ...r.items] } : r,
    );
  }
  return (
    <section aria-label="Настройка журналов">
      <h3>Кто ведёт журналы</h3>
      <p>
        Отметьте журналы для каждого сотрудника. Право привязано к текущей роли
        и доступам учётной записи: если они изменятся, право перестанет
        действовать, пока вы не сохраните настройку снова.
      </p>
      <form
        className="rg-form"
        onSubmit={(e) => {
          e.preventDefault();
          void ctx.run(save);
        }}
      >
        <div className="rg-scroll">
          <table className="rg-table rg-matrix">
            <thead>
              <tr>
                <th>Сотрудник</th>
                {order.map((c) => (
                  <th key={c}>{capabilityNames[c]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {eligible.map((e) => (
                <tr key={e.id} className={stale.has(e.id) ? "rg-warn" : ""}>
                  <td data-label="Сотрудник">
                    {e.name}
                    <small>
                      {" "}
                      · {roleNames[e.role as keyof typeof roleNames] ?? e.role}
                    </small>
                    {stale.has(e.id) && (
                      <strong className="rg-note">
                        Права устарели — сохраните заново или снимите
                      </strong>
                    )}
                  </td>
                  {order.map((c) => (
                    <td key={c} data-label={capabilityNames[c]}>
                      <input
                        type="checkbox"
                        aria-label={`${e.name}: ${capabilityNames[c]}`}
                        checked={Boolean(matrix[e.id]?.includes(c))}
                        onChange={(ev) => toggle(e.id, c, ev.target.checked)}
                      />
                    </td>
                  ))}
                </tr>
              ))}
              {former.map((a) => (
                <tr key={a.accountId} className="rg-warn">
                  <td colSpan={4}>
                    {a.name}
                    <strong className="rg-note">
                      Учётная запись больше не подходит: права будут сняты при
                      сохранении.
                    </strong>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!eligible.length && (
          <p>В этом пространстве нет подходящих сотрудников.</p>
        )}
        <label className="rg-check">
          <input
            type="checkbox"
            checked={family}
            onChange={(e) => {
              setFamily(e.target.checked);
              ctx.dirty(true);
            }}
          />
          Показывать родителям опубликованные сводки о происшествиях
        </label>
        <p>
          По умолчанию выключено. Родитель видит только краткий текст, который
          администрация опубликовала, и только про своего ребёнка.
        </p>
        <label>
          Причина изменения
          <input
            required
            minLength={3}
            maxLength={500}
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              ctx.dirty(true);
            }}
          />
        </label>
        <div className="rg-actions">
          <button>Сохранить права</button>
          <button
            type="button"
            onClick={() => void ctx.run(() => showHistory())}
          >
            История изменений
          </button>
        </div>
      </form>
      {history && (
        <HistoryList
          history={history}
          onMore={() => void ctx.run(() => showHistory(true))}
        />
      )}
    </section>
  );
}

type Summary = {
  number: string;
  occurredOn: string;
  kind: string;
  summary: string;
  publishedAt: string;
  children: string[];
};
function Family({ ctx }: { ctx: Ctx }) {
  const [data, setData] = useState<{
    items: Summary[];
    enabled: boolean;
  } | null>(null);
  useEffect(() => {
    void ctx.run(async () =>
      setData(await api("registers/family/incidents", ctx.workspace)),
    );
  }, [ctx.workspace]);
  if (!data) return null;
  if (!data.enabled) return <p>Школа не публикует сведения о происшествиях.</p>;
  return (
    <section aria-label="Сообщения о происшествиях">
      <h3>Сообщения о происшествиях</h3>
      <p>Школа публикует здесь краткие сведения только о вашем ребёнке.</p>
      {!data.items.length && <p>Опубликованных сообщений нет.</p>}
      <ul className="rg-cards">
        {data.items.map((s) => (
          <li key={s.number} className="rg-card">
            <strong>
              № {s.number} · {incidentKinds[s.kind] || s.kind}
            </strong>
            <span>
              {s.occurredOn} · {s.children.join(", ")}
            </span>
            <p className="rg-prose">{s.summary}</p>
            <small>Опубликовано {displayTime(s.publishedAt)}</small>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Registers({
  user,
  initialWorkspace,
  onClose,
}: {
  user: Account;
  initialWorkspace: string;
  onClose: () => void;
}) {
  const spaces =
    user.role === "admin"
      ? ["school", "demo"]
      : [
          ...new Set([
            ...user.workspaces,
            ...user.grants.map((g) => g.workspace),
          ]),
        ];
  const [workspace, setWorkspace] = useState(
    spaces.includes(initialWorkspace)
      ? initialWorkspace
      : spaces[0] || "school",
  );
  const [options, setOptions] = useState<Options | null>(null),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(0),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [tab, setTab] = useState("");
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    unsaved = useRef(false),
    replay = useRef({ key: "", id: "" });
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  const leave = () =>
    !unsaved.current ||
    window.confirm(
      "Есть несохранённые изменения. Отбросить их и перейти дальше?",
    );
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (unsaved.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, []);
  function change(fn: () => void) {
    if (leave()) {
      unsaved.current = false;
      fn();
    }
  }
  async function load() {
    const n = ++sequence.current;
    const o: Options = await api("registers/options", workspace);
    if (n !== sequence.current) return;
    setOptions(o);
    const tabs = tabsOf(o);
    setTab((t) => (tabs.some(([id]) => id === t) ? t : (tabs[0]?.[0] ?? "")));
  }
  // A background run (a reload after a filter changed) leaves the form enabled:
  // disabling it would take the focus away from the field the person is typing in.
  async function run(task: () => Promise<void>, background = false) {
    if (background) setLoading((n) => n + 1);
    else setBusy(true);
    setError("");
    if (!background) setNotice("");
    try {
      await task();
    } catch (e: any) {
      setError(e.message || "Не удалось выполнить действие.");
      if ([401, 403, 404].includes(e.status)) {
        unsaved.current = false;
        setOptions(null);
      }
    } finally {
      if (background) setLoading((n) => n - 1);
      else setBusy(false);
    }
  }
  useEffect(() => {
    unsaved.current = false;
    setOptions(null);
    void run(load);
    return () => {
      ++sequence.current;
    };
  }, [workspace]);
  // One request id per identical payload: a double click or a retry replays instead of repeating.
  async function mutate(path: string, body: object, method = "POST") {
    const key = JSON.stringify([workspace, path, body, method]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const result = await api("registers/" + path, workspace, method, {
      ...body,
      requestId: replay.current.id,
    });
    replay.current = { key: "", id: "" };
    return result;
  }
  const ctx: Ctx | null = options && {
    user,
    workspace,
    options,
    run,
    mutate,
    dirty: (value) => {
      unsaved.current = value;
    },
    say: setNotice,
    refresh: load,
  };
  const tabs = options ? tabsOf(options) : [];
  return (
    <dialog
      className="registers"
      ref={dialog}
      aria-labelledby="registers-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && leave()) onClose();
      }}
    >
      <header className="rg-noprint">
        <div>
          <h2 id="registers-title">
            {user.role === "parent"
              ? "Сообщения о происшествиях"
              : "Журналы регистрации"}
          </h2>
          <p>Посетители, происшествия и школьный инвентарь</p>
        </div>
        <button
          disabled={busy}
          onClick={() => {
            if (leave()) onClose();
          }}
        >
          Закрыть
        </button>
      </header>
      {error && (
        <p role="alert" className="rg-noprint">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="rg-noprint">
          {notice}
        </p>
      )}
      {(busy || loading > 0) && (
        <p role="status" className="rg-noprint">
          Загрузка…
        </p>
      )}
      <fieldset disabled={busy}>
        <div className="rg-controls rg-noprint">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => change(() => setWorkspace(e.target.value))}
            >
              {spaces.map((s) => (
                <option key={s} value={s}>
                  {s === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => change(() => void run(load))}>Обновить</button>
        </div>
        {options && !tabs.length && (
          <p>
            {user.role === "parent"
              ? "Школа не публикует сведения о происшествиях для вашей учётной записи."
              : "Журналы регистрации ведут назначенные сотрудники. Обратитесь к администрации."}
          </p>
        )}
        {tabs.length > 1 && (
          <nav className="rg-tabs rg-noprint" aria-label="Разделы журналов">
            {tabs.map(([id, label]) => (
              <button
                key={id}
                aria-pressed={tab === id}
                onClick={() => change(() => setTab(id))}
              >
                {label}
              </button>
            ))}
          </nav>
        )}
        {ctx && tab === "visitors" && (
          <RegisterVisitors key={workspace} ctx={ctx} />
        )}
        {ctx && tab === "incidents" && (
          <RegisterIncidents key={workspace} ctx={ctx} />
        )}
        {ctx && tab === "inventory" && (
          <RegisterInventory key={workspace} ctx={ctx} />
        )}
        {ctx && tab === "settings" && options?.config && (
          <Settings key={workspace + options.config.version} ctx={ctx} />
        )}
        {ctx && tab === "family" && <Family key={workspace} ctx={ctx} />}
      </fieldset>
    </dialog>
  );
}

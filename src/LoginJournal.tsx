import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { roleNames, type Role } from "./auth";
import {
  BROWSER_NAMES,
  stampLabel,
  type LoginPage,
  type LoginRow,
} from "./safety";
import {
  LOGIN_TABS,
  accountAfterTabChange,
  accountsForTab,
  exportQuery,
  listQuery,
  nextTabIndex,
  tabColumns,
  type LoginAccount,
  type LoginTab,
} from "./loginJournalRules";
import "./Safety.css";
import "./LoginJournal.css";

export default function LoginJournal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<LoginTab>(""),
    [outcome, setOutcome] = useState(""),
    [account, setAccount] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [rows, setRows] = useState<LoginRow[]>([]),
    [next, setNext] = useState<number | null>(null),
    [days, setDays] = useState(180),
    [people, setPeople] = useState<LoginAccount[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [busyMore, setBusyMore] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  // Bumped whenever the filters change: a page that answers for an older generation is dropped.
  const generation = useRef(0);
  const filters = { tab, outcome, account, start, end };
  const columns = tabColumns(tab);
  const offered = useMemo(() => accountsForTab(tab, people), [tab, people]);
  useEffect(() => {
    dialog.current?.showModal();
    tabButtons.current[0]?.focus();
    return () => dialog.current?.close();
  }, []);
  useEffect(() => {
    api("access/users", "school")
      .then((list: LoginAccount[]) => setPeople(list))
      .catch(() => setPeople([]));
  }, []);
  useEffect(() => {
    const mine = ++generation.current;
    setLoading(true);
    setBusyMore(false);
    setRows([]);
    setNext(null);
    setError("");
    api("access/logins?" + listQuery(filters), "school")
      .then((page: LoginPage) => {
        if (generation.current !== mine) return;
        setRows(page.items);
        setNext(page.nextBefore);
        setDays(page.retentionDays);
      })
      .catch((e) => {
        if (generation.current !== mine) return;
        setError(e.message);
      })
      .finally(() => {
        if (generation.current === mine) setLoading(false);
      });
  }, [tab, outcome, account, start, end]);
  const more = () => {
    const mine = generation.current;
    setBusyMore(true);
    setError("");
    api("access/logins?" + listQuery(filters, next), "school")
      .then((page: LoginPage) => {
        if (generation.current !== mine) return;
        setRows((old) => [...old, ...page.items]);
        setNext(page.nextBefore);
      })
      .catch((e) => {
        if (generation.current === mine) setError(e.message);
      })
      .finally(() => {
        if (generation.current === mine) setBusyMore(false);
      });
  };
  const pickTab = (id: LoginTab) => {
    setTab(id);
    setAccount((a) => accountAfterTabChange(a, id, people));
  };
  const keyTab = (e: React.KeyboardEvent, index: number) => {
    const to = nextTabIndex(index, e.key);
    if (to === null) return;
    e.preventDefault();
    pickTab(LOGIN_TABS[to].id);
    tabButtons.current[to]?.focus();
  };
  const exportUrl = `/api/access/logins/export?${exportQuery(filters)}`;
  return (
    <dialog
      ref={dialog}
      className="sf-dialog lj-dialog"
      aria-labelledby="login-journal-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header className="lj-head">
        <h2 id="login-journal-title">Журнал входов</h2>
        <button onClick={onClose}>Закрыть</button>
      </header>
      <details className="lj-about">
        <summary>Что хранится</summary>
        <p>
          Успешные и неудачные входы. Пароли, токены и набранные неизвестные
          логины не сохраняются; адрес хранится до сети, записи хранятся {days}{" "}
          дней
        </p>
      </details>
      <div className="lj-tabs" role="tablist" aria-label="Кто входил">
        {LOGIN_TABS.map((t, i) => (
          <button
            key={t.id || "all"}
            ref={(el) => {
              tabButtons.current[i] = el;
            }}
            role="tab"
            id={`lj-tab-${t.id || "all"}`}
            aria-selected={tab === t.id}
            aria-controls="lj-panel"
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => pickTab(t.id)}
            onKeyDown={(e) => keyTab(e, i)}
          >
            {t.title}
          </button>
        ))}
      </div>
      <div className="lj-filters">
        <label>
          Результат
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">Все</option>
            <option value="success">Вход</option>
            <option value="failed">Неудачная попытка</option>
          </select>
        </label>
        {columns.account && (
          <label>
            Учётная запись
            <select
              value={account}
              onChange={(e) => setAccount(e.target.value)}
            >
              <option value="">Все</option>
              {offered.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          С
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          По
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <a className="lj-csv" href={exportUrl}>
          Скачать CSV
        </a>
      </div>
      {error && (
        <p role="alert" className="sf-error lj-error">
          {error}
        </p>
      )}
      <div
        className="lj-area"
        role="tabpanel"
        id="lj-panel"
        aria-labelledby={`lj-tab-${tab || "all"}`}
      >
        {loading && (
          <p role="status" className="lj-state">
            Загружаю…
          </p>
        )}
        {!loading && !rows.length && !error && (
          <p className="lj-state">Записей нет</p>
        )}
        {!!rows.length && (
          <table className="lj-table">
            <thead>
              <tr>
                <th className="lj-time">Время</th>
                <th className="lj-result">Результат</th>
                {columns.account && (
                  <th className="lj-account">Учётная запись</th>
                )}
                {columns.role && <th className="lj-role">Роль</th>}
                <th className="lj-net">Сеть</th>
                <th className="lj-browser">Браузер</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const who = r.accountName
                  ? `${r.accountName} · ${r.accountLogin}`
                  : "Неизвестный логин";
                return (
                  <tr key={r.seq}>
                    <td className="lj-time">{stampLabel(r.created)}</td>
                    <td className="lj-result">
                      <span
                        className={
                          "lj-badge" + (r.outcome === "failed" ? " lj-bad" : "")
                        }
                      >
                        {r.outcome === "success" ? "Вход" : "Неудачная попытка"}
                      </span>
                    </td>
                    {columns.account && (
                      <td className="lj-account" title={who}>
                        {r.accountName ? (
                          <>
                            {r.accountName}
                            <small className="sf-muted">
                              {" "}
                              · {r.accountLogin}
                            </small>
                          </>
                        ) : (
                          "Неизвестный логин"
                        )}
                      </td>
                    )}
                    {columns.role && (
                      <td className="lj-role">
                        {r.role ? (roleNames[r.role as Role] ?? r.role) : "—"}
                      </td>
                    )}
                    <td className="lj-net">{r.address || "—"}</td>
                    <td className="lj-browser">
                      {BROWSER_NAMES[r.browser] ?? BROWSER_NAMES.other}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <div className="lj-foot">
        <span className="sf-muted">
          {rows.length ? `Показано записей: ${rows.length}` : ""}
        </span>
        {next !== null && (
          <button onClick={more} disabled={busyMore}>
            Показать более ранние
          </button>
        )}
      </div>
    </dialog>
  );
}

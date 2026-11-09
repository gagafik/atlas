import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { roleNames, type Role } from "./auth";
import {
  BROWSER_NAMES,
  stampLabel,
  type LoginPage,
  type LoginRow,
} from "./safety";
import "./Safety.css";

export default function LoginJournal({ onClose }: { onClose: () => void }) {
  const [outcome, setOutcome] = useState(""),
    [account, setAccount] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [rows, setRows] = useState<LoginRow[]>([]),
    [next, setNext] = useState<number | null>(null),
    [days, setDays] = useState(180),
    [people, setPeople] = useState<{ id: string; name: string }[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  useEffect(() => {
    api("access/users", "school")
      .then((list: { id: string; name: string }[]) => setPeople(list))
      .catch(() => setPeople([]));
  }, []);
  const filters = () => {
    const p = new URLSearchParams();
    if (outcome) p.set("outcome", outcome);
    if (account) p.set("account", account);
    if (start) p.set("start", start);
    if (end) p.set("end", end);
    return p;
  };
  useEffect(() => {
    let live = true;
    setLoading(true);
    api("access/logins?" + filters(), "school")
      .then((page: LoginPage) => {
        if (!live) return;
        setRows(page.items);
        setNext(page.nextBefore);
        setDays(page.retentionDays);
        setError("");
      })
      .catch((e) => {
        if (!live) return;
        setRows([]);
        setNext(null);
        setError(e.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [outcome, account, start, end]);
  const more = () => {
    const p = filters();
    p.set("before", String(next));
    api("access/logins?" + p, "school")
      .then((page: LoginPage) => {
        setRows((old) => [...old, ...page.items]);
        setNext(page.nextBefore);
      })
      .catch((e) => setError(e.message));
  };
  const exportUrl = `/api/access/logins/export?${filters()}`;
  return (
    <dialog
      ref={dialog}
      className="sf-dialog"
      aria-labelledby="login-journal-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <div>
          <h2 id="login-journal-title">Журнал входов</h2>
          <p>
            Успешные и неудачные входы. Пароли, токены и набранные неизвестные
            логины не сохраняются; адрес хранится до сети, записи хранятся{" "}
            {days} дней
          </p>
        </div>
        <button onClick={onClose}>Закрыть</button>
      </header>
      {error && (
        <p role="alert" className="sf-error">
          {error}
        </p>
      )}
      <div className="sf-controls">
        <label>
          Результат
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">Все</option>
            <option value="success">Вход</option>
            <option value="failed">Неудачная попытка</option>
          </select>
        </label>
        <label>
          Учётная запись
          <select value={account} onChange={(e) => setAccount(e.target.value)}>
            <option value="">Все</option>
            <option value="unknown">Неизвестный логин</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
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
        <div className="sf-actions">
          <a href={exportUrl}>Скачать CSV</a>
        </div>
      </div>
      {!loading && !rows.length && !error && <p>Записей нет</p>}
      {!!rows.length && (
        <div className="sf-wide">
          <table className="sf-table">
            <thead>
              <tr>
                <th>Время</th>
                <th>Результат</th>
                <th>Учётная запись</th>
                <th>Роль</th>
                <th>Сеть</th>
                <th>Браузер</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.seq}>
                  <td data-label="Время">{stampLabel(r.created)}</td>
                  <td
                    data-label="Результат"
                    className={r.outcome === "failed" ? "sf-failed" : ""}
                  >
                    {r.outcome === "success" ? "Вход" : "Неудачная попытка"}
                  </td>
                  <td data-label="Учётная запись">
                    {r.accountName ? (
                      <>
                        {r.accountName}
                        <small className="sf-muted"> · {r.accountLogin}</small>
                      </>
                    ) : (
                      "Неизвестный логин"
                    )}
                  </td>
                  <td data-label="Роль">
                    {r.role ? (roleNames[r.role as Role] ?? r.role) : "—"}
                  </td>
                  <td data-label="Сеть">{r.address || "—"}</td>
                  <td data-label="Браузер">
                    {BROWSER_NAMES[r.browser] ?? BROWSER_NAMES.other}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {next !== null && <button onClick={more}>Показать более ранние</button>}
    </dialog>
  );
}

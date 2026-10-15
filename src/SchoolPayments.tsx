import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import {
  formatMoney,
  parseMoney,
  paymentKinds,
  summaryLabels,
} from "./payments";
import {
  Amount,
  Reason,
  financeSubmit,
  ReceiptEditor,
  PlanEditor,
  PlanIssueEditor,
  BankImport,
  FinanceSettings,
} from "./PaymentEditors";
import "./SchoolPayments.css";

type PaymentsProps = {
  user: Account;
  initialWorkspace: string;
  onClose: () => void;
};

export default function SchoolPayments(props: PaymentsProps) {
  const { user, initialWorkspace } = props;
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
  return (
    <PaymentsWorkspace
      key={workspace}
      {...props}
      workspace={workspace}
      spaces={spaces}
      onWorkspaceChange={setWorkspace}
    />
  );
}

function PaymentsWorkspace({
  onClose,
  workspace,
  spaces,
  onWorkspaceChange,
}: PaymentsProps & {
  workspace: string;
  spaces: string[];
  onWorkspaceChange: (workspace: string) => void;
}) {
  const [options, setOptions] = useState<any>(null),
    [accounts, setAccounts] = useState<any>({ items: [] }),
    [account, setAccount] = useState<any>(null);
  const [tab, setTab] = useState("accounts"),
    [action, setAction] = useState(""),
    [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0);
  const [plans, setPlans] = useState<any>({ items: [] }),
    [selectedPlan, setSelectedPlan] = useState<any>(null),
    [newPlan, setNewPlan] = useState(false),
    [archived, setArchived] = useState(false);
  const [report, setReport] = useState<any>(null),
    [through, setThrough] = useState(""),
    [periods, setPeriods] = useState<any>({ items: [] }),
    [closePreview, setClosePreview] = useState<any>(null);
  const [history, setHistory] = useState<any>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [dirty, setDirty] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  const money = (v: string | number) =>
    formatMoney(
      v,
      options?.config.decimals ?? 2,
      options?.config.currency || "KZT",
    );
  const leave = () =>
    !dirty ||
    window.confirm("Есть несохранённая форма. Продолжить без её сохранения?");
  const endpoint = (part: string) =>
    `/api/payments/${part}${part.includes("?") ? "&" : "?"}workspace=${encodeURIComponent(workspace)}`;
  const read = (part: string) => api("payments/" + part, workspace);
  async function refresh(
    id: string | null = account?.id || null,
    start = offset,
    search = query,
  ) {
    const token = ++sequence.current;
    try {
      const [opts, list, detail] = await Promise.all([
        read("options"),
        read(`accounts?offset=${start}&q=${encodeURIComponent(search)}`),
        id ? read("accounts/" + id) : Promise.resolve(null),
      ]);
      if (token !== sequence.current) return;
      setOptions(opts);
      setAccounts(list);
      setAccount(detail);
      setOffset(start);
      setThrough((old) => old || opts.today);
    } catch (e: any) {
      if (token === sequence.current) {
        setOptions(null);
        setAccounts({ items: [] });
        setAccount(null);
        setError(e.message);
      }
    }
  }
  async function aux() {
    try {
      if (tab === "plans") setPlans(await read(`plans?archived=${archived}`));
      if (tab === "reports") {
        const [r, p] = await Promise.all([
          read("report" + (through ? `?through=${through}` : "")),
          read("periods"),
        ]);
        setReport(r);
        setPeriods(p);
      }
    } catch (e: any) {
      setError(e.message);
      setPlans({ items: [] });
      setReport(null);
      setPeriods({ items: [] });
    }
  }
  useEffect(() => {
    dialog.current?.showModal();
    return () => {
      ++sequence.current;
    };
  }, []);
  useEffect(() => {
    void refresh(null, 0, "");
  }, [workspace]);
  useEffect(() => {
    if (options?.canManage) void aux();
  }, [tab, workspace, archived, options?.canManage]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty || busy) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);
  async function run(part: string, data: unknown, method = "POST") {
    if (busy) throw new Error("Дождитесь завершения текущего действия.");
    const key = JSON.stringify([workspace, part, method, data]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api("payments/" + part, workspace, method, {
        ...(data as object),
        requestId: replay.current.id,
      });
      if (!part.endsWith("/preview")) {
        replay.current = { key: "", id: "" };
        setDirty(false);
        setNotice("Операция сохранена.");
        setHistory(null);
        if (part === "plans") setNewPlan(false);
        await refresh();
        await aux();
      }
      return result;
    } catch (e: any) {
      setError(e.message);
      if ([401, 403, 404].includes(e.status)) {
        setAccount(null);
        setOptions(null);
        setReport(null);
        setHistory(null);
      }
      throw e;
    } finally {
      setBusy(false);
    }
  }
  async function openAccount(id: string) {
    if (!id || !leave()) return;
    setDirty(false);
    setAction("");
    setHistory(null);
    setError("");
    setBusy(true);
    try {
      await refresh(id);
    } finally {
      setBusy(false);
    }
  }
  function changeTab(next: string) {
    if (!leave()) return;
    setTab(next);
    setDirty(false);
    setError("");
    setNotice("");
    setClosePreview(null);
  }
  const common = { options, run, money, error: setError };
  function summary(data: any) {
    return (
      <div className="finance-summary">
        {[
          "balance",
          "overdue",
          "unallocated",
          "charged",
          "received",
          "outstanding",
        ].map((key) => (
          <div key={key}>
            <span>{summaryLabels[key]}</span>
            <strong>{money(data[key])}</strong>
          </div>
        ))}
      </div>
    );
  }
  async function loadHistory(before?: number) {
    try {
      const result = await read(
        `accounts/${account.id}/history${before ? `?before=${before}` : ""}`,
      );
      setHistory((old: any) =>
        before ? { ...result, items: [...old.items, ...result.items] } : result,
      );
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function pageItems(start: number) {
    if (!leave()) return;
    setDirty(false);
    setAction("");
    try {
      setAccount(await read(`accounts/${account.id}?itemOffset=${start}`));
    } catch (e: any) {
      setAccount(null);
      setError(e.message);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="school-payments"
      aria-labelledby="payments-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && leave()) onClose();
      }}
    >
      <header>
        <div>
          <span className="finance-eyebrow">АТЛАС · ФИНАНСЫ</span>
          <h2 id="payments-title">Школьные платежи</h2>
          <p>Начисления, оплаты и понятная история по каждому ребёнку.</p>
        </div>
        <button
          onClick={() => {
            if (leave()) onClose();
          }}
          disabled={busy}
        >
          Закрыть
        </button>
      </header>
      <fieldset
        disabled={busy}
        className="finance-body"
        onChangeCapture={(e) => {
          if (
            (e.target as HTMLElement).closest("form:not([data-finance-search])")
          )
            setDirty(true);
        }}
      >
        <div className="finance-toolbar">
          <label>
            Пространство
            <select
              value={workspace}
              onChange={(e) => {
                if (leave()) {
                  onWorkspaceChange(e.target.value);
                }
              }}
            >
              {spaces.map((w) => (
                <option key={w} value={w}>
                  {w === "school" ? "Школа" : "Демонстрация"}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              if (leave()) {
                setDirty(false);
                void refresh();
                void aux();
              }
            }}
          >
            Обновить
          </button>
        </div>
        {error && (
          <p role="alert" className="finance-error">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="finance-notice">
            {notice}
          </p>
        )}
        {busy && <p role="status">Выполняется операция…</p>}
        {!options && !error && <p>Загружаем финансовый кабинет…</p>}
        {options && (
          <>
            <p className="finance-muted">
              Валюта: {options.config.currency}.{" "}
              {options.config.closedThrough
                ? `Учёт закрыт по ${options.config.closedThrough}.`
                : "Закрытых периодов пока нет."}
            </p>
            <nav className="finance-tabs" aria-label="Разделы платежей">
              <button
                aria-pressed={tab === "accounts"}
                onClick={() => changeTab("accounts")}
              >
                Лицевые счета
              </button>
              {options.canManage && (
                <>
                  <button
                    aria-pressed={tab === "plans"}
                    onClick={() => changeTab("plans")}
                  >
                    Планы и скидки
                  </button>
                  <button
                    aria-pressed={tab === "import"}
                    onClick={() => changeTab("import")}
                  >
                    Сверка выписки
                  </button>
                  <button
                    aria-pressed={tab === "reports"}
                    onClick={() => changeTab("reports")}
                  >
                    Отчёты и закрытие
                  </button>
                </>
              )}
              {options.admin && (
                <button
                  aria-pressed={tab === "settings"}
                  onClick={() => changeTab("settings")}
                >
                  Доступ
                </button>
              )}
            </nav>
            {tab === "accounts" && (
              <>
                <section className="finance-card">
                  <h3>
                    {options.canManage ? "Счета учеников" : "Счета ваших детей"}
                  </h3>
                  {options.canManage && (
                    <label>
                      Открыть или создать лицевой счёт
                      <select
                        value={account?.id || ""}
                        onChange={(e) => void openAccount(e.target.value)}
                      >
                        <option value="">Выберите ребёнка</option>
                        {options.students.map((p: any) => (
                          <option key={p.id} value={p.id}>
                            {p.name} · {p.className}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <form
                    className="finance-toolbar"
                    data-finance-search="true"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (leave()) {
                        setDirty(false);
                        void refresh(null, 0, query);
                      }
                    }}
                  >
                    <label>
                      Поиск
                      <input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        maxLength={200}
                        placeholder="Имя или номер счёта"
                      />
                    </label>
                    <button>Найти</button>
                  </form>
                  <div className="finance-account-list">
                    {accounts.items.map((a: any) => (
                      <button
                        className={account?.id === a.id ? "selected" : ""}
                        key={a.id}
                        onClick={() => void openAccount(a.id)}
                      >
                        <strong>{a.name}</strong>
                        <span>
                          {a.code}
                          {!a.active && " · выбыл"}
                        </span>
                        <b>{money(a.balance)}</b>
                      </button>
                    ))}
                  </div>
                  {!accounts.items.length && (
                    <p>
                      {options.canManage
                        ? "Счетов пока нет. Выберите ребёнка и создайте начисление или назначьте плательщика."
                        : "Доступных счетов пока нет. Школа должна предоставить финансовый доступ к ребёнку."}
                    </p>
                  )}
                  <div className="finance-actions">
                    {offset > 0 && (
                      <button
                        onClick={() => {
                          if (leave()) {
                            setDirty(false);
                            void refresh(null, Math.max(0, offset - 30));
                          }
                        }}
                      >
                        Предыдущие счета
                      </button>
                    )}
                    {accounts.nextOffset != null && (
                      <button
                        onClick={() => {
                          if (leave()) {
                            setDirty(false);
                            void refresh(null, accounts.nextOffset);
                          }
                        }}
                      >
                        Следующие счета
                      </button>
                    )}
                  </div>
                </section>
                {account && (
                  <section className="finance-card">
                    <div className="finance-toolbar">
                      <div>
                        <h3>{account.name}</h3>
                        <p>{account.code || "Счёт ещё не создан"}</p>
                      </div>
                      {account.code && (
                        <a href={endpoint(`accounts/${account.id}/export`)}>
                          Скачать выписку CSV
                        </a>
                      )}
                    </div>
                    {summary(account.summary)}
                    <p className="finance-muted">
                      Положительный баланс — долг, отрицательный — переплата.
                      Свободная оплата уже учтена в балансе, но ещё не
                      распределена по взносам.
                    </p>
                    <div className="finance-scroll">
                      <table>
                        <caption>Начисления</caption>
                        <thead>
                          <tr>
                            <th>Назначение</th>
                            <th>Срок</th>
                            <th>Начислено</th>
                            <th>Погашено</th>
                            <th>Остаток</th>
                          </tr>
                        </thead>
                        <tbody>
                          {account.charges.map((c: any) => (
                            <tr key={c.id}>
                              <td>{c.title}</td>
                              <td>{c.due}</td>
                              <td>{money(c.total)}</td>
                              <td>{money(c.allocated)}</td>
                              <td>{money(c.outstanding)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="finance-scroll">
                      <table>
                        <caption>Поступления за вычетом возвратов</caption>
                        <thead>
                          <tr>
                            <th>Дата оплаты</th>
                            {account.canManage && <th>Документ</th>}
                            <th>Сумма</th>
                            <th>Свободно</th>
                          </tr>
                        </thead>
                        <tbody>
                          {account.credits.map((r: any) => (
                            <tr key={r.id}>
                              <td>{r.paid_date}</td>
                              {account.canManage && (
                                <td>
                                  {r.reference} · {r.payer}
                                </td>
                              )}
                              <td>{money(r.total)}</td>
                              <td>{money(r.available)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="finance-actions">
                      {account.itemOffset > 0 && (
                        <button
                          onClick={() =>
                            void pageItems(Math.max(0, account.itemOffset - 50))
                          }
                        >
                          Предыдущие взносы и оплаты
                        </button>
                      )}
                      {account.nextItemOffset != null && (
                        <button
                          onClick={() => void pageItems(account.nextItemOffset)}
                        >
                          Следующие взносы и оплаты
                        </button>
                      )}
                    </div>
                    {account.canManage && (
                      <>
                        <label>
                          Действие со счётом
                          <select
                            value={action}
                            onChange={(e) => {
                              if (leave()) {
                                setDirty(false);
                                setAction(e.target.value);
                                setHistory(null);
                              }
                            }}
                          >
                            <option value="">Выберите действие</option>
                            <option value="charge">
                              Разовое начисление / начальный долг
                            </option>
                            <option value="receipt">
                              Учесть оплату / перенести переплату
                            </option>
                            <option value="allocate">
                              Распределить оплату вручную
                            </option>
                            <option value="auto">
                              Распределить по ближайшим срокам
                            </option>
                            <option value="reverse">
                              Отменить распределение
                            </option>
                            <option value="adjust">
                              Изменить сумму начисления / отменить взнос
                            </option>
                            <option value="refund">
                              Учесть возврат / исправить ошибочное поступление
                            </option>
                            <option value="remind">
                              Напомнить о задолженности
                            </option>
                            <option value="history">Служебная история</option>
                            {options.admin && (
                              <option value="payers">
                                Назначить плательщиков
                              </option>
                            )}
                          </select>
                        </label>
                        <div key={`${account.id}:${account.version}:${action}`}>
                          {action === "receipt" && (
                            <ReceiptEditor {...common} account={account} />
                          )}
                          {action === "charge" && (
                            <form
                              className="finance-editor"
                              onSubmit={financeSubmit(async (d) => {
                                await run(`accounts/${account.id}/charges`, {
                                  version: account.version,
                                  title: d.get("title"),
                                  due: d.get("due"),
                                  amount: parseMoney(
                                    String(d.get("amount")),
                                    options.config.decimals,
                                  ),
                                  reason: d.get("reason"),
                                });
                              }, setError)}
                            >
                              <h4>Разовое начисление</h4>
                              <label>
                                Назначение (видно семье)
                                <input name="title" required maxLength={160} />
                              </label>
                              <label>
                                Оплатить до
                                <input
                                  type="date"
                                  name="due"
                                  required
                                  defaultValue={options.today}
                                />
                              </label>
                              <Amount />
                              <Reason />
                              <button>Создать начисление</button>
                            </form>
                          )}
                          {action === "adjust" && (
                            <form
                              className="finance-editor"
                              onSubmit={financeSubmit(async (d) => {
                                await run(
                                  `accounts/${account.id}/charges/${d.get("charge")}/adjust`,
                                  {
                                    version: account.version,
                                    amount: parseMoney(
                                      String(d.get("amount")),
                                      options.config.decimals,
                                    ),
                                    label: d.get("label"),
                                    reason: d.get("reason"),
                                  },
                                );
                              }, setError)}
                            >
                              <h4>Новая общая сумма начисления</h4>
                              <p>
                                Для полной отмены укажите 0. Уменьшение не может
                                затронуть уже распределённую оплату: сначала
                                отмените её распределение.
                              </p>
                              <label>
                                Начисление
                                <select name="charge" required>
                                  <option value="">Выберите взнос</option>
                                  {account.charges.map((c: any) => (
                                    <option value={c.id} key={c.id}>
                                      {c.title} · {money(c.total)}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              <Amount label="Новая общая сумма" />
                              <label>
                                Описание исправления (видно семье)
                                <input name="label" required maxLength={160} />
                              </label>
                              <Reason />
                              <button>Зафиксировать изменение</button>
                            </form>
                          )}
                          {action === "allocate" && (
                            <form
                              className="finance-editor"
                              onSubmit={financeSubmit(async (d) => {
                                await run(
                                  `accounts/${account.id}/allocations`,
                                  {
                                    version: account.version,
                                    chargeId: d.get("charge"),
                                    receiptId: d.get("receipt"),
                                    amount: parseMoney(
                                      String(d.get("amount")),
                                      options.config.decimals,
                                    ),
                                    reason: d.get("reason"),
                                  },
                                );
                              }, setError)}
                            >
                              <h4>Сопоставить оплату и взнос</h4>
                              <label>
                                Начисление
                                <select name="charge" required>
                                  {account.charges
                                    .filter(
                                      (c: any) => BigInt(c.outstanding) > 0n,
                                    )
                                    .map((c: any) => (
                                      <option value={c.id} key={c.id}>
                                        {c.title} · остаток{" "}
                                        {money(c.outstanding)}
                                      </option>
                                    ))}
                                </select>
                              </label>
                              <label>
                                Оплата
                                <select name="receipt" required>
                                  {account.credits
                                    .filter(
                                      (c: any) => BigInt(c.available) > 0n,
                                    )
                                    .map((c: any) => (
                                      <option value={c.id} key={c.id}>
                                        {c.reference} · свободно{" "}
                                        {money(c.available)}
                                      </option>
                                    ))}
                                </select>
                              </label>
                              <Amount />
                              <Reason />
                              <button>Распределить сумму</button>
                            </form>
                          )}
                          {action === "auto" && (
                            <form
                              className="finance-editor"
                              onSubmit={financeSubmit(async (d) => {
                                if (
                                  window.confirm(
                                    "Распределить свободные оплаты по взносам, начиная с ближайшего срока?",
                                  )
                                )
                                  await run(
                                    `accounts/${account.id}/auto-allocate`,
                                    {
                                      version: account.version,
                                      reason: d.get("reason"),
                                    },
                                  );
                              }, setError)}
                            >
                              <p>
                                Проверяются все взносы и оплаты счёта, включая
                                другие страницы. Сначала погашаются самые ранние
                                сроки.
                              </p>
                              <Reason />
                              <button>Распределить автоматически</button>
                            </form>
                          )}
                          {action === "reverse" && (
                            <form
                              className="finance-editor"
                              onSubmit={financeSubmit(async (d) => {
                                await run(
                                  `accounts/${account.id}/allocations/${d.get("allocation")}/reverse`,
                                  {
                                    version: account.version,
                                    reason: d.get("reason"),
                                  },
                                );
                              }, setError)}
                            >
                              <label>
                                Действующее распределение
                                <select name="allocation" required>
                                  <option value="">
                                    Выберите распределение
                                  </option>
                                  {account.allocations.map((a: any) => (
                                    <option value={a.id} key={a.id}>
                                      {a.book_date} · {money(a.amount)} ·{" "}
                                      {account.charges.find(
                                        (c: any) => c.id === a.charge_id,
                                      )?.title || "Взнос на другой странице"}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              <Reason />
                              <button>Отменить распределение</button>
                            </form>
                          )}
                          {action === "refund" && (
                            <form
                              className="finance-editor"
                              onSubmit={financeSubmit(async (d) => {
                                if (
                                  window.confirm(
                                    "Подтвердить запись возврата или исправления? Реальный денежный перевод выполняется вне Атласа.",
                                  )
                                )
                                  await run(
                                    `accounts/${account.id}/receipts/${d.get("receipt")}/refund`,
                                    {
                                      version: account.version,
                                      amount: parseMoney(
                                        String(d.get("amount")),
                                        options.config.decimals,
                                      ),
                                      kind: d.get("kind"),
                                      reference: d.get("reference"),
                                      reason: d.get("reason"),
                                    },
                                  );
                              }, setError)}
                            >
                              <h4>Учёт возврата или исправления</h4>
                              <label>
                                Оплата
                                <select name="receipt" required>
                                  {account.credits
                                    .filter(
                                      (c: any) => BigInt(c.available) > 0n,
                                    )
                                    .map((c: any) => (
                                      <option value={c.id} key={c.id}>
                                        {c.reference} · свободно{" "}
                                        {money(c.available)}
                                      </option>
                                    ))}
                                </select>
                              </label>
                              <label>
                                Вид
                                <select name="kind">
                                  <option value="refund">
                                    Деньги возвращены плательщику
                                  </option>
                                  <option value="correction">
                                    Поступление было внесено ошибочно
                                  </option>
                                </select>
                              </label>
                              <Amount />
                              <label>
                                Документ возврата или исправления
                                <input
                                  name="reference"
                                  required
                                  maxLength={100}
                                />
                              </label>
                              <Reason />
                              <button>Зафиксировать операцию</button>
                            </form>
                          )}
                          {action === "remind" && (
                            <form
                              className="finance-editor"
                              onSubmit={financeSubmit(async (d) => {
                                await run(`accounts/${account.id}/remind`, {
                                  version: account.version,
                                  reason: d.get("reason"),
                                });
                              }, setError)}
                            >
                              <p>
                                Напоминание в Атласе получают только родители с
                                текущим финансовым доступом. Сумма не попадает в
                                текст уведомления. Не чаще раза в день.
                              </p>
                              <Reason />
                              <button>Отправить напоминание</button>
                            </form>
                          )}
                          {action === "payers" && (
                            <form
                              className="finance-editor"
                              onSubmit={financeSubmit(async (d) => {
                                await run(
                                  `accounts/${account.id}/payers`,
                                  {
                                    version: account.version,
                                    parentIds: d.getAll("parents"),
                                    reason: d.get("reason"),
                                  },
                                  "PUT",
                                );
                              }, setError)}
                            >
                              <h4>Назначенные плательщики</h4>
                              <p>
                                Этот список применяется при политике «Только
                                назначенным плательщикам».
                              </p>
                              {options.parents
                                .filter((p: any) =>
                                  p.studentIds.includes(account.id),
                                )
                                .map((p: any) => (
                                  <label className="finance-check" key={p.id}>
                                    <input
                                      name="parents"
                                      type="checkbox"
                                      value={p.id}
                                      defaultChecked={account.payerIds.includes(
                                        p.id,
                                      )}
                                    />
                                    {p.name}
                                  </label>
                                ))}
                              <Reason />
                              <button>Сохранить плательщиков</button>
                            </form>
                          )}
                        </div>
                        {action === "history" && (
                          <section>
                            <button onClick={() => void loadHistory()}>
                              Загрузить историю
                            </button>
                            {history?.items.map((h: any) => (
                              <details key={h.seq}>
                                <summary>
                                  {h.created.slice(0, 16).replace("T", " ")} ·{" "}
                                  {h.actor_name} ·{" "}
                                  {(
                                    {
                                      charge: "Начисление",
                                      receipt: "Поступление",
                                      adjusted: "Изменение начисления",
                                      refund: "Возврат",
                                      correction: "Исправление поступления",
                                      allocated: "Распределение оплаты",
                                      auto_allocated: "Распределение по срокам",
                                      allocation_reversed:
                                        "Отмена распределения",
                                      payers: "Изменение плательщиков",
                                      reminded: "Напоминание",
                                      plan_charged: "Начисление по плану",
                                    } as Record<string, string>
                                  )[h.action] || "Изменение счёта"}
                                </summary>
                                <p>{h.reason}</p>
                                {h.document.amount != null && (
                                  <p>Сумма: {money(h.document.amount)}</p>
                                )}
                                {h.document.reference && (
                                  <p>Документ: {h.document.reference}</p>
                                )}
                                {h.action === "adjusted" && (
                                  <p>
                                    Было {money(h.document.before)}, стало{" "}
                                    {money(h.document.after)}.
                                  </p>
                                )}
                                {h.document.title && <p>{h.document.title}</p>}
                              </details>
                            ))}
                            {history?.nextBefore && (
                              <button
                                onClick={() =>
                                  void loadHistory(history.nextBefore)
                                }
                              >
                                Ещё история
                              </button>
                            )}
                          </section>
                        )}
                      </>
                    )}
                    <h4>Движения по счёту</h4>
                    <div className="finance-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Дата учёта</th>
                            <th>Операция</th>
                            <th>Описание</th>
                            <th>Изменение баланса</th>
                          </tr>
                        </thead>
                        <tbody>
                          {account.entries.map((e: any) => (
                            <tr key={e.id}>
                              <td>{e.book_date}</td>
                              <td>{paymentKinds[e.kind] || e.kind}</td>
                              <td>{e.label}</td>
                              <td>{money(e.amount)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {account.nextBefore && (
                      <button
                        onClick={async () => {
                          try {
                            const page = await read(
                              `accounts/${account.id}?before=${account.nextBefore}&itemOffset=${account.itemOffset}`,
                            );
                            setAccount({
                              ...page,
                              entries: [...account.entries, ...page.entries],
                            });
                          } catch (e: any) {
                            setError(e.message);
                          }
                        }}
                      >
                        Показать более ранние движения
                      </button>
                    )}
                  </section>
                )}
              </>
            )}
            {tab === "plans" && options.canManage && (
              <>
                <div className="finance-toolbar">
                  <button
                    onClick={() => {
                      if (leave()) {
                        setDirty(false);
                        setNewPlan(true);
                        setSelectedPlan(null);
                      }
                    }}
                  >
                    Новый план
                  </button>
                  <label className="finance-check">
                    <input
                      type="checkbox"
                      checked={archived}
                      onChange={(e) => {
                        if (leave()) {
                          setDirty(false);
                          setArchived(e.target.checked);
                          setSelectedPlan(null);
                          setNewPlan(false);
                        }
                      }}
                    />
                    Архив
                  </label>
                </div>
                <div className="finance-account-list">
                  {plans.items.map((p: any) => (
                    <button
                      key={p.id}
                      onClick={() => {
                        if (leave()) {
                          setDirty(false);
                          setSelectedPlan(p);
                          setNewPlan(false);
                        }
                      }}
                    >
                      <strong>{p.data.title}</strong>
                      <span>
                        {p.data.installments.length} взносов ·{" "}
                        {p.archived ? "архив" : "действует"}
                      </span>
                    </button>
                  ))}
                </div>
                {plans.nextBefore && (
                  <button
                    onClick={async () => {
                      try {
                        const page = await read(
                          `plans?before=${plans.nextBefore}&archived=${archived}`,
                        );
                        setPlans({
                          ...page,
                          items: [...plans.items, ...page.items],
                        });
                      } catch (e: any) {
                        setError(e.message);
                      }
                    }}
                  >
                    Ещё планы
                  </button>
                )}
                {newPlan && <PlanEditor {...common} />}
                {selectedPlan && !selectedPlan.archived && (
                  <>
                    <PlanIssueEditor
                      {...common}
                      key={selectedPlan.id}
                      plan={selectedPlan}
                    />
                    <button
                      onClick={async () => {
                        if (!leave()) return;
                        const reason = window.prompt(
                          "Основание архивации плана",
                        );
                        if (reason)
                          try {
                            await run(`plans/${selectedPlan.id}/archive`, {
                              version: selectedPlan.version,
                              reason,
                            });
                            setSelectedPlan(null);
                          } catch {
                            /* error displayed */
                          }
                      }}
                    >
                      Перенести план в архив
                    </button>
                  </>
                )}
                {selectedPlan?.archived && (
                  <p>
                    План в архиве. Его начисления и история сохраняются в
                    лицевых счетах.
                  </p>
                )}
              </>
            )}
            {tab === "import" && options.canManage && (
              <BankImport {...common} />
            )}
            {tab === "settings" && options.admin && (
              <FinanceSettings {...common} key={options.config.version} />
            )}
            {tab === "reports" && options.canManage && (
              <>
                <section className="finance-card">
                  <h3>Сводка по лицевым счетам</h3>
                  <div className="finance-toolbar">
                    <label>
                      По дату
                      <input
                        type="date"
                        value={through}
                        max={options.today}
                        onChange={(e) => {
                          setThrough(e.target.value);
                          setClosePreview(null);
                        }}
                      />
                    </label>
                    <button onClick={() => void aux()}>Показать отчёт</button>
                    <a href={endpoint(`report/export?through=${through}`)}>
                      Скачать сводку CSV
                    </a>
                  </div>
                  {report && (
                    <>
                      <p>
                        Отчёт по {report.through}. Денежные движения отражаются
                        в день внесения; дата банковской оплаты хранится
                        отдельно.
                      </p>
                      {summary(report.totals)}
                      <div className="finance-scroll">
                        <table>
                          <thead>
                            <tr>
                              <th>Ребёнок</th>
                              <th>Счёт</th>
                              <th>Баланс</th>
                              <th>Просрочено</th>
                              <th>Свободная оплата</th>
                            </tr>
                          </thead>
                          <tbody>
                            {report.items.map((a: any) => (
                              <tr key={a.id}>
                                <td>{a.name}</td>
                                <td>{a.code}</td>
                                <td>{money(a.balance)}</td>
                                <td>{money(a.overdue)}</td>
                                <td>{money(a.unallocated)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {report.nextOffset != null && (
                        <button
                          onClick={async () => {
                            try {
                              const page = await read(
                                `report?through=${report.through}&offset=${report.nextOffset}`,
                              );
                              setReport({
                                ...page,
                                items: [...report.items, ...page.items],
                              });
                            } catch (e: any) {
                              setError(e.message);
                            }
                          }}
                        >
                          Ещё счета
                        </button>
                      )}
                    </>
                  )}
                </section>
                {options.admin && (
                  <form
                    className="finance-card"
                    onChange={() => setClosePreview(null)}
                    onSubmit={financeSubmit(async (d) => {
                      const cmd = {
                        version: options.config.version,
                        through: d.get("through"),
                        reason: d.get("reason"),
                      };
                      if (!closePreview) {
                        setClosePreview(await run("periods/preview", cmd));
                        return;
                      }
                      if (
                        window.confirm(
                          "Закрыть учёт по выбранную дату? Если выбрано сегодня, новые денежные операции будут доступны завтра.",
                        )
                      ) {
                        await run("periods", {
                          ...cmd,
                          previewHash: closePreview.previewHash,
                        });
                        setClosePreview(null);
                      }
                    }, setError)}
                  >
                    <h3>Закрыть период</h3>
                    <p>
                      Сохранится неизменяемая сводка. Закрытие сегодняшнего дня
                      блокирует новые денежные операции до завтра. Отмена
                      закрытия не предусмотрена.
                    </p>
                    <label>
                      Закрыть по дату
                      <input
                        name="through"
                        type="date"
                        required
                        max={options.today}
                        defaultValue={options.today}
                      />
                    </label>
                    <Reason />
                    {closePreview && (
                      <div className="finance-preview">
                        <p>
                          Счетов: {closePreview.accounts} · дата{" "}
                          {closePreview.through}
                        </p>
                        {summary(closePreview.totals)}
                      </div>
                    )}
                    <button>
                      {closePreview
                        ? "Подтвердить закрытие"
                        : "Проверить итоги"}
                    </button>
                  </form>
                )}
                <section className="finance-card">
                  <h3>Закрытые периоды</h3>
                  {periods.items.map((p: any) => (
                    <article key={p.id}>
                      <h4>По {p.through}</h4>
                      <p>
                        {p.actor_name} · {p.reason}
                      </p>
                      <a href={endpoint(`periods/${p.id}/export`)}>
                        Скачать зафиксированную сводку
                      </a>
                    </article>
                  ))}
                  {!periods.items.length && <p>Периоды ещё не закрывали.</p>}
                  {periods.nextBefore && (
                    <button
                      onClick={async () => {
                        try {
                          const page = await read(
                            `periods?before=${periods.nextBefore}`,
                          );
                          setPeriods({
                            ...page,
                            items: [...periods.items, ...page.items],
                          });
                        } catch (e: any) {
                          setError(e.message);
                        }
                      }}
                    >
                      Ещё периоды
                    </button>
                  )}
                </section>
              </>
            )}
          </>
        )}
      </fieldset>
    </dialog>
  );
}

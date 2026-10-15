import { useState, type FormEvent } from "react";
import { parseMoney, type FinanceRun, type FinanceFormat } from "./payments";

export function financeSubmit(
  action: (data: FormData) => Promise<unknown>,
  error: (message: string) => void,
) {
  return async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      await action(new FormData(event.currentTarget));
    } catch (e: any) {
      error(e.message);
    }
  };
}
export function Reason({
  label = "Основание (видно сотрудникам)",
}: {
  label?: string;
}) {
  return (
    <label>
      {label}
      <input name="reason" required maxLength={1000} />
    </label>
  );
}
export function Amount({
  name = "amount",
  label = "Сумма",
  value = "",
}: {
  name?: string;
  label?: string;
  value?: string;
}) {
  return (
    <label>
      {label}
      <input
        name={name}
        defaultValue={value}
        inputMode="decimal"
        required
        maxLength={20}
        autoComplete="off"
      />
    </label>
  );
}
type Props = {
  options: any;
  run: FinanceRun;
  money: FinanceFormat;
  error: (message: string) => void;
};

export function ReceiptEditor({
  options,
  run,
  money,
  error,
  account,
}: Props & { account?: any }) {
  const [lines, setLines] = useState([crypto.randomUUID()]);
  const students = [...options.students];
  if (account && !students.some((s: any) => s.id === account.id))
    students.push({
      id: account.id,
      name: account.name,
      version: account.version,
    });
  const submit = financeSubmit(async (data) => {
    const parts = lines.map((key) => {
      const studentId = String(data.get("student-" + key));
      const student = students.find((x: any) => x.id === studentId);
      if (!student)
        throw new Error("Выберите ученика для каждой части оплаты.");
      const amount = parseMoney(
        String(data.get("amount-" + key)),
        options.config.decimals,
      );
      if (!amount) throw new Error("Поступление должно быть больше нуля.");
      return {
        studentId,
        version: account?.id === studentId ? account.version : student.version,
        amount,
      };
    });
    if (new Set(parts.map((x) => x.studentId)).size !== parts.length)
      throw new Error("Объедините суммы одного ребёнка в одну строку.");
    const total = parts.reduce((sum, p) => sum + BigInt(p.amount), 0n);
    if (
      !window.confirm(
        `Учесть поступление ${money(total.toString())} по ${parts.length} лицевым счетам?`,
      )
    )
      return;
    await run("receipts", {
      paidDate: data.get("paidDate"),
      method: data.get("method"),
      reference: data.get("reference"),
      source: data.get("source"),
      payer: data.get("payer"),
      reason: data.get("reason"),
      parts,
    });
  }, error);
  return (
    <form className="finance-card" onSubmit={submit}>
      <h3>Учесть полученную оплату</h3>
      <p>
        Внесите подтверждённое поступление. Одна оплата может быть разделена
        между детьми. Деньги через эту форму не переводятся.
      </p>
      <div className="finance-grid">
        <label>
          Дата поступления
          <input
            type="date"
            name="paidDate"
            defaultValue={options.today}
            max={options.today}
            required
          />
        </label>
        <label>
          Способ
          <select name="method">
            <option value="bank">Банк</option>
            <option value="cash">Наличные</option>
            <option value="transfer">Перевод</option>
            <option value="opening">Перенос переплаты</option>
          </select>
        </label>
        <label>
          Номер документа
          <input name="reference" required maxLength={100} />
        </label>
        <label>
          Источник для банковской оплаты
          <input name="source" required defaultValue="manual" maxLength={40} />
          <small>
            Используйте то же название при импорте выписки: так операция не
            будет учтена повторно.
          </small>
        </label>
        <label>
          Плательщик (видно сотрудникам)
          <input name="payer" maxLength={160} />
        </label>
      </div>
      {lines.map((key, index) => (
        <div className="finance-line" key={key}>
          <label>
            Ребёнок {index + 1}
            <select
              name={"student-" + key}
              defaultValue={index === 0 ? account?.id || "" : ""}
              required
            >
              <option value="">Выберите ученика</option>
              {students.map((s: any) => (
                <option key={s.id} value={s.id}>
                  {s.name} · {s.className || "счёт"}
                </option>
              ))}
            </select>
          </label>
          <Amount name={"amount-" + key} label="Часть оплаты" />
          {lines.length > 1 && (
            <button
              type="button"
              onClick={() => setLines(lines.filter((x) => x !== key))}
            >
              Убрать строку
            </button>
          )}
        </div>
      ))}
      <button
        type="button"
        disabled={lines.length >= 20}
        onClick={() => setLines([...lines, crypto.randomUUID()])}
      >
        Добавить ребёнка
      </button>
      <Reason />
      <button type="submit">Учесть оплату</button>
    </form>
  );
}

export function PlanEditor({ options, run, money, error }: Props) {
  const [installments, setInstallments] = useState([crypto.randomUUID()]);
  return (
    <form
      className="finance-card"
      onSubmit={financeSubmit(async (data) => {
        await run("plans", {
          title: data.get("title"),
          installments: installments.map((key) => ({
            label: data.get("label-" + key),
            due: data.get("due-" + key),
            amount: parseMoney(
              String(data.get("amount-" + key)),
              options.config.decimals,
            ),
          })),
        });
      }, error)}
    >
      <h3>Новый план взносов</h3>
      <p>
        Каждая строка — отдельный взнос с собственным сроком. После создания
        план фиксируется; для другой суммы создайте новый план.
      </p>
      <label>
        Название
        <input
          name="title"
          maxLength={160}
          required
          placeholder="Например, обучение · 2026/27"
        />
      </label>
      {installments.map((key, i) => (
        <div className="finance-line" key={key}>
          <label>
            Взнос {i + 1}
            <input name={"label-" + key} required maxLength={100} />
          </label>
          <label>
            Оплатить до
            <input
              type="date"
              name={"due-" + key}
              required
              defaultValue={options.today}
            />
          </label>
          <Amount name={"amount-" + key} />
          {installments.length > 1 && (
            <button
              type="button"
              onClick={() =>
                setInstallments(installments.filter((x) => x !== key))
              }
            >
              Убрать
            </button>
          )}
        </div>
      ))}
      <button
        type="button"
        disabled={installments.length >= 12}
        onClick={() => setInstallments([...installments, crypto.randomUUID()])}
      >
        Добавить взнос
      </button>
      <button type="submit">Создать план</button>
      <small>До 12 взносов. Валюта: {money("0")}.</small>
    </form>
  );
}

export function PlanIssueEditor({
  options,
  run,
  money,
  error,
  plan,
}: Props & { plan: any }) {
  const [selected, setSelected] = useState<string[]>([]),
    [classId, setClassId] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const classes = [
    ...new Map<string, string>(
      options.students.map((s: any) => [s.classId, s.className]),
    ).entries(),
  ];
  const visible = options.students.filter(
    (s: any) => !classId || s.classId === classId,
  );
  function command(data: FormData) {
    if (!selected.length) throw new Error("Выберите учеников.");
    return {
      planVersion: plan.version,
      reason: data.get("reason"),
      students: selected.map((id) => ({
        studentId: id,
        discountBps: parseMoney(String(data.get("percent-" + id) || "0"), 2),
        discountAmount: parseMoney(
          String(data.get("discount-" + id) || "0"),
          options.config.decimals,
        ),
      })),
    };
  }
  return (
    <form
      className="finance-card"
      onChange={() => setPreview(null)}
      onSubmit={financeSubmit(async (data) => {
        const cmd = command(data);
        if (!preview) {
          setPreview(await run(`plans/${plan.id}/preview`, cmd));
          return;
        }
        await run(`plans/${plan.id}/issue`, {
          ...cmd,
          previewHash: preview.previewHash,
        });
        setPreview(null);
        setSelected([]);
      }, error)}
    >
      <h3>Начислить: {plan.data.title}</h3>
      <ul>
        {plan.data.installments.map((x: any, i: number) => (
          <li key={i}>
            {x.label} · до {x.due} · {money(x.amount)}
          </li>
        ))}
      </ul>
      <label>
        Класс
        <select value={classId} onChange={(e) => setClassId(e.target.value)}>
          <option value="">Все классы</option>
          {classes.map(([id, name]) => (
            <option value={id} key={id}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <div className="finance-actions">
        <button
          type="button"
          onClick={() => {
            setSelected(
              [
                ...new Set([...selected, ...visible.map((s: any) => s.id)]),
              ].slice(0, 100),
            );
            setPreview(null);
          }}
        >
          Выбрать показанных (до 100)
        </button>
        <button
          type="button"
          onClick={() => {
            setSelected([]);
            setPreview(null);
          }}
        >
          Очистить выбор
        </button>
      </div>
      <div className="finance-choices">
        {visible.map((s: any) => (
          <label className="finance-check" key={s.id}>
            <input
              type="checkbox"
              checked={selected.includes(s.id)}
              disabled={!selected.includes(s.id) && selected.length >= 100}
              onChange={(e) =>
                setSelected(
                  e.target.checked
                    ? [...selected, s.id]
                    : selected.filter((id) => id !== s.id),
                )
              }
            />
            {s.name} · {s.className}
          </label>
        ))}
      </div>
      <p>
        Выбрано: {selected.length}. Скидка применяется к каждому взносу: укажите
        процент либо фиксированную сумму.
      </p>
      {selected.map((id) => (
        <div className="finance-line" key={id}>
          <strong>
            {options.students.find((s: any) => s.id === id)?.name}
          </strong>
          <Amount name={"percent-" + id} label="Скидка, %" value="0" />
          <Amount
            name={"discount-" + id}
            label="Скидка с каждого взноса"
            value="0"
          />
        </div>
      ))}
      <Reason />
      {preview && (
        <section className="finance-preview">
          <h4>Проверьте начисление · {money(preview.total)}</h4>
          <p>
            Количество взносов: {preview.items.length}. Повторное начисление
            этого плана тому же ребёнку запрещено.
          </p>
          <div className="finance-scroll">
            <table>
              <thead>
                <tr>
                  <th>Ребёнок</th>
                  <th>Взносов</th>
                  <th>Скидка</th>
                  <th>К начислению</th>
                </tr>
              </thead>
              <tbody>
                {selected.map((id) => {
                  const rows = preview.items.filter(
                    (x: any) => x.studentId === id,
                  );
                  return (
                    <tr key={id}>
                      <td>{rows[0]?.name}</td>
                      <td>{rows.length}</td>
                      <td>
                        {money(
                          rows
                            .reduce(
                              (sum: bigint, x: any) => sum + BigInt(x.discount),
                              0n,
                            )
                            .toString(),
                        )}
                      </td>
                      <td>
                        {money(
                          rows
                            .reduce(
                              (sum: bigint, x: any) => sum + BigInt(x.amount),
                              0n,
                            )
                            .toString(),
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <button type="submit">
        {preview ? "Подтвердить начисление" : "Проверить суммы"}
      </button>
    </form>
  );
}

export function BankImport({ options, run, money, error }: Props) {
  const [text, setText] = useState(""),
    [preview, setPreview] = useState<any>(null);
  return (
    <form
      className="finance-card"
      onChange={() => setPreview(null)}
      onSubmit={financeSubmit(async (data) => {
        const cmd = {
          source: data.get("source"),
          text,
          reason: data.get("reason"),
        };
        if (!preview) {
          setPreview(await run("import/preview", cmd));
          return;
        }
        await run("import", { ...cmd, previewHash: preview.previewHash });
        setPreview(null);
        setText("");
      }, error)}
    >
      <h3>Сверка банковских поступлений</h3>
      <p>
        Используйте постоянное название источника и уникальные номера операций
        банка. Сопоставление выполняется по коду лицевого счёта, без угадывания
        по ФИО. До 100 строк за один импорт.
      </p>
      <label>
        Источник выписки
        <input
          name="source"
          required
          maxLength={40}
          placeholder="Название банка или счёта"
        />
      </label>
      <label>
        CSV-файл
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            if (file.size > 60000) {
              error("Файл должен быть не больше 60 КБ.");
              return;
            }
            try {
              setText(await file.text());
              setPreview(null);
            } catch {
              error("Не удалось прочитать файл.");
            }
          }}
        />
      </label>
      <label>
        Содержимое CSV
        <textarea
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={60000}
          required
          placeholder={
            "bank_id,paid_date,account_code,amount,payer\nОП-001,2026-10-15,AT-...,12500.00,Имя плательщика"
          }
        />
      </label>
      <p>
        Сумма указывается в {options.config.currency}, до{" "}
        {options.config.decimals} знаков после запятой. Дата: ГГГГ-ММ-ДД.
        Пропущенные поля и неизвестные счета нужно исправить до подтверждения.
      </p>
      <Reason />
      {preview && (
        <section className="finance-preview">
          <h4>
            Сопоставлено {preview.items.length} · {money(preview.total)}
          </h4>
          {preview.errors.map((e: any) => (
            <p className="finance-error" key={e.line}>
              Строка {e.line}: {e.message}
            </p>
          ))}
          <div className="finance-scroll">
            <table>
              <thead>
                <tr>
                  <th>Строка</th>
                  <th>Документ</th>
                  <th>Ребёнок</th>
                  <th>Дата</th>
                  <th>Сумма</th>
                </tr>
              </thead>
              <tbody>
                {preview.items.map((r: any) => (
                  <tr key={r.line}>
                    <td>{r.line}</td>
                    <td>{r.reference}</td>
                    <td>
                      {r.name} · {r.code}
                    </td>
                    <td>{r.paidDate}</td>
                    <td>{money(r.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <button type="submit" disabled={!!preview?.errors.length}>
        {preview ? "Учесть все поступления" : "Сопоставить выписку"}
      </button>
      {preview && (
        <button type="button" onClick={() => setPreview(null)}>
          Изменить и проверить заново
        </button>
      )}
    </form>
  );
}

export function FinanceSettings({ options, run, error }: Props) {
  const cfg = options.config;
  return (
    <form
      className="finance-card"
      onSubmit={financeSubmit(async (data) => {
        await run(
          "config",
          {
            version: cfg.version,
            currency: String(data.get("currency")).toUpperCase(),
            decimals: Number(data.get("decimals")),
            familyPolicy: data.get("familyPolicy"),
            managerIds: data.getAll("managers"),
            reason: data.get("reason"),
          },
          "PUT",
        );
      }, error)}
    >
      <h3>Доступ и единицы учёта</h3>
      <div className="finance-grid">
        <label>
          Код валюты
          <input
            name="currency"
            defaultValue={cfg.currency}
            required
            pattern="[A-Z]{3}"
            maxLength={3}
          />
        </label>
        <label>
          Знаков после запятой
          <input
            name="decimals"
            type="number"
            min={0}
            max={3}
            defaultValue={cfg.decimals}
            required
          />
        </label>
      </div>
      <p>Валюта и точность фиксируются после первого плана или операции.</p>
      <label>
        Кому показывать счёт ребёнка
        <select name="familyPolicy" defaultValue={cfg.familyPolicy}>
          <option value="assigned">Только назначенным плательщикам</option>
          <option value="verified">
            Всем подтверждённым родителям с действующим доступом
          </option>
        </select>
      </label>
      <p>
        Ученикам и неназначенным сотрудникам финансовые сведения недоступны.
        Изменение общих полномочий сотрудника или назначенного плательщика
        требует повторной проверки назначения.
      </p>
      <fieldset>
        <legend>Сотрудники финансовой службы</legend>
        {options.staff.map((p: any) => (
          <label key={p.id} className="finance-check">
            <input
              type="checkbox"
              name="managers"
              value={p.id}
              defaultChecked={cfg.managerIds.includes(p.id)}
            />
            {p.name}
          </label>
        ))}
      </fieldset>
      <Reason />
      <button type="submit">Сохранить доступ</button>
    </form>
  );
}

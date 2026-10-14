import { useEffect, useState } from "react";
import type { State } from "./types";
import { api } from "./api";
import { csvCell, download } from "./utils";
import ChangePreview from "./ChangePreview";
import type { Preview } from "./ChangePreview";

interface ImportField {
  key: string;
  label: string;
  kind: string;
  required: boolean;
  choices: (string | number)[];
  hint: string;
  aliases: string[];
}
interface ImportTable {
  key: string;
  label: string;
  canMatchName: boolean;
  canMatchExternal: boolean;
  common: string[];
  fields: ImportField[];
}
interface SheetData {
  sheet: number;
  headerRow: number;
  headers: { index: number; label: string }[];
  rows: { number: number; cells: string[] }[];
  rowCount: number;
  warnings: string[];
}
interface Upload extends SheetData {
  token: string;
  filename: string;
  sheets: { index: number; name: string; rows: number; columns: number }[];
}
interface Result {
  status: string;
  errorCount: number;
  errors: { row: number | null; field: string; message: string }[];
  counts: { rows: number; added: number; changed: number; unchanged: number };
  preview?: Preview;
}
const normal = (v: string) =>
  v
    .trim()
    .toLocaleLowerCase()
    .replace(/[\s_.\-]/g, "");

export default function ImportWizard({
  state: s,
  workspace,
  onState,
}: {
  state: State;
  workspace: string;
  onState: (s: State) => Promise<void>;
}) {
  const [schema, setSchema] = useState<ImportTable[]>([]),
    [table, setTable] = useState("students");
  const [file, setFile] = useState<File | null>(null),
    [upload, setUpload] = useState<Upload | null>(null);
  const [delimiter, setDelimiter] = useState("auto"),
    [encoding, setEncoding] = useState("auto");
  const [mapping, setMapping] = useState<Record<string, number>>({}),
    [constants, setConstants] = useState<Record<string, string>>({});
  const [mode, setMode] = useState("upsert"),
    [match, setMatch] = useState("name"),
    [skipBlanks, setSkipBlanks] = useState(true),
    [separator, setSeparator] = useState(";");
  const [advanced, setAdvanced] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [nextHeader, setNextHeader] = useState(1);
  const selected = schema.find((x) => x.key === table);
  useEffect(() => {
    let stopped = false;
    api("exchange/import-schema", workspace)
      .then((data) => {
        if (!stopped) setSchema(data);
      })
      .catch((e) => {
        if (!stopped) setError(e.message);
      });
    return () => {
      stopped = true;
    };
  }, [workspace]);
  useEffect(() => {
    setResult(null);
    setError("");
  }, [s.revision, workspace]);
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
  function autoMap(key: string, headers = upload?.headers || []) {
    const meta = schema.find((t) => t.key === key);
    const mapped: Record<string, number> = {};
    for (const f of meta?.fields || []) {
      const labels = [f.key, f.label, ...f.aliases].map(normal);
      const exact = headers.filter((h) => normal(h.label) === normal(f.key));
      const found = exact.length
        ? exact
        : headers.filter((h) => labels.includes(normal(h.label)));
      if (found.length === 1) mapped[f.key] = found[0].index;
    }
    setMapping(mapped);
    setConstants({});
    setResult(null);
    setAdvanced(false);
    setMatch(
      mapped.id !== undefined
        ? "id"
        : meta?.canMatchExternal && mapped.externalId !== undefined
          ? "externalId"
          : meta?.canMatchName
            ? "name"
            : "id",
    );
  }
  async function readFile(chosen: File) {
    setUpload(null);
    setResult(null);
    if (chosen.size > 10_000_000)
      throw new Error("Файл превышает 10 МБ. Разделите его на части.");
    const content = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("Не удалось прочитать файл."));
      reader.readAsDataURL(chosen);
    });
    const data: Upload = await api("exchange/inspect", workspace, "POST", {
      filename: chosen.name,
      content,
      delimiter,
      encoding,
    });
    setUpload(data);
    setNextHeader(1);
    const hint =
      schema.find(
        (t) =>
          normal(t.label) === normal(data.sheets[0]?.name || "") ||
          normal(t.key) === normal(data.sheets[0]?.name || ""),
      )?.key || table;
    setTable(hint);
    autoMap(hint, data.headers);
  }
  async function selectSheet(sheet: number, headerRow: number) {
    if (!upload) return;
    const data: SheetData = await api("exchange/sheet", workspace, "POST", {
      token: upload.token,
      sheet,
      headerRow,
    });
    setUpload({ ...upload, ...data });
    setNextHeader(headerRow);
    const hint =
      sheet !== upload.sheet
        ? schema.find(
            (t) =>
              normal(t.label) === normal(upload.sheets[sheet]?.name || "") ||
              normal(t.key) === normal(upload.sheets[sheet]?.name || ""),
          )?.key || table
        : table;
    setTable(hint);
    autoMap(hint, data.headers);
  }
  function map(key: string, value: string) {
    const nextMap = { ...mapping },
      nextConstants = { ...constants };
    delete nextMap[key];
    delete nextConstants[key];
    if (value === "constant") nextConstants[key] = "";
    else if (value !== "skip") nextMap[key] = Number(value);
    setMapping(nextMap);
    setConstants(nextConstants);
    setResult(null);
  }
  function template() {
    if (!selected) return;
    download(
      `atlas-${table}-template.csv`,
      "\uFEFF" + selected.common.map(csvCell).join(";") + "\r\n",
      "text/csv;charset=utf-8",
    );
  }
  const fields =
    selected?.fields.filter(
      (f) =>
        advanced ||
        selected.common.includes(f.key) ||
        mapping[f.key] !== undefined ||
        constants[f.key] !== undefined,
    ) || [];
  return (
    <section className="export-card import-wizard">
      <h2>Импорт таблиц</h2>
      <p>
        Загрузите файл, выберите лист и сопоставьте колонки. До применения
        показываются все изменения; записи вне файла и поля без сопоставления
        сохраняются.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <fieldset className="import-settings" disabled={busy}>
        <legend>1. Файл и таблица</legend>
        <div className="export-options">
          <label className="field">
            <span>Куда импортировать</span>
            <select
              value={table}
              onChange={(e) => {
                setTable(e.target.value);
                autoMap(e.target.value);
              }}
            >
              {schema.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Разделитель CSV</span>
            <select
              value={delimiter}
              onChange={(e) => setDelimiter(e.target.value)}
            >
              <option value="auto">Определить автоматически</option>
              <option value=";">Точка с запятой</option>
              <option value=",">Запятая</option>
              <option value={"\t"}>Табуляция</option>
              <option value="|">Вертикальная черта</option>
            </select>
          </label>
          <label className="field">
            <span>Кодировка текста</span>
            <select
              value={encoding}
              onChange={(e) => setEncoding(e.target.value)}
            >
              <option value="auto">Автоматически</option>
              <option value="utf-8-sig">UTF-8</option>
              <option value="cp1251">Windows-1251</option>
              <option value="utf-16">UTF-16</option>
            </select>
          </label>
        </div>
        <label className="field">
          <span>Файл для импорта таблицы</span>
          <input
            type="file"
            accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls,.html,.htm,.mhtml,.mht,.xml"
            onChange={(e) => {
              const chosen = e.target.files?.[0];
              if (!chosen) return;
              setFile(chosen);
              void run(() => readFile(chosen));
            }}
          />
          <small>
            До 10 МБ, 20 000 строк, 200 колонок и 300 000 ячеек. Для XML-копии
            Атласа используйте восстановление ниже.
          </small>
        </label>
        <div className="button-row">
          <button
            disabled={!file}
            onClick={() => file && void run(() => readFile(file))}
          >
            Перечитать файл
          </button>
          <button disabled={!selected} onClick={template}>
            Шаблон CSV
          </button>
        </div>
        <p className="muted">
          После изменения кодировки или разделителя перечитайте файл. Заголовки
          шаблона соответствуют полям ниже; значения ссылок могут быть ID или
          точными однозначными именами.
        </p>
        {upload && (
          <>
            <div className="export-options">
              <label className="field">
                <span>Лист файла</span>
                <select
                  value={upload.sheet}
                  onChange={(e) =>
                    void run(() => selectSheet(Number(e.target.value), 1))
                  }
                >
                  {upload.sheets.map((x) => (
                    <option key={x.index} value={x.index}>
                      {x.name} · {x.rows} строк
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Строка заголовков</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={nextHeader}
                  onChange={(e) => setNextHeader(Number(e.target.value))}
                />
                <small>
                  0 — заголовков нет. Данные начинаются со следующей строки.
                </small>
              </label>
              <button
                onClick={() =>
                  void run(() => selectSheet(upload.sheet, nextHeader))
                }
              >
                Применить строку заголовков
              </button>
            </div>
            {upload.warnings.map((w, i) => (
              <p className="info-line" key={i}>
                {w}
              </p>
            ))}
            <details open>
              <summary>
                Первые строки · {upload.rowCount} строк данных ·{" "}
                {upload.filename}
              </summary>
              <div className="import-sample table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Строка</th>
                      {upload.headers.map((h) => (
                        <th key={h.index}>
                          {h.index + 1}. {h.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {upload.rows.map((r) => (
                      <tr key={r.number}>
                        <th>{r.number}</th>
                        {upload.headers.map((h) => (
                          <td key={h.index} title={r.cells[h.index] || ""}>
                            {(r.cells[h.index] || "").slice(0, 160)}
                            {(r.cells[h.index] || "").length > 160 ? "…" : ""}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </>
        )}
      </fieldset>
      {upload && selected && (
        <fieldset className="import-settings" disabled={busy}>
          <legend>2. Сопоставление и режим</legend>
          <div className="export-options">
            <label className="field">
              <span>Режим импорта</span>
              <select
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value);
                  setResult(null);
                }}
              >
                <option value="upsert">
                  Добавить новые и обновить найденные
                </option>
                <option value="update">Только обновить существующие</option>
                <option value="add">Только добавить новые</option>
              </select>
            </label>
            {mode !== "add" && (
              <label className="field">
                <span>Искать существующие записи</span>
                <select
                  value={match}
                  onChange={(e) => {
                    setMatch(e.target.value);
                    setResult(null);
                  }}
                >
                  <option value="id">По ID записи</option>
                  {selected.canMatchName && (
                    <option value="name">
                      {table === "students"
                        ? "По имени и классу"
                        : "По точному имени / названию"}
                    </option>
                  )}
                  {selected.canMatchExternal && (
                    <option value="externalId">По внешнему ID ученика</option>
                  )}
                </select>
              </label>
            )}
            <label className="field">
              <span>Разделитель внутри списков</span>
              <select
                value={separator}
                onChange={(e) => {
                  setSeparator(e.target.value);
                  setResult(null);
                }}
              >
                <option value=";">Точка с запятой</option>
                <option value=",">Запятая</option>
                <option value="|">Вертикальная черта</option>
                <option value={"\n"}>Новая строка</option>
              </select>
            </label>
          </div>
          {mode === "add" && (
            <p className="info-line">
              Новые строки без ID получат новые идентификаторы. При загрузке
              другого файла в этом режиме совпадение имён не предотвращает
              дубликаты.
            </p>
          )}
          <label className="check-line">
            <input
              type="checkbox"
              checked={skipBlanks}
              onChange={(e) => {
                setSkipBlanks(e.target.checked);
                setResult(null);
              }}
            />
            Пропускать пустые ячейки — сохранять прежние значения
          </label>
          {!skipBlanks && (
            <p className="info-line">
              Пустые сопоставленные ячейки очистят необязательные поля или
              вернут значение по умолчанию. Обязательное поле нельзя оставить
              пустым.
            </p>
          )}
          <p className="muted">
            Звёздочкой отмечены поля, обязательные для новой записи. При
            обновлении несопоставленные поля сохраняются. Неоднозначные имена
            требуют ID. Дни и номера уроков начинаются с 0.
          </p>
          <div className="import-mapping">
            {fields.map((f) => (
              <div className="import-map-row" key={f.key}>
                <div>
                  <strong>
                    {f.label}
                    {f.required ? " *" : ""}
                  </strong>
                  <small>
                    {f.key} ·{" "}
                    {f.choices.length ? f.choices.join(" / ") : f.kind}
                  </small>
                  {f.hint && <small>{f.hint}</small>}
                </div>
                <label className="field">
                  <span className="sr-only">Колонка для {f.label}</span>
                  <select
                    aria-label={`Колонка для ${f.label}`}
                    value={
                      constants[f.key] !== undefined
                        ? "constant"
                        : (mapping[f.key] ?? "skip")
                    }
                    onChange={(e) => map(f.key, e.target.value)}
                  >
                    <option value="skip">Не изменять</option>
                    {upload.headers.map((h) => (
                      <option key={h.index} value={h.index}>
                        {h.index + 1}. {h.label}
                      </option>
                    ))}
                    <option value="constant">
                      Постоянное значение для всех строк
                    </option>
                  </select>
                </label>
                {constants[f.key] !== undefined && (
                  <label className="field import-constant">
                    <span>Значение: {f.label}</span>
                    {f.choices.length ? (
                      <select
                        value={constants[f.key]}
                        onChange={(e) => {
                          setConstants({
                            ...constants,
                            [f.key]: e.target.value,
                          });
                          setResult(null);
                        }}
                      >
                        <option value="">Не задано</option>
                        {f.choices.map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        value={constants[f.key]}
                        onChange={(e) => {
                          setConstants({
                            ...constants,
                            [f.key]: e.target.value,
                          });
                          setResult(null);
                        }}
                      />
                    )}
                  </label>
                )}
              </div>
            ))}
          </div>
          <label className="check-line">
            <input
              type="checkbox"
              checked={advanced}
              onChange={(e) => setAdvanced(e.target.checked)}
            />
            Показать все поля таблицы ({selected.fields.length})
          </label>
          <details>
            <summary>Форматы значений</summary>
            <p>
              Да/нет: true/false, 1/0 или да/нет. Даты: ГГГГ-ММ-ДД. В списке
              можно использовать выбранный разделитель или JSON, например
              ["ID-1","ID-2"]. В сложных полях — звонках, секциях, интервалах и
              настройках по неделям — нужен JSON с внутренними ID. Образцы
              структуры есть в экспорте таблиц всей базы.
            </p>
            <p>
              Импорт не создаёт связанные справочники автоматически: сначала
              загрузите учителей, предметы, кабинеты и группы, затем их нагрузку
              и занятия.
            </p>
          </details>
          <button
            className="primary"
            disabled={
              (!Object.keys(mapping).length &&
                !Object.keys(constants).length) ||
              !upload.rowCount
            }
            onClick={() =>
              void run(async () =>
                setResult(
                  await api("exchange/import-preview", workspace, "POST", {
                    token: upload.token,
                    sheet: upload.sheet,
                    headerRow: upload.headerRow,
                    revision: s.revision,
                    table,
                    mapping,
                    constants,
                    mode,
                    match,
                    skipBlanks,
                    listSeparator: separator,
                  }),
                ),
              )
            }
          >
            {busy ? "Проверяем импорт…" : "Проверить импорт"}
          </button>
        </fieldset>
      )}
      {result && (
        <section className="import-result" aria-live="polite">
          <h3>Проверка импорта</h3>
          <p>
            Строк данных: {result.counts.rows}. Добавлений:{" "}
            {result.counts.added} · изменений: {result.counts.changed} · без
            изменений: {result.counts.unchanged}.
          </p>
          {!!result.errorCount && (
            <>
              <p role="alert">
                Ошибок: {result.errorCount}. Ни одна строка не сохранена.
                Исправьте файл, сопоставление или постоянные значения.
              </p>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Строка</th>
                      <th>Поле</th>
                      <th>Причина</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.errors.map((e, i) => (
                      <tr key={i}>
                        <td>{e.row ?? "Вся база"}</td>
                        <td>{e.field}</td>
                        <td>{e.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {result.errorCount > result.errors.length && (
                <p>Показаны первые {result.errors.length} ошибок.</p>
              )}
              <button
                onClick={() =>
                  download(
                    "import-errors.csv",
                    "\uFEFF" +
                      [
                        ["Строка", "Поле", "Ошибка"],
                        ...result.errors.map((e) => [
                          String(e.row ?? "Вся база"),
                          e.field,
                          e.message,
                        ]),
                      ]
                        .map((r) => r.map(csvCell).join(";"))
                        .join("\r\n"),
                    "text/csv;charset=utf-8",
                  )
                }
              >
                Скачать показанные ошибки
              </button>
            </>
          )}
          {result.preview && (
            <ChangePreview
              title={`Импорт в «${selected?.label}»`}
              preview={result.preview}
              busy={busy}
              onCancel={() => setResult(null)}
              onApply={() =>
                void run(async () => {
                  const p = result.preview!;
                  await onState(
                    await api("editor/apply", workspace, "POST", {
                      revision: p.revision,
                      token: p.token,
                    }),
                  );
                })
              }
            />
          )}
        </section>
      )}
    </section>
  );
}

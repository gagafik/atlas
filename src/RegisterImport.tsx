import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import {
  registerIssueCount,
  registerReasons,
  targetLabel,
  type RegisterAttention,
  type RegisterColumns,
  type RegisterCounts,
  type RegisterIssue,
  type RegisterPreview,
} from "./bulkInvitations";

export const registerBase = "access/invitations/registers";

const countLabels: [keyof RegisterCounts, string][] = [
  ["rows", "Строк с родителями в файле"],
  ["ready", "Готовы к выдаче"],
  ["readyCovered", "Готовы, но у детей уже есть доступ или код"],
  ["partial", "Найдены не все дети"],
  ["partialCovered", "Найдены не все дети, доступ уже есть"],
  ["notFound", "Ни один ребёнок не найден"],
  ["ambiguous", "Неоднозначное совпадение"],
  ["noChildren", "Нет действующих детей: пропущены"],
  ["noName", "Нет имени родителя: пропущены"],
  ["repeated", "Повторы: посчитаны один раз"],
];

function readBase64(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл."));
    reader.readAsDataURL(file);
  });
}

export default function RegisterImport({
  workspace,
  locked,
  run,
  onIssue,
  onDirty,
  staleKey,
}: {
  workspace: string;
  locked: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
  onIssue: (payload: RegisterIssue) => void;
  onDirty: (dirty: boolean) => void;
  staleKey: number;
}) {
  const [file, setFile] = useState<{ name: string; content: string } | null>(
      null,
    ),
    [preview, setPreview] = useState<RegisterPreview | null>(null),
    [more, setMore] = useState<RegisterAttention[]>([]),
    [days, setDays] = useState(7),
    [includePartial, setIncludePartial] = useState(false),
    [includeCovered, setIncludeCovered] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  // A refused or outdated issue request leaves the file but drops the stale review.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    setPreview(null);
    setMore([]);
  }, [staleKey]);
  useEffect(() => onDirty(!!preview), [preview]);

  async function check(
    chosen: { name: string; content: string },
    options: {
      sheet: number;
      columns?: RegisterColumns;
      offset?: number;
    },
  ) {
    const result: RegisterPreview = await api(
      `${registerBase}/preview`,
      workspace,
      "POST",
      {
        workspace,
        filename: chosen.name,
        content: chosen.content,
        sheet: options.sheet,
        ...(options.columns ? { columns: options.columns } : {}),
        offset: options.offset || 0,
      },
    );
    if (!alive.current) return;
    if (options.offset) setMore((old) => [...old, ...result.attention]);
    else {
      setPreview(result);
      setMore([]);
    }
  }
  const run1 = (
    chosen: { name: string; content: string },
    options: Parameters<typeof check>[1],
  ) => void run(() => check(chosen, options));
  async function choose(input: File | undefined) {
    if (!input) return;
    if (input.size > 10_000_000) {
      setPreview(null);
      setFile(null);
      void run(async () => {
        throw new Error("Файл превышает 10 МБ. Разделите его на части.");
      });
      return;
    }
    void run(async () => {
      const content = await readBase64(input);
      const chosen = { name: input.name, content };
      setFile(chosen);
      setIncludePartial(false);
      setIncludeCovered(false);
      await check(chosen, { sheet: 0 });
    });
  }
  const counts = preview?.counts || null;
  const shown = [...(preview?.attention || []), ...more];
  const count = counts
    ? registerIssueCount(counts, includePartial, includeCovered)
    : 0;
  const columnSelect = (
    key: keyof RegisterColumns,
    label: string,
    optional: boolean,
  ) =>
    preview &&
    file && (
      <label className="field">
        <span>{label}</span>
        <select
          value={preview.columns[key] ?? ""}
          onChange={(e) =>
            run1(file, {
              sheet: preview.sheet,
              columns: {
                ...preview.columns,
                [key]: e.target.value === "" ? null : Number(e.target.value),
              },
            })
          }
        >
          {optional && <option value="">Нет такой колонки</option>}
          {!optional && preview.columns[key] === null && (
            <option value="">Выберите колонку</option>
          )}
          {preview.headers.map((h) => (
            <option key={h.index} value={h.index}>
              {h.label}
            </option>
          ))}
        </select>
      </label>
    );
  return (
    <section className="register-import" aria-label="Реестр родителей из файла">
      <h4>Родители из файла реестра</h4>
      <p>
        Файл CSV или XLSX из прежней системы: строка на родителя, дети записаны
        как «Фамилия Имя (класс)» через запятую. До 3000 строк. Ребёнок ищется
        среди действующих учеников по классу и имени; регистр, пробелы и порядок
        слов не важны. Весь реестр выдаётся одним пакетом.
      </p>
      <fieldset disabled={locked}>
        <label className="field">
          <span>Файл реестра родителей</span>
          <input
            type="file"
            accept=".csv,.tsv,.txt,.xlsx,.xls"
            onChange={(e) => {
              void choose(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </label>
        {preview && file && (
          <>
            <p>
              Файл: {file.name}. Строка заголовков: {preview.headerRow}.
            </p>
            {preview.sheets.length > 1 && (
              <label className="field">
                <span>Лист файла</span>
                <select
                  value={preview.sheet}
                  onChange={(e) =>
                    run1(file, { sheet: Number(e.target.value) })
                  }
                >
                  {preview.sheets.map((name, i) => (
                    <option key={i} value={i}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="dated-fields">
              {columnSelect(
                "surname",
                "Колонка с фамилией (или полным именем)",
                true,
              )}
              {columnSelect("name", "Колонка с именем", true)}
              {columnSelect("children", "Колонка с детьми", false)}
            </div>
            {preview.needsColumns && (
              <p role="status">
                Не удалось определить колонки по заголовкам «Фамилия», «Имя»,
                «Дети». Выберите их вручную.
              </p>
            )}
          </>
        )}
        {preview && counts && file && (
          <section aria-label="Проверка реестра">
            <h4>Проверьте реестр перед выдачей</h4>
            <dl className="register-counts">
              {countLabels
                .filter(
                  ([key]) =>
                    key === "rows" || key === "ready" || counts[key] > 0,
                )
                .map(([key, label]) => (
                  <div key={key}>
                    <dt>{label}</dt>
                    <dd>{counts[key]}</dd>
                  </div>
                ))}
            </dl>
            {preview.attentionTotal > 0 && (
              <>
                <h4>Требуют внимания: {preview.attentionTotal}</h4>
                {shown.map((row) => (
                  <article className="export-card" key={row.row}>
                    <strong>
                      Строка {row.row}: {row.recipient || "без имени"}
                    </strong>
                    <p>Дети в файле: {row.children}</p>
                    {row.found.length > 0 && (
                      <p>Найдены: {row.found.map(targetLabel).join("; ")}</p>
                    )}
                    {registerReasons(row).map((reason, i) => (
                      <p key={i}>{reason}</p>
                    ))}
                  </article>
                ))}
                {shown.length < preview.attentionTotal && (
                  <button
                    onClick={() =>
                      run1(file, {
                        sheet: preview.sheet,
                        columns: preview.columns,
                        offset: shown.length,
                      })
                    }
                  >
                    Показать ещё
                  </button>
                )}
              </>
            )}
            {counts.partial + counts.partialCovered > 0 && (
              <label>
                <input
                  type="checkbox"
                  checked={includePartial}
                  onChange={(e) => setIncludePartial(e.target.checked)}
                />
                Выдать коды и родителям, у которых найдены не все дети: они
                получат доступ только к найденным.
              </label>
            )}
            {counts.readyCovered + counts.partialCovered > 0 && (
              <label>
                <input
                  type="checkbox"
                  checked={includeCovered}
                  onChange={(e) => setIncludeCovered(e.target.checked)}
                />
                Выдать и тем, у чьих детей уже есть аккаунт или ожидающее
                приглашение: нужен отдельный код.
              </label>
            )}
            <label className="field">
              <span>Срок приглашений, дней</span>
              <input
                type="number"
                min={1}
                max={30}
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
              />
            </label>
            <button
              className="primary"
              disabled={
                count === 0 ||
                !preview.previewToken ||
                !Number.isInteger(days) ||
                days < 1 ||
                days > 30
              }
              onClick={() =>
                onIssue({
                  workspace,
                  filename: file.name,
                  content: file.content,
                  sheet: preview.sheet,
                  headerRow: preview.headerRow,
                  columns: preview.columns,
                  days,
                  previewToken: preview.previewToken!,
                  requestId: crypto.randomUUID(),
                  includePartial,
                  includeCovered,
                })
              }
            >
              Выдать коды: {count}
            </button>
            <button
              onClick={() => {
                if (
                  window.confirm(
                    "Убрать загруженный реестр? Приглашения ещё не созданы.",
                  )
                ) {
                  setFile(null);
                  setPreview(null);
                  setMore([]);
                }
              }}
            >
              Убрать файл
            </button>
          </section>
        )}
      </fieldset>
    </section>
  );
}

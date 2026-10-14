import { useEffect, useRef, useState } from "react";
import type { State, Named } from "./types";
import { api } from "./api";
import { patterns } from "./cycles";
import ChangePreview from "./ChangePreview";
import type { Preview } from "./ChangePreview";
import ImportWizard from "./ImportWizard";

type View = "class" | "teacher" | "room" | "group" | "student";
interface Options {
  view: View;
  entityIds: string[];
  cycleIds: string[];
  template: "grid" | "list" | "overview";
  paper: "A4" | "A3" | "A2" | "A1";
  landscape: boolean;
  showTeachers: boolean;
  showRooms: boolean;
  showTimes: boolean;
  title: string;
}

export default function ExchangePanel({
  state: s,
  workspace,
  defaultCycle,
  onState,
}: {
  state: State;
  workspace: string;
  defaultCycle: string;
  onState: (s: State) => Promise<void>;
}) {
  const [options, setOptions] = useState<Options>({
    view: "class",
    entityIds: [],
    cycleIds: [defaultCycle],
    template: "grid",
    paper: "A4",
    landscape: true,
    showTeachers: true,
    showRooms: true,
    showTimes: true,
    title: "",
  });
  const [selection, setSelection] = useState(false),
    [query, setQuery] = useState("");
  const [html, setHtml] = useState(""),
    [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [printReady, setPrintReady] = useState(false);
  const [tab, setTab] = useState<"import" | "print" | "backup">("import");
  const frame = useRef<HTMLIFrameElement>(null);
  const tables = {
    class: s.classes,
    teacher: s.teachers,
    room: s.rooms,
    group: s.groups,
    student: s.students,
  };
  const entities: Named[] = tables[options.view];
  const filtered = entities.filter((e) =>
    e.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  useEffect(() => {
    setHtml("");
    setPreview(null);
    setError("");
  }, [s.revision, workspace]);
  useEffect(() => {
    setOptions((v) => ({ ...v, cycleIds: [defaultCycle] }));
    setHtml("");
  }, [defaultCycle]);
  function change(patch: Partial<Options>) {
    setOptions((o) => ({ ...o, ...patch }));
    setHtml("");
    setError("");
  }
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
  async function file(format: string, show = false) {
    const response = await fetch(
      `/api/exchange/export?workspace=${encodeURIComponent(workspace)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Atlas-Request": "1" },
        body: JSON.stringify({
          ...options,
          entityIds: selection ? options.entityIds : [],
          revision: s.revision,
          format,
        }),
      },
    );
    if (!response.ok) {
      const r = await response.json();
      throw new Error(
        typeof r.detail === "string"
          ? r.detail
          : "Не удалось подготовить экспорт.",
      );
    }
    if (show) {
      setPrintReady(false);
      setHtml(await response.text());
      return;
    }
    const blob = await response.blob(),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = `atlas-${format}.${format.startsWith("xlsx") ? "xlsx" : format}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const empty =
    !options.cycleIds.length || (selection && !options.entityIds.length);
  return (
    <div className="exchange-panel">
      <div className="exchange-tabs" aria-label="Разделы обмена">
        {(
          [
            ["import", "Импорт таблиц"],
            ["print", "Расписание и печать"],
            ["backup", "Копии всей базы"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "selected" : ""}
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div hidden={tab !== "import"}>
        <ImportWizard state={s} workspace={workspace} onState={onState} />
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div className="exchange-section" hidden={tab !== "print"}>
        <section className="export-card">
          <h2>Расписание и пакетная печать</h2>
          <p className="muted">
            Выберите несколько записей и недель. В сетке и списке каждая запись
            начинается с новой страницы; сводный плакат объединяет их. Время
            берётся из фактических звонков каждого занятия.
          </p>
          <fieldset className="export-settings" disabled={busy}>
            <div className="export-options">
              <label className="field">
                <span>Для кого подготовить</span>
                <select
                  value={options.view}
                  onChange={(e) => {
                    change({ view: e.target.value as View, entityIds: [] });
                    setQuery("");
                  }}
                >
                  <option value="class">Классы</option>
                  <option value="teacher">Учителя</option>
                  <option value="room">Кабинеты</option>
                  <option value="group">Учебные группы</option>
                  <option value="student">Ученики</option>
                </select>
              </label>
              <label className="field">
                <span>Объём экспорта</span>
                <select
                  value={selection ? "selected" : "all"}
                  onChange={(e) => {
                    setSelection(e.target.value === "selected");
                    setHtml("");
                  }}
                >
                  <option value="all">Все записи</option>
                  <option value="selected">Только выбранные</option>
                </select>
                <small>В режиме «Все ученики» архивные записи исключены.</small>
              </label>
              <label className="field">
                <span>Шаблон печати</span>
                <select
                  value={options.template}
                  onChange={(e) =>
                    change({ template: e.target.value as Options["template"] })
                  }
                >
                  <option value="grid">Недельная сетка</option>
                  <option value="list">Список занятий</option>
                  <option value="overview">Сводный плакат</option>
                </select>
              </label>
              <label className="field">
                <span>Размер бумаги</span>
                <select
                  value={options.paper}
                  onChange={(e) =>
                    change({ paper: e.target.value as Options["paper"] })
                  }
                >
                  {["A4", "A3", "A2", "A1"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Ориентация</span>
                <select
                  value={options.landscape ? "landscape" : "portrait"}
                  onChange={(e) =>
                    change({ landscape: e.target.value === "landscape" })
                  }
                >
                  <option value="landscape">Альбомная</option>
                  <option value="portrait">Книжная</option>
                </select>
              </label>
              <label className="field">
                <span>Заголовок документа</span>
                <input
                  maxLength={160}
                  placeholder={s.settings.name}
                  value={options.title}
                  onChange={(e) => change({ title: e.target.value })}
                />
              </label>
            </div>
            {selection && (
              <fieldset>
                <legend>Выбранные записи: {options.entityIds.length}</legend>
                <label className="field">
                  <span>Поиск для экспорта</span>
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Название или имя"
                  />
                </label>
                <div className="button-row">
                  <button
                    onClick={() =>
                      change({
                        entityIds: [
                          ...new Set([
                            ...options.entityIds,
                            ...filtered.map((x) => x.id),
                          ]),
                        ],
                      })
                    }
                  >
                    Выбрать найденные
                  </button>
                  <button onClick={() => change({ entityIds: [] })}>
                    Снять выбор
                  </button>
                </div>
                <div className="export-picker">
                  {filtered.map((e) => (
                    <label key={e.id}>
                      <input
                        type="checkbox"
                        checked={options.entityIds.includes(e.id)}
                        onChange={(event) =>
                          change({
                            entityIds: event.target.checked
                              ? [...options.entityIds, e.id]
                              : options.entityIds.filter((id) => id !== e.id),
                          })
                        }
                      />
                      {e.name}
                    </label>
                  ))}
                </div>
                {!filtered.length && <p>Ничего не найдено.</p>}
              </fieldset>
            )}
            <fieldset>
              <legend>Недели в документе</legend>
              <div className="export-toggles">
                {patterns(s).map((c) => (
                  <label key={c.id}>
                    <input
                      type="checkbox"
                      checked={options.cycleIds.includes(c.id)}
                      onChange={(e) =>
                        change({
                          cycleIds: e.target.checked
                            ? [...options.cycleIds, c.id]
                            : options.cycleIds.filter((id) => id !== c.id),
                        })
                      }
                    />
                    {c.name}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="export-toggles">
              {(
                [
                  ["showTeachers", "Учителя"],
                  ["showRooms", "Кабинеты"],
                  ["showTimes", "Время занятий"],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={options[key]}
                    onChange={(e) => change({ [key]: e.target.checked })}
                  />
                  {label}
                </label>
              ))}
            </div>
            {options.template === "overview" && (
              <p className="info-line">
                Для большой сводной таблицы выберите A2 или A1. Размер и масштаб
                окончательно подтверждаются в диалоге печати.
              </p>
            )}
            <div className="button-row">
              <button
                className="primary"
                disabled={busy || empty}
                onClick={() => void run(() => file("html", true))}
              >
                Предпросмотр печати
              </button>
              <button
                disabled={busy || empty}
                onClick={() => void run(() => file("xlsx"))}
              >
                Скачать расписание XLSX
              </button>
              <button
                disabled={busy || empty}
                onClick={() => void run(() => file("html"))}
              >
                Скачать HTML
              </button>
            </div>
          </fieldset>
          <p className="muted">
            Это повторяющиеся шаблоны недель. Неразмещённая нагрузка и изменения
            на конкретные даты в документ не входят.
          </p>
        </section>
        {html && (
          <section className="export-card print-preview">
            <div className="section-heading">
              <h2>Предпросмотр документа</h2>
              <button
                className="primary"
                disabled={!printReady}
                onClick={() => {
                  frame.current?.contentWindow?.focus();
                  frame.current?.contentWindow?.print();
                }}
              >
                Печать / сохранить PDF
              </button>
            </div>
            <p>PDF сохраняется через системный диалог печати браузера.</p>
            <iframe
              ref={frame}
              title="Документ для печати"
              srcDoc={html}
              onLoad={() => setPrintReady(true)}
              sandbox="allow-same-origin allow-modals"
            />
          </section>
        )}
      </div>
      <div className="exchange-section" hidden={tab !== "backup"}>
        <section className="export-card">
          <h2>Обмен всей базой</h2>
          <p>
            Таблицы XLSX содержат все справочники, составы учеников, нагрузку,
            занятия, курсы и заявки. Структурированные поля записаны как JSON.
            XML-копия хранит всю базу в открытом формате Атласа и подходит для
            восстановления.
          </p>
          <div className="button-row">
            <button
              disabled={busy}
              onClick={() => void run(() => file("xlsx_data"))}
            >
              Скачать таблицы всей базы
            </button>
            <button disabled={busy} onClick={() => void run(() => file("xml"))}>
              Скачать XML-копию
            </button>
          </div>
          <p className="muted">
            XML Атласа не является XML aSc. Прямой импорт закрытого файла .roz
            не поддерживается.
          </p>
          <label className="field">
            <span>Восстановить из XML-копии Атласа</span>
            <input
              type="file"
              accept=".xml"
              disabled={busy}
              onChange={(e) => {
                const input = e.target,
                  picked = input.files?.[0];
                if (!picked) return;
                void run(async () => {
                  if (picked.size > 20_000_000)
                    throw new Error("Файл превышает 20 МБ.");
                  const xml = await picked.text();
                  setPreview(
                    await api("exchange/xml-preview", workspace, "POST", {
                      revision: s.revision,
                      xml,
                    }),
                  );
                });
                input.value = "";
              }}
            />
            <small>
              Восстановление заменит всю текущую базу после просмотра изменений.
              Предыдущая версия остаётся в истории.
            </small>
          </label>
        </section>
        {preview && (
          <ChangePreview
            title="Восстановление всей базы из XML"
            preview={preview}
            busy={busy}
            onCancel={() => setPreview(null)}
            onApply={() =>
              void run(async () => {
                await onState(
                  await api("editor/apply", workspace, "POST", {
                    revision: preview.revision,
                    token: preview.token,
                  }),
                );
              })
            }
          />
        )}
      </div>
    </div>
  );
}

import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Account } from "./auth";
import HallBoard from "./HallBoard";
import type { HallPayload } from "./hallScreen";
import { displayTime, schoolInput, schoolISO } from "./meetings";
import "./HallScreens.css";

type Link = {
  status: "none" | "active" | "expired";
  issued: string | null;
  expires: string | null;
  expiresOn: string | null;
};
type Screen = {
  id: string;
  version: number;
  status: "active" | "archived";
  name: string;
  buildingId: string | null;
  teacherNames: string;
  tomorrowFrom: number | null;
  pageSeconds: number;
  buildingName: string;
  buildingMissing: boolean;
  link: Link;
  lastSeen: string | null;
};
type Note = {
  id: string;
  version: number;
  state: "showing" | "scheduled" | "hidden" | "ended" | "withdrawn";
  title: string;
  text: string;
  startsAt: string;
  endsAt: string;
  visible: boolean;
  screenIds: string[];
  author: string;
  withdrawReason: string;
};
type Options = {
  screens: Screen[];
  announcements: Note[];
  buildings: { id: string; name: string }[];
  teacherNames: Record<string, string>;
  today: string;
  maxScreens: number;
};
type ScreenForm = {
  id?: string;
  version?: number;
  name: string;
  buildingId: string;
  teacherNames: string;
  tomorrowFrom: string;
  pageSeconds: string;
};
type NoteForm = {
  id?: string;
  version?: number;
  title: string;
  text: string;
  startsAt: string;
  endsAt: string;
  visible: boolean;
  screenIds: string[];
};
type Confirm = {
  kind: "revoke" | "archive" | "withdraw";
  id: string;
  name: string;
  version: number;
  reason: string;
};
const NOTE_STATE: Record<string, string> = {
  showing: "Показывается",
  scheduled: "Запланировано",
  hidden: "Скрыто",
  ended: "Завершено",
  withdrawn: "Снято",
};
const ACTIONS: Record<string, string> = {
  created: "Создано",
  updated: "Изменено",
  link_issued: "Выдана ссылка",
  link_revoked: "Ссылка отозвана",
  archived: "Экран убран",
  withdrawn: "Объявление снято",
};
const ruDate = (iso: string) => iso.split("-").reverse().join(".");
const addDays = (iso: string, n: number) =>
  new Date(Date.parse(iso + "T00:00:00Z") + n * 86400000)
    .toISOString()
    .slice(0, 10);
const linkText = (l: Link) =>
  l.status === "none"
    ? "Ссылка не выдана"
    : l.status === "expired"
      ? "Ссылка истекла"
      : l.expiresOn
        ? `Ссылка действует до ${ruDate(l.expiresOn)}`
        : "Ссылка без срока";
const seenText = (iso: string | null, today: string) =>
  !iso
    ? "Экран ещё не обращался"
    : schoolInput(iso).slice(0, 10) === today
      ? `Экран обращался в ${schoolInput(iso).slice(11)}`
      : `Экран обращался ${displayTime(iso)}`;
const blankScreen = (): ScreenForm => ({
  name: "",
  buildingId: "",
  teacherNames: "short",
  tomorrowFrom: "14",
  pageSeconds: "15",
});
const screenForm = (s: Screen): ScreenForm => ({
  id: s.id,
  version: s.version,
  name: s.name,
  // A deleted building cannot be chosen again: the screen then shows every building.
  buildingId: s.buildingMissing ? "" : s.buildingId || "",
  teacherNames: s.teacherNames,
  tomorrowFrom: s.tomorrowFrom === null ? "" : String(s.tomorrowFrom),
  pageSeconds: String(s.pageSeconds),
});
const blankNote = (): NoteForm => ({
  title: "",
  text: "",
  startsAt: schoolInput(new Date().toISOString()),
  endsAt: schoolInput(new Date(Date.now() + 86400000).toISOString()),
  visible: true,
  screenIds: [],
});
const noteForm = (n: Note): NoteForm => ({
  id: n.id,
  version: n.version,
  title: n.title,
  text: n.text,
  startsAt: schoolInput(n.startsAt),
  endsAt: schoolInput(n.endsAt),
  visible: n.visible,
  screenIds: n.screenIds,
});

/** A 1920x1080 board scaled into the width of its container. */
function Scaled({ children }: { children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.4);
  useEffect(() => {
    const fit = () =>
      box.current && setScale(Math.max(0.1, box.current.clientWidth / 1920));
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);
  return (
    <div className="hs-scaled" ref={box} style={{ height: 1080 * scale }}>
      <div
        style={{
          width: 1920,
          height: 1080,
          transform: `scale(${scale})`,
          transformOrigin: "0 0",
        }}
      >
        {children}
      </div>
    </div>
  );
}

function History({
  workspace,
  entity,
  id,
  version,
}: {
  workspace: string;
  entity: "screen" | "announcement";
  id: string;
  version: number;
}) {
  const [open, setOpen] = useState(false),
    [rows, setRows] = useState<
      | { created: string; actor: string; action: string; reason: string }[]
      | null
    >(null),
    [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let live = true;
    api(`hall/history?entity=${entity}&id=${encodeURIComponent(id)}`, workspace)
      .then((r) => {
        if (live) {
          setRows(r.items);
          setError("");
        }
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [open, workspace, entity, id, version]);
  return (
    <details
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>История</summary>
      {error && <p role="alert">{error}</p>}
      {rows &&
        (rows.length ? (
          <ol className="hs-history">
            {rows.map((r, i) => (
              <li key={i}>
                {displayTime(r.created)} · {r.actor} ·{" "}
                {ACTIONS[r.action] || r.action}
                {r.reason ? ` — ${r.reason}` : ""}
              </li>
            ))}
          </ol>
        ) : (
          <p>Записей нет.</p>
        ))}
    </details>
  );
}

export default function HallScreens({
  initialWorkspace,
  onClose,
}: {
  user: Account;
  initialWorkspace: string;
  onClose: () => void;
}) {
  const [workspace, setWorkspace] = useState(
    ["school", "demo"].includes(initialWorkspace) ? initialWorkspace : "school",
  );
  const [options, setOptions] = useState<Options | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [tab, setTab] = useState<"screens" | "notes">("screens");
  const [form, setForm] = useState<ScreenForm | null>(null),
    [note, setNote] = useState<NoteForm | null>(null),
    [linkPanel, setLinkPanel] = useState<{
      id: string;
      name: string;
      version: number;
      expiresOn: string;
    } | null>(null),
    [issued, setIssued] = useState<{ name: string; url: string } | null>(null),
    [confirm, setConfirm] = useState<Confirm | null>(null),
    [preview, setPreview] = useState<{
      name: string;
      data: HallPayload;
    } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null),
    sequence = useRef(0),
    replay = useRef({ key: "", id: "" });
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  const unsaved = Boolean(form || note);
  const leave = () =>
    !unsaved ||
    window.confirm(
      "Есть несохранённые изменения. Отбросить их и перейти дальше?",
    );
  function clear() {
    setForm(null);
    setNote(null);
    setLinkPanel(null);
    setIssued(null);
    setConfirm(null);
    setPreview(null);
  }
  async function load() {
    const n = ++sequence.current;
    const value: Options = await api("hall/options", workspace);
    if (n === sequence.current) setOptions(value);
    return value;
  }
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e: any) {
      setError(
        (e.message || "Не удалось выполнить действие.") +
          (e.status === 409 && !/обновлён/.test(e.message)
            ? " Список обновлён."
            : ""),
      );
      if ([401, 403, 404].includes(e.status)) {
        clear();
        setOptions(null);
      } else if (e.status === 409) {
        try {
          await load();
        } catch {
          // The message above already tells the administrator to reload.
        }
      }
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    clear();
    setOptions(null);
    void run(async () => {
      await load();
    });
    return () => {
      ++sequence.current;
    };
  }, [workspace]);
  // Creation keeps its request id for retries of the same unsaved form.
  async function create(path: string, body: object) {
    const key = JSON.stringify([workspace, path, body]);
    if (replay.current.key !== key)
      replay.current = { key, id: crypto.randomUUID() };
    const result = await api("hall/" + path, workspace, "POST", {
      ...body,
      requestId: replay.current.id,
    });
    replay.current = { key: "", id: "" };
    return result;
  }
  async function saveScreen() {
    if (!form) return;
    const seconds = Number(form.pageSeconds);
    if (!form.name.trim()) throw new Error("Укажите название экрана.");
    if (!Number.isInteger(seconds) || seconds < 8 || seconds > 60)
      throw new Error("Смена страниц — от 8 до 60 секунд.");
    const body = {
      name: form.name.trim(),
      buildingId: form.buildingId || null,
      teacherNames: form.teacherNames,
      tomorrowFrom: form.tomorrowFrom === "" ? null : Number(form.tomorrowFrom),
      pageSeconds: seconds,
    };
    if (form.id)
      await api(`hall/screens/${form.id}`, workspace, "PUT", {
        ...body,
        version: form.version,
      });
    else await create("screens", body);
    setNotice(
      form.id ? "Экран сохранён." : "Экран создан. Теперь выдайте ссылку.",
    );
    setForm(null);
    await load();
  }
  async function issueLink() {
    if (!linkPanel) return;
    try {
      const r = await api(
        `hall/screens/${linkPanel.id}/link`,
        workspace,
        "POST",
        {
          version: linkPanel.version,
          expiresOn: linkPanel.expiresOn || null,
        },
      );
      setIssued({
        name: linkPanel.name,
        url: new URL(r.path, location.origin).href,
      });
      setLinkPanel(null);
      await load();
    } catch (e: any) {
      if (e.status === 409) {
        // The link may already exist from a lost answer or another window: offer a fresh one.
        const fresh = (await load()).screens.find((s) => s.id === linkPanel.id);
        if (fresh?.status === "active")
          setLinkPanel({ ...linkPanel, version: fresh.version });
        else setLinkPanel(null);
        throw Object.assign(
          new Error(
            "Экран изменился: возможно, ссылка уже была выдана. Список обновлён. Нажмите «Выдать ссылку» ещё раз, если нужна новая; прежняя тогда перестанет работать.",
          ),
          { status: 0 },
        );
      }
      throw e;
    }
  }
  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setNotice("Ссылка скопирована.");
    } catch {
      setNotice(
        "Не удалось скопировать. Выделите ссылку и скопируйте вручную.",
      );
    }
  }
  async function decide() {
    if (!confirm) return;
    const reason = confirm.reason.trim();
    if (confirm.kind !== "revoke" && !reason)
      throw new Error("Укажите причину.");
    const path =
      confirm.kind === "withdraw"
        ? `hall/announcements/${confirm.id}/withdraw`
        : `hall/screens/${confirm.id}/${confirm.kind}`;
    await api(path, workspace, "POST", { version: confirm.version, reason });
    setNotice(
      confirm.kind === "revoke"
        ? "Ссылка отозвана. Телевизор перестанет показывать данные при ближайшем обновлении."
        : confirm.kind === "archive"
          ? "Экран убран."
          : "Объявление снято.",
    );
    setConfirm(null);
    await load();
  }
  async function saveNote() {
    if (!note) return;
    if (!note.title.trim()) throw new Error("Укажите заголовок объявления.");
    const body = {
      title: note.title.trim(),
      text: note.text.trim(),
      startsAt: schoolISO(note.startsAt),
      endsAt: schoolISO(note.endsAt),
      visible: note.visible,
      screenIds: note.screenIds,
    };
    if (note.id)
      await api(`hall/announcements/${note.id}`, workspace, "PUT", {
        ...body,
        version: note.version,
      });
    else await create("announcements", body);
    setNotice(note.id ? "Объявление сохранено." : "Объявление создано.");
    setNote(null);
    await load();
  }
  const active = options?.screens.filter((s) => s.status === "active") || [],
    archived = options?.screens.filter((s) => s.status === "archived") || [];
  const screenName = (id: string) =>
    options?.screens.find((s) => s.id === id)?.name || "убранный экран";
  const today = options?.today || "";
  const noteScreens = note
    ? (options?.screens || []).filter(
        (s) => s.status === "active" || note.screenIds.includes(s.id),
      )
    : [];
  return (
    <dialog
      className="hall-screens"
      ref={dialog}
      aria-labelledby="hall-screens-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy && leave()) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="hall-screens-title">Информационные экраны</h2>
          <p>Телевизоры в холле: изменения расписания и объявления</p>
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
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {busy && <p role="status">Загрузка…</p>}
      <div className="hs-controls">
        <label>
          Пространство
          <select
            value={workspace}
            disabled={busy}
            onChange={(e) => {
              if (leave()) setWorkspace(e.target.value);
            }}
          >
            <option value="school">Школа</option>
            <option value="demo">Демонстрация</option>
          </select>
        </label>
        <button
          disabled={busy}
          onClick={() => {
            if (leave()) {
              clear();
              void run(async () => {
                await load();
              });
            }
          }}
        >
          Обновить
        </button>
      </div>
      <nav className="hs-tabs" aria-label="Разделы информационных экранов">
        {(
          [
            ["screens", "Экраны"],
            ["notes", "Объявления"],
          ] as const
        ).map(([id, label]) => (
          <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>
      {options && tab === "screens" && (
        <section aria-label="Экраны">
          <p>
            Действующих экранов: {active.length} из {options.maxScreens}. Ссылка
            экрана работает как пароль: открывайте её только на телевизоре
            школы.
          </p>
          {issued && (
            <section
              className="hs-panel"
              role="group"
              aria-label="Новая ссылка"
            >
              <h3>Ссылка для экрана «{issued.name}»</h3>
              <p>
                Ссылка показывается один раз. Откройте её на телевизоре в
                полноэкранном режиме. Новая ссылка отключает прежнюю.
              </p>
              <div className="hs-copy">
                <input
                  readOnly
                  value={issued.url}
                  aria-label="Ссылка экрана"
                  onFocus={(e) => e.target.select()}
                />
                <button onClick={() => void copy(issued.url)}>
                  Скопировать
                </button>
                <button onClick={() => setIssued(null)}>Готово</button>
              </div>
            </section>
          )}
          {linkPanel && (
            <section
              className="hs-panel"
              role="group"
              aria-label="Выдача ссылки"
            >
              <h3>Ссылка для экрана «{linkPanel.name}»</h3>
              <label>
                Действует до (необязательно)
                <input
                  type="date"
                  min={today}
                  max={addDays(today, 366)}
                  value={linkPanel.expiresOn}
                  onChange={(e) =>
                    setLinkPanel({ ...linkPanel, expiresOn: e.target.value })
                  }
                />
              </label>
              <p>
                Без срока ссылка работает, пока её не отозвали. Прежняя ссылка
                этого экрана перестанет работать.
              </p>
              <div className="hs-actions">
                <button disabled={busy} onClick={() => void run(issueLink)}>
                  Выдать ссылку
                </button>
                <button onClick={() => setLinkPanel(null)}>Отмена</button>
              </div>
            </section>
          )}
          {confirm && confirm.kind !== "withdraw" && (
            <section
              className="hs-panel"
              role="group"
              aria-label="Подтверждение"
            >
              <h3>
                {confirm.kind === "revoke"
                  ? `Отозвать ссылку экрана «${confirm.name}»?`
                  : `Убрать экран «${confirm.name}»?`}
              </h3>
              <p>
                {confirm.kind === "revoke"
                  ? "Телевизор перестанет показывать данные при ближайшем обновлении. Выдайте новую ссылку, чтобы вернуть экран."
                  : "Ссылка перестанет работать, экран исчезнет из списка действующих."}
              </p>
              <label>
                {confirm.kind === "revoke"
                  ? "Причина (необязательно)"
                  : "Причина"}
                <textarea
                  maxLength={500}
                  value={confirm.reason}
                  onChange={(e) =>
                    setConfirm({ ...confirm, reason: e.target.value })
                  }
                />
              </label>
              <div className="hs-actions">
                <button disabled={busy} onClick={() => void run(decide)}>
                  Подтвердить
                </button>
                <button onClick={() => setConfirm(null)}>Отмена</button>
              </div>
            </section>
          )}
          {preview && (
            <section
              className="hs-panel"
              role="group"
              aria-label="Предпросмотр"
            >
              <h3>Предпросмотр экрана «{preview.name}»</h3>
              <p>
                Так экран выглядит сейчас. Времена и страницы меняются сами.
              </p>
              <Scaled>
                <HallBoard
                  data={preview.data}
                  clock={Date.parse(preview.data.serverTime)}
                  offline={false}
                  fixed
                />
              </Scaled>
              <div className="hs-actions">
                <button onClick={() => setPreview(null)}>
                  Закрыть предпросмотр
                </button>
              </div>
            </section>
          )}
          {form ? (
            <form
              className="hs-form"
              onSubmit={(e) => {
                e.preventDefault();
                void run(saveScreen);
              }}
            >
              <h3>{form.id ? "Изменить экран" : "Новый экран"}</h3>
              <label>
                Название
                <input
                  required
                  maxLength={80}
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </label>
              <label>
                Здание
                <select
                  value={form.buildingId}
                  onChange={(e) =>
                    setForm({ ...form, buildingId: e.target.value })
                  }
                >
                  <option value="">Все здания</option>
                  {options.buildings.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Имена учителей
                <select
                  value={form.teacherNames}
                  onChange={(e) =>
                    setForm({ ...form, teacherNames: e.target.value })
                  }
                >
                  {Object.entries(options.teacherNames).map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Показывать завтрашние изменения с
                <select
                  value={form.tomorrowFrom}
                  onChange={(e) =>
                    setForm({ ...form, tomorrowFrom: e.target.value })
                  }
                >
                  <option value="">Не показывать</option>
                  {Array.from({ length: 24 }, (_, h) => (
                    <option key={h} value={String(h)}>
                      {h}:00
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Смена страниц, секунд
                <input
                  type="number"
                  min={8}
                  max={60}
                  required
                  value={form.pageSeconds}
                  onChange={(e) =>
                    setForm({ ...form, pageSeconds: e.target.value })
                  }
                />
              </label>
              <div className="hs-actions">
                <button disabled={busy}>Сохранить</button>
                <button type="button" onClick={() => setForm(null)}>
                  Отмена
                </button>
              </div>
            </form>
          ) : (
            <div className="hs-actions">
              <button
                disabled={busy || active.length >= options.maxScreens}
                onClick={() => {
                  setNotice("");
                  setForm(blankScreen());
                }}
              >
                Новый экран
              </button>
            </div>
          )}
          {active.length === 0 && <p>Действующих экранов нет.</p>}
          <div className="hs-cards">
            {active.map((s) => (
              <article className="hs-card" key={s.id}>
                <h3>{s.name}</h3>
                <p>
                  {s.buildingName || "Все здания"}
                  {s.buildingMissing && (
                    <strong className="hs-warn">
                      {" "}
                      · здание удалено из школы, экран показывает все здания
                    </strong>
                  )}
                </p>
                <p>
                  Имена учителей:{" "}
                  {options.teacherNames[s.teacherNames] || s.teacherNames}
                  {" · "}
                  {s.tomorrowFrom === null
                    ? "Только сегодня"
                    : `Завтра с ${s.tomorrowFrom}:00`}
                </p>
                <p>
                  {linkText(s.link)}
                  {s.link.status === "active" &&
                    " · " + seenText(s.lastSeen, today)}
                </p>
                <div className="hs-actions">
                  <button
                    disabled={busy}
                    onClick={() => {
                      setNotice("");
                      setForm(screenForm(s));
                    }}
                  >
                    Изменить
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setIssued(null);
                      setLinkPanel({
                        id: s.id,
                        name: s.name,
                        version: s.version,
                        expiresOn: "",
                      });
                    }}
                  >
                    {s.link.status === "none"
                      ? "Выдать ссылку"
                      : "Выдать новую ссылку"}
                  </button>
                  {s.link.status !== "none" && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        setConfirm({
                          kind: "revoke",
                          id: s.id,
                          name: s.name,
                          version: s.version,
                          reason: "",
                        })
                      }
                    >
                      Отозвать ссылку
                    </button>
                  )}
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        const data = await api(
                          `hall/screens/${s.id}/preview`,
                          workspace,
                        );
                        setPreview({ name: s.name, data });
                      })
                    }
                  >
                    Предпросмотр
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      setConfirm({
                        kind: "archive",
                        id: s.id,
                        name: s.name,
                        version: s.version,
                        reason: "",
                      })
                    }
                  >
                    Убрать экран
                  </button>
                </div>
                <History
                  workspace={workspace}
                  entity="screen"
                  id={s.id}
                  version={s.version}
                />
              </article>
            ))}
          </div>
          {archived.length > 0 && (
            <details>
              <summary>Убранные экраны ({archived.length})</summary>
              <div className="hs-cards">
                {archived.map((s) => (
                  <article className="hs-card" key={s.id}>
                    <h3>{s.name}</h3>
                    <History
                      workspace={workspace}
                      entity="screen"
                      id={s.id}
                      version={s.version}
                    />
                  </article>
                ))}
              </div>
            </details>
          )}
        </section>
      )}
      {options && tab === "notes" && (
        <section aria-label="Объявления">
          <p>
            На экране одновременно не больше десяти объявлений, они сменяют друг
            друга. Время указывается школьное (UTC+5).
          </p>
          {confirm?.kind === "withdraw" && (
            <section
              className="hs-panel"
              role="group"
              aria-label="Подтверждение"
            >
              <h3>Снять объявление «{confirm.name}»?</h3>
              <p>Объявление исчезнет со всех экранов.</p>
              <label>
                Причина
                <textarea
                  maxLength={500}
                  value={confirm.reason}
                  onChange={(e) =>
                    setConfirm({ ...confirm, reason: e.target.value })
                  }
                />
              </label>
              <div className="hs-actions">
                <button disabled={busy} onClick={() => void run(decide)}>
                  Подтвердить
                </button>
                <button onClick={() => setConfirm(null)}>Отмена</button>
              </div>
            </section>
          )}
          {note ? (
            <form
              className="hs-form"
              onSubmit={(e) => {
                e.preventDefault();
                void run(saveNote);
              }}
            >
              <h3>{note.id ? "Изменить объявление" : "Новое объявление"}</h3>
              <label>
                Заголовок
                <input
                  required
                  maxLength={120}
                  value={note.title}
                  onChange={(e) => setNote({ ...note, title: e.target.value })}
                />
              </label>
              <label>
                Текст ({note.text.length} из 600)
                <textarea
                  maxLength={600}
                  value={note.text}
                  onChange={(e) => setNote({ ...note, text: e.target.value })}
                />
              </label>
              <label>
                Начало (время школы, UTC+5)
                <input
                  type="datetime-local"
                  required
                  value={note.startsAt}
                  onChange={(e) =>
                    setNote({ ...note, startsAt: e.target.value })
                  }
                />
              </label>
              <label>
                Окончание (время школы, UTC+5)
                <input
                  type="datetime-local"
                  required
                  value={note.endsAt}
                  onChange={(e) => setNote({ ...note, endsAt: e.target.value })}
                />
              </label>
              <label className="hs-check">
                <input
                  type="checkbox"
                  checked={note.visible}
                  onChange={(e) =>
                    setNote({ ...note, visible: e.target.checked })
                  }
                />
                Показывать на экране
              </label>
              <fieldset>
                <legend>Экраны</legend>
                <p>Не выбрано — на всех экранах</p>
                {noteScreens.map((s) => (
                  <label className="hs-check" key={s.id}>
                    <input
                      type="checkbox"
                      checked={note.screenIds.includes(s.id)}
                      onChange={(e) =>
                        setNote({
                          ...note,
                          screenIds: e.target.checked
                            ? [...note.screenIds, s.id]
                            : note.screenIds.filter((id) => id !== s.id),
                        })
                      }
                    />
                    {s.name}
                    {s.status === "archived" && " (экран убран)"}
                  </label>
                ))}
              </fieldset>
              <div className="hs-actions">
                <button disabled={busy}>Сохранить</button>
                <button type="button" onClick={() => setNote(null)}>
                  Отмена
                </button>
              </div>
            </form>
          ) : (
            <div className="hs-actions">
              <button
                disabled={busy}
                onClick={() => {
                  setNotice("");
                  setNote(blankNote());
                }}
              >
                Новое объявление
              </button>
            </div>
          )}
          {options.announcements.length === 0 && <p>Объявлений нет.</p>}
          <div className="hs-cards">
            {options.announcements.map((n) => (
              <article className="hs-card" key={n.id}>
                <h3>
                  {n.title}{" "}
                  <span className={"hs-badge hs-" + n.state}>
                    {NOTE_STATE[n.state]}
                  </span>
                </h3>
                <p>
                  {displayTime(n.startsAt)} — {displayTime(n.endsAt)}
                </p>
                {n.text && <p className="hs-prose">{n.text}</p>}
                <p>
                  Экраны:{" "}
                  {n.screenIds.length
                    ? n.screenIds.map(screenName).join(", ")
                    : "все"}
                  {" · "}Автор: {n.author}
                </p>
                {n.withdrawReason && <p>Причина снятия: {n.withdrawReason}</p>}
                {n.state !== "withdrawn" && (
                  <div className="hs-actions">
                    <button
                      disabled={busy}
                      onClick={() => {
                        setNotice("");
                        setNote(noteForm(n));
                      }}
                    >
                      Изменить
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        setConfirm({
                          kind: "withdraw",
                          id: n.id,
                          name: n.title,
                          version: n.version,
                          reason: "",
                        })
                      }
                    >
                      Снять
                    </button>
                  </div>
                )}
                <History
                  workspace={workspace}
                  entity="announcement"
                  id={n.id}
                  version={n.version}
                />
              </article>
            ))}
          </div>
        </section>
      )}
    </dialog>
  );
}

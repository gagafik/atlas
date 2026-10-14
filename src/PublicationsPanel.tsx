import { useEffect, useState } from "react";
import { api } from "./api";
import { Multi } from "./DatedSchedule";
import type { State } from "./types";

type View = "class" | "teacher" | "room" | "group" | "student";
interface Options {
  name: string;
  kind: "weekly" | "dated";
  view: View;
  entityIds: string[];
  cycleIds: string[];
  firstDate: string;
  lastDate: string;
  expiresInDays: number;
  template: "grid" | "list" | "overview";
  showTeachers: boolean;
  showRooms: boolean;
  showTimes: boolean;
}
interface Preview {
  token: string;
  revision: number;
  html: string;
  records: number;
  rows: number;
  expiresInDays: number;
}
interface Publication {
  id: string;
  name: string;
  revision: number;
  created: string;
  expires: string;
  revoked: string | null;
  status: "active" | "expired" | "revoked";
  url: string;
  records: number;
  rows: number;
  kind: "weekly" | "dated";
}
export default function PublicationsPanel({
  state: s,
  workspace,
  defaultCycle,
}: {
  state: State;
  workspace: string;
  defaultCycle: string;
}) {
  const [options, setOptions] = useState<Options>({
    name: "Расписание",
    kind: "weekly",
    view: "class",
    entityIds: [],
    cycleIds: [defaultCycle],
    firstDate: s.settings.yearStart,
    lastDate: s.settings.yearStart,
    expiresInDays: 30,
    template: "grid",
    showTeachers: true,
    showRooms: true,
    showTimes: true,
  });
  const [preview, setPreview] = useState<Preview | null>(null),
    [publications, setPublications] = useState<Publication[]>([]);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [listLoading, setListLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setPreview(null);
    setError("");
    setNotice("");
    setListLoading(true);
    api("publications", workspace)
      .then((r) => {
        if (alive) setPublications(r);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      })
      .finally(() => {
        if (alive) setListLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [workspace, s.revision]);
  const tables = {
    class: s.classes,
    teacher: s.teachers,
    room: s.rooms,
    group: s.groups,
    student: s.students.filter((x) => x.active),
  };
  function change(patch: Partial<Options>) {
    setOptions((o) => ({ ...o, ...patch }));
    setPreview(null);
    setError("");
    setNotice("");
  }
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function inspect() {
    const body = {
      ...options,
      firstDate: options.kind === "dated" ? options.firstDate : null,
      lastDate: options.kind === "dated" ? options.lastDate : null,
      revision: s.revision,
    };
    setPreview(await api("publications/preview", workspace, "POST", body));
  }
  async function publish() {
    if (!preview) return;
    const item = await api("publications", workspace, "POST", {
      revision: preview.revision,
      token: preview.token,
    });
    setPublications((items) => [item, ...items]);
    setPreview(null);
    setNotice("Снимок опубликован. Ссылка доступна в списке ниже.");
  }
  async function revoke(item: Publication) {
    await api(
      `publications/${encodeURIComponent(item.id)}`,
      workspace,
      "DELETE",
    );
    setPublications((items) =>
      items.map((x) => (x.id === item.id ? { ...x, status: "revoked" } : x)),
    );
    setNotice(
      `Ссылка «${item.name}» отозвана. Сохранённые получателями копии остаются у них.`,
    );
  }
  return (
    <div className="publications-panel dated-panel">
      <p className="info-line">
        Публикация — отдельный снимок выбранных расписаний. Ссылку сможет
        открыть каждый, у кого она есть. На текущем локальном сервере она
        работает на этом компьютере; доступ с других устройств требует настройки
        сервера.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="info-line" role="status">
          {notice}
        </p>
      )}
      <form
        className="dated-form"
        onSubmit={(e) => {
          e.preventDefault();
          void run(inspect);
        }}
      >
        <h3>Подготовить публикацию</h3>
        <fieldset className="export-settings" disabled={busy}>
          <div className="dated-fields">
            <label className="field">
              <span>Название публикации</span>
              <input
                required
                maxLength={160}
                value={options.name}
                onChange={(e) => change({ name: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Основа документа</span>
              <select
                value={options.kind}
                onChange={(e) =>
                  change({ kind: e.target.value as Options["kind"] })
                }
              >
                <option value="weekly">Повторяющаяся неделя</option>
                <option value="dated">Фактические занятия по датам</option>
              </select>
            </label>
            <label className="field">
              <span>Для кого публикуем</span>
              <select
                value={options.view}
                onChange={(e) =>
                  change({ view: e.target.value as View, entityIds: [] })
                }
              >
                <option value="class">Классы</option>
                <option value="teacher">Учителя</option>
                <option value="room">Кабинеты</option>
                <option value="group">Группы</option>
                <option value="student">Ученики</option>
              </select>
            </label>
            <label className="field">
              <span>Срок действия ссылки, дней</span>
              <input
                type="number"
                min={1}
                max={365}
                required
                value={options.expiresInDays}
                onChange={(e) =>
                  change({ expiresInDays: Number(e.target.value) })
                }
              />
            </label>
          </div>
          <Multi
            label="Выбранные расписания"
            items={tables[options.view]}
            values={options.entityIds}
            onChange={(v) => change({ entityIds: v })}
          />
          {options.kind === "weekly" ? (
            <>
              <Multi
                label="Недели для публикации"
                items={
                  s.cycles?.patterns || [
                    { id: "weekly", name: "Каждая неделя" },
                  ]
                }
                values={options.cycleIds}
                onChange={(v) => change({ cycleIds: v })}
              />
              <label className="field">
                <span>Вид документа</span>
                <select
                  value={options.template}
                  onChange={(e) =>
                    change({ template: e.target.value as Options["template"] })
                  }
                >
                  <option value="grid">Сетка недели</option>
                  <option value="list">Список</option>
                  <option value="overview">Сводная таблица</option>
                </select>
              </label>
            </>
          ) : (
            <div className="dated-fields">
              <label className="field">
                <span>Первая дата публикации</span>
                <input
                  type="date"
                  required
                  min={s.settings.yearStart}
                  max={s.settings.yearEnd}
                  value={options.firstDate}
                  onChange={(e) => change({ firstDate: e.target.value })}
                />
              </label>
              <label className="field">
                <span>Последняя дата публикации</span>
                <input
                  type="date"
                  required
                  min={options.firstDate}
                  max={s.settings.yearEnd}
                  value={options.lastDate}
                  onChange={(e) => change({ lastDate: e.target.value })}
                />
              </label>
              <p className="muted">
                До 31 дня. Включены замены, отмены, разовые занятия,
                бронирования и дежурства на момент подготовки.
              </p>
            </div>
          )}
          <div className="dated-toolbar">
            <label className="check-line">
              <input
                type="checkbox"
                checked={options.showTeachers}
                onChange={(e) => change({ showTeachers: e.target.checked })}
              />
              Показывать учителей
            </label>
            <label className="check-line">
              <input
                type="checkbox"
                checked={options.showRooms}
                onChange={(e) => change({ showRooms: e.target.checked })}
              />
              Показывать кабинеты
            </label>
            <label className="check-line">
              <input
                type="checkbox"
                checked={options.showTimes}
                onChange={(e) => change({ showTimes: e.target.checked })}
              />
              Показывать время
            </label>
          </div>
          <p className="muted">
            В документ входят выбранные названия и подписи уроков. Списки
            учеников, причины отсутствия, примечания и исходные файлы не
            публикуются. В личном расписании отображается имя выбранного
            ученика.
          </p>
          <button
            className="primary"
            disabled={
              !options.entityIds.length ||
              options.entityIds.length > 200 ||
              (options.kind === "weekly" && !options.cycleIds.length)
            }
          >
            {busy ? "Подготавливаю…" : "Посмотреть перед публикацией"}
          </button>
        </fieldset>
      </form>
      {preview && (
        <section className="publication-preview export-card">
          <h3>Документ для получателя</h3>
          <p>
            Версия {preview.revision} · расписаний {preview.records} · записей{" "}
            {preview.rows}. Ссылка будет действовать {preview.expiresInDays}{" "}
            дней после публикации.
          </p>
          <iframe
            title="Предпросмотр публикации"
            sandbox=""
            srcDoc={preview.html}
          />
          <div className="dated-toolbar">
            <button disabled={busy} onClick={() => setPreview(null)}>
              Отбросить документ
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() => void run(publish)}
            >
              Опубликовать этот снимок
            </button>
          </div>
        </section>
      )}
      <section className="publication-history">
        <h3>Опубликованные снимки</h3>
        {listLoading ? (
          <p role="status">Загружаю список…</p>
        ) : (
          !publications.length && (
            <p className="empty-state">Публикаций пока нет.</p>
          )
        )}
        {publications.map((item) => (
          <article className="export-card" key={item.id}>
            <h4>{item.name}</h4>
            <p>
              {item.status === "active"
                ? "Действует"
                : item.status === "revoked"
                  ? "Отозвана"
                  : "Срок истёк"}{" "}
              · версия {item.revision} · расписаний {item.records} · записей{" "}
              {item.rows}
            </p>
            <p className="muted">
              Создано {new Date(item.created).toLocaleString("ru")} · до{" "}
              {new Date(item.expires).toLocaleString("ru")}
            </p>
            {item.status === "active" && (
              <>
                <label className="field">
                  <span>Ссылка на {item.name}</span>
                  <input
                    readOnly
                    value={new URL(item.url, location.origin).href}
                    onFocus={(e) => e.target.select()}
                  />
                </label>
                <div className="dated-toolbar">
                  <a
                    className="button"
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Открыть публикацию
                  </a>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await navigator.clipboard.writeText(
                          new URL(item.url, location.origin).href,
                        );
                        setNotice("Ссылка скопирована.");
                      })
                    }
                  >
                    Скопировать ссылку
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => void run(() => revoke(item))}
                  >
                    Отозвать ссылку
                  </button>
                </div>
              </>
            )}
          </article>
        ))}
      </section>
    </div>
  );
}

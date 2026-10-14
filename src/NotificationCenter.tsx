import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { startPolling } from "./polling";

interface Notice {
  id: number;
  workspace: string;
  revision: number;
  created: string;
  action: string;
  read: boolean;
}
interface Inbox {
  items: Notice[];
  unread: number;
  latestId: number;
  nextBeforeId: number | null;
  enabled: boolean;
}
const titles: Record<string, string> = {
  "absence.submitted": "Поступило заявление об отсутствии",
  "absence.updated": "Изменилось решение по объяснительной",
  "message.received": "Новое сообщение",
  "message.reported": "Получена жалоба на сообщение",
  "message.moderated": "Изменилось решение по жалобе на сообщение",
  "family.updated": "Изменились сведения или доступ к ребёнку",
  "journal.updated": "Обновлён учебный журнал",
  "journal.finals_published": "Опубликованы итоговые оценки",
  "journal.finals_withdrawn": "Отозваны итоговые оценки",
  "journal.withdrawn": "Запись журнала скрыта",
  "homework.submitted": "Домашняя работа отправлена на проверку",
  "homework.withdrawn": "Ученик отозвал домашнюю работу",
  "homework.reviewed": "Обновлён результат проверки домашней работы",
  "homework.deadline_changed": "Изменён индивидуальный срок домашней работы",
  "quiz.published": "Назначен новый тест",
  "quiz.submitted": "Тест отправлен на проверку",
  "quiz.close": "Приём ответов на тест завершён",
  "quiz.withdraw": "Назначение теста отозвано",
  "quiz.release": "Открыт разбор теста",
  "quiz.results_updated": "Исправлен результат теста",
  saved: "Расписание обновлено",
  undo: "Изменение расписания отменено",
  redo: "Изменение расписания повторено",
};

export default function NotificationCenter({ identity }: { identity: string }) {
  const [open, setOpen] = useState(false),
    [inbox, setInbox] = useState<Inbox | null>(null);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [unreadOnly, setUnreadOnly] = useState(false);
  const [browserOn, setBrowserOn] = useState(false),
    [permission, setPermission] = useState(
      typeof Notification === "undefined"
        ? "unsupported"
        : Notification.permission,
    );
  const dialog = useRef<HTMLDialogElement>(null),
    request = useRef(0),
    latest = useRef<number | null>(null);
  const storageKey = `atlas-alerts:${identity}`;
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.data?.type === "atlas-open-notifications") setOpen(true);
    };
    navigator.serviceWorker?.addEventListener("message", receive);
    if (new URLSearchParams(location.search).get("notifications") === "1") {
      setOpen(true);
      const url = new URL(location.href);
      url.searchParams.delete("notifications");
      history.replaceState(null, "", url);
    }
    return () =>
      navigator.serviceWorker?.removeEventListener("message", receive);
  }, []);
  useEffect(() => {
    try {
      setBrowserOn(sessionStorage.getItem(storageKey) === "on");
    } catch {
      /* Session storage may be disabled. */
    }
  }, [storageKey]);
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    let live = true;
    async function update() {
      const sequence = ++request.current;
      try {
        const data: Inbox = await api(
          `notifications/inbox?unreadOnly=${unreadOnly}`,
          "school",
        );
        if (!live || sequence !== request.current) return;
        setInbox(data);
        setError("");
        const fresh =
          latest.current !== null &&
          data.items.some((n) => n.id > latest.current! && !n.read);
        latest.current = Math.max(latest.current || 0, data.latestId);
        if (
          fresh &&
          browserOn &&
          data.enabled &&
          typeof Notification !== "undefined" &&
          Notification.permission === "granted"
        ) {
          const registration = await navigator.serviceWorker?.getRegistration();
          if (!live) return;
          if (registration?.active)
            await registration.showNotification("Атлас: есть изменения", {
              body: "Откройте приложение и проверьте список уведомлений.",
              icon: "/icons/atlas-192.png",
              tag: `atlas-${identity}`,
            });
        }
        return true;
      } catch (e: any) {
        if (live) setError(e.message);
        return false;
      }
    }
    // Reading older pages is explicit; do not replace them while the dialog is open.
    const polling = startPolling(update, {
      interval: 25000,
      paused: () => open,
      // Preserve explicitly enabled alerts while an inactive tab remains open.
      background: () =>
        browserOn &&
        typeof Notification !== "undefined" &&
        Notification.permission === "granted",
    });
    return () => {
      live = false;
      polling.stop();
    };
  }, [identity, unreadOnly, open, browserOn]);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    ++request.current;
    try {
      await action();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function mark(ids?: number[]) {
    if (!inbox?.latestId) return;
    await api(
      "notifications/read",
      "school",
      "POST",
      ids ? { ids } : { throughId: inbox.latestId },
    );
    setInbox(
      await api(`notifications/inbox?unreadOnly=${unreadOnly}`, "school"),
    );
  }
  async function toggleBrowser() {
    if (browserOn) {
      setBrowserOn(false);
      try {
        sessionStorage.removeItem(storageKey);
      } catch {
        /* Optional preference. */
      }
      return;
    }
    if (typeof Notification === "undefined" || !window.isSecureContext) {
      setError(
        "Уведомления браузера здесь недоступны. Используйте HTTPS или этот компьютер сервера.",
      );
      return;
    }
    const value = await Notification.requestPermission();
    setPermission(value);
    if (value === "granted") {
      const registration = await navigator.serviceWorker?.getRegistration();
      if (!registration?.active) {
        setError(
          "Оболочка приложения ещё не готова. Перезагрузите страницу и повторите.",
        );
        return;
      }
      setBrowserOn(true);
      try {
        sessionStorage.setItem(storageKey, "on");
      } catch {
        /* Permission still works in this tab. */
      }
    }
  }
  return (
    <>
      <button
        className="notifications-button"
        onClick={() => setOpen(true)}
        aria-label={`Уведомления${inbox?.unread ? `, непрочитанных: ${inbox.unread}` : ""}`}
      >
        Уведомления {inbox && inbox.unread > 0 && <b>{inbox.unread}</b>}
      </button>
      <dialog
        ref={dialog}
        className="notifications-dialog"
        onClose={() => setOpen(false)}
      >
        <header>
          <h2>Уведомления</h2>
          <button autoFocus onClick={() => setOpen(false)}>
            Закрыть
          </button>
        </header>
        <p>
          Изменения расписания и учебного журнала. Список хранится до 90 дней,
          не более 500 записей. Опубликованные снимки по ссылкам обновляются
          отдельно.
        </p>
        {error && (
          <p role="alert" className="error-banner">
            {error}
          </p>
        )}
        {inbox && (
          <>
            <div className="notification-settings">
              <label>
                <input
                  type="checkbox"
                  checked={inbox.enabled}
                  disabled={busy}
                  onChange={(e) => {
                    const enabled = e.target.checked;
                    void run(async () => {
                      await api("notifications/preferences", "school", "PUT", {
                        enabled,
                      });
                      setInbox((current) => current && { ...current, enabled });
                    });
                  }}
                />{" "}
                Сохранять новые уведомления
              </label>
              <button
                disabled={busy || permission === "unsupported"}
                onClick={() => void run(toggleBrowser)}
              >
                {browserOn
                  ? "Выключить уведомления браузера"
                  : "Включить уведомления браузера"}
              </button>
              <small>
                Уведомления браузера работают, пока эта вкладка открыта.
                Включаются отдельно для этой вкладки и учётной записи. В них нет
                имён и содержания уроков.
              </small>
              {permission === "denied" && (
                <small>
                  Браузер запретил уведомления. Разрешение можно изменить в
                  настройках сайта.
                </small>
              )}
            </div>
            <div className="dated-toolbar">
              <label>
                <input
                  type="checkbox"
                  checked={unreadOnly}
                  onChange={(e) => setUnreadOnly(e.target.checked)}
                />{" "}
                Только непрочитанные ({inbox.unread})
              </label>
              <button
                disabled={busy || !inbox.unread}
                onClick={() => void run(() => mark())}
              >
                Прочитать все
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () =>
                    setInbox(
                      await api(
                        `notifications/inbox?unreadOnly=${unreadOnly}`,
                        "school",
                      ),
                    ),
                  )
                }
              >
                Обновить
              </button>
            </div>
            <div className="notification-list">
              {!inbox.items.length && <p>Уведомлений пока нет.</p>}
              {inbox.items.map((item) => (
                <article key={item.id} className={item.read ? "" : "unread"}>
                  <h3>{titles[item.action] || titles.saved}</h3>
                  <p>
                    {item.workspace === "school" ? "Школа" : "Демонстрация"} ·{" "}
                    {item.action.startsWith("journal.") ||
                    item.action.startsWith("homework.") ||
                    item.action.startsWith("quiz.") ||
                    item.action.startsWith("message.")
                      ? "редакция записи"
                      : "версия"}{" "}
                    {item.revision}
                  </p>
                  <p>
                    {item.action.startsWith("absence.")
                      ? "Откройте «Отсутствия и объяснительные» в учебном журнале."
                      : item.action === "family.updated"
                        ? "Откройте «Мои дети и доступ» в личном кабинете."
                        : item.action.startsWith("message.")
                          ? "Откройте раздел сообщений в личном кабинете."
                          : item.action.startsWith("quiz.")
                            ? "Откройте «Контрольные и тесты», чтобы посмотреть назначение и результаты."
                            : item.action.startsWith("homework.")
                              ? "Откройте домашнюю работу в учебном журнале."
                              : item.action.startsWith("journal.")
                                ? "Откройте учебный журнал, чтобы проверить опубликованные записи."
                                : "Проверьте доступное вам расписание, включая занятия по датам."}
                  </p>
                  <time dateTime={item.created}>
                    {new Date(item.created).toLocaleString("ru-RU")}
                  </time>
                  {!item.read && (
                    <button
                      disabled={busy}
                      onClick={() => void run(() => mark([item.id]))}
                    >
                      Прочитано
                    </button>
                  )}
                </article>
              ))}
            </div>
            {inbox.nextBeforeId && (
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const older: Inbox = await api(
                      `notifications/inbox?beforeId=${inbox.nextBeforeId}&unreadOnly=${unreadOnly}`,
                      "school",
                    );
                    setInbox(
                      (current) =>
                        current && {
                          ...older,
                          items: [
                            ...current.items,
                            ...older.items.filter(
                              (n) => !current.items.some((x) => x.id === n.id),
                            ),
                          ],
                        },
                    );
                  })
                }
              >
                Показать более ранние
              </button>
            )}
          </>
        )}
      </dialog>
    </>
  );
}

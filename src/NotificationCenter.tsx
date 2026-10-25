import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { startPolling } from "./polling";
import PushSettings from "./PushSettings";

interface Notice {
  id: number;
  workspace: string;
  revision: number;
  created: string;
  action: string;
  read: boolean;
  detail?: string;
}
interface Inbox {
  items: Notice[];
  unread: number;
  latestId: number;
  nextBeforeId: number | null;
  enabled: boolean;
}
const titles: Record<string, string> = {
  "handover:adult_proposed":
    "Школе предложен взрослый, который будет забирать ребёнка",
  "handover:adult_updated": "Изменилось разрешение забирать ребёнка",
  "handover:requested": "Оформлена заявка на уход ребёнка",
  "handover:arrive": "Взрослый прибыл за ребёнком",
  "handover:ready": "Ребёнок готов к уходу",
  "handover:cancel": "Заявка на уход отменена",
  "handover:completed": "Школа подтвердила передачу ребёнка",
  "handover:returned": "Школа отметила возвращение ребёнка",
  "canteen:ordered": "Оформлен заказ питания",
  "canteen:cancelled": "Заказ питания отменён",
  "payments:charged": "В финансовом кабинете появилось начисление",
  "payments:received": "Школа учла поступление оплаты",
  "payments:adjusted": "Школа уточнила начисление",
  "payments:updated": "Обновлена история оплаты",
  "payments:reminder":
    "Проверьте просроченные начисления в финансовом кабинете",
  "meeting.published": "Открыта запись на родительские встречи",
  "meeting.booked": "Создана запись на встречу",
  "meeting.cancelled": "Встреча отменена",
  "meeting.updated": "Обновлена запись на встречу",
  "meeting.waitlist_offer":
    "Школа предложила время встречи. Откройте очередь ожидания.",
  "meeting.waitlist_changed": "Изменилась заявка в очереди ожидания встреч.",
  "meeting.rescheduled": "Изменилось время родительской встречи",
  "meeting.reminder": "Скоро родительская встреча",
  "forms.assigned": "Вам назначена школьная форма",
  "forms.published": "Опубликована школьная форма",
  "forms.submitted": "Новые ответы на школьную форму",
  "forms.reviewed": "Изменилось решение по заявке",
  "staff-time.submitted": "Табель или отсутствие ожидает проверки",
  "staff-time.changed": "Изменилось состояние записи в табеле",
  "library.issued": "В библиотеке оформлена выдача",
  "library.changed": "Изменилось состояние библиотечной выдачи",
  "library.reminder": "Напоминание о библиотечной книге",
  "lost-found.claimed": "Есть изменение в заявках на найденные вещи",
  "lost-found.changed": "Изменилось решение по вашей находке",
  "behaviour.published": "Новая похвала или замечание",
  "behaviour.changed": "Изменена запись о поведении",
  "behaviour.withdrawn": "Запись о поведении отозвана",
  "behaviour.acknowledged": "Родитель ознакомился с замечанием",
  "behaviour.pending": "Мера ждёт согласования администрации",
  "behaviour.decided": "Принято решение по вашей мере",
  "assessment.published":
    "Назначена контрольная работа (СОР, СОЧ или экзамен). Откройте календарь контрольных",
  "assessment.moved":
    "Перенесена контрольная работа. Откройте календарь контрольных",
  "assessment.changed": "Изменён тип контрольной работы",
  "assessment.cancelled": "Отменена контрольная работа",
  "assessment.staff_changed":
    "Изменена работа в календаре контрольных по вашему предмету",
  "clubs.changed": "Изменилась запись или информация кружка",
  "clubs.session": "Изменилось занятие или посещение кружка",
  "documents.assigned": "Вам поручена подготовка документа",
  "documents.submitted": "Школьный документ ожидает проверки",
  "documents.issued": "Вам доступен школьный документ",
  "documents.changed": "Изменилось состояние школьного документа",
  "documents.revoked": "Школьный документ отозван",
  "duty.reminder": "Напоминание о дежурстве",
  "duty.changed": "Изменились ваши дежурства",
  "duty.swap_requested": "Запрошена замена дежурного",
  "duty.swap_decided": "Принято решение по замене дежурства",
  "push.test": "Проверка фоновых уведомлений",
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
  const [pushActive, setPushActive] = useState(false);
  useEffect(() => {
    if (typeof Notification !== "undefined")
      setPermission(Notification.permission);
  }, [pushActive]);
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
          !pushActive &&
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
        !pushActive &&
        typeof Notification !== "undefined" &&
        Notification.permission === "granted",
    });
    return () => {
      live = false;
      polling.stop();
    };
  }, [identity, unreadOnly, open, browserOn, pushActive]);
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
          Сообщения и изменения в школе. Список хранится до 90 дней, не более
          500 записей. Опубликованные снимки по ссылкам обновляются отдельно.
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
                disabled={busy || permission === "unsupported" || pushActive}
                onClick={() => void run(toggleBrowser)}
              >
                {browserOn
                  ? "Выключить уведомления браузера"
                  : "Включить уведомления браузера"}
              </button>
              {pushActive && (
                <small>
                  Фоновая доставка включена; отдельные уведомления открытой
                  вкладки приостановлены.
                </small>
              )}
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
            <PushSettings
              identity={identity}
              open={open}
              onActiveChange={setPushActive}
            />
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
                  {item.detail && <p>{item.detail}</p>}
                  {item.action.startsWith("library.") && (
                    <button
                      onClick={() =>
                        window.dispatchEvent(
                          new CustomEvent("atlas-open-library", {
                            detail: { workspace: item.workspace },
                          }),
                        )
                      }
                    >
                      Открыть библиотеку
                    </button>
                  )}
                  {item.action.startsWith("clubs.") && (
                    <button
                      onClick={() => {
                        window.dispatchEvent(
                          new CustomEvent("atlas-open-clubs", {
                            detail: { workspace: item.workspace },
                          }),
                        );
                        setOpen(false);
                      }}
                    >
                      Открыть кружки
                    </button>
                  )}
                  {item.action.startsWith("lost-found.") && (
                    <button
                      onClick={() => {
                        window.dispatchEvent(
                          new CustomEvent("atlas-open-lost-found", {
                            detail: { workspace: item.workspace },
                          }),
                        );
                        setOpen(false);
                      }}
                    >
                      Открыть потерянные вещи
                    </button>
                  )}
                  {item.action.startsWith("documents.") && (
                    <button
                      onClick={() =>
                        window.dispatchEvent(
                          new CustomEvent("atlas-open-documents", {
                            detail: { workspace: item.workspace },
                          }),
                        )
                      }
                    >
                      Открыть документы
                    </button>
                  )}
                  {item.action.startsWith("staff-time.") && (
                    <button
                      onClick={() =>
                        window.dispatchEvent(
                          new CustomEvent("atlas-open-staff-time", {
                            detail: { workspace: item.workspace },
                          }),
                        )
                      }
                    >
                      Открыть табель
                    </button>
                  )}
                  {item.action.startsWith("handover:") && (
                    <button
                      onClick={() =>
                        window.dispatchEvent(
                          new CustomEvent("atlas-open-handover", {
                            detail: { workspace: item.workspace },
                          }),
                        )
                      }
                    >
                      Открыть уход детей
                    </button>
                  )}
                  {item.action.startsWith("meeting.") && (
                    <button
                      onClick={() =>
                        window.dispatchEvent(
                          new CustomEvent("atlas-open-meetings", {
                            detail: { workspace: item.workspace },
                          }),
                        )
                      }
                    >
                      Открыть родительские встречи
                    </button>
                  )}
                  {item.action.startsWith("forms.") && (
                    <button
                      onClick={() => {
                        window.dispatchEvent(
                          new CustomEvent("atlas-open-forms", {
                            detail: { workspace: item.workspace },
                          }),
                        );
                      }}
                    >
                      Открыть формы и опросы
                    </button>
                  )}
                  <p>
                    {item.workspace === "school" ? "Школа" : "Демонстрация"} ·{" "}
                    {item.action.startsWith("meeting.") ||
                    item.action.startsWith("documents.") ||
                    item.action.startsWith("library.") ||
                    item.action.startsWith("clubs.") ||
                    item.action.startsWith("lost-found.") ||
                    item.action.startsWith("staff-time.") ||
                    item.action.startsWith("forms.") ||
                    item.action.startsWith("journal.") ||
                    item.action.startsWith("homework.") ||
                    item.action.startsWith("quiz.") ||
                    item.action.startsWith("message.")
                      ? "редакция записи"
                      : "версия"}{" "}
                    {item.revision}
                  </p>
                  <p>
                    {item.action.startsWith("handover:")
                      ? "Откройте уход детей, чтобы проверить разрешение или состояние заявки."
                      : item.action.startsWith("clubs.")
                        ? "Откройте кружки, чтобы проверить запись и занятия."
                        : item.action.startsWith("lost-found.")
                          ? "Откройте потерянные вещи, чтобы проверить свои заявки или решение ответственного."
                          : item.action.startsWith("library.")
                            ? "Откройте библиотеку, чтобы проверить срок возврата и состояние выдачи."
                            : item.action.startsWith("documents.")
                              ? "Откройте документы и архив, чтобы проверить содержание и состояние документа."
                              : item.action.startsWith("staff-time.")
                                ? "Откройте табель, чтобы проверить часы или решение по отсутствию."
                                : item.action.startsWith("meeting.")
                                  ? "Откройте родительские встречи, чтобы проверить время и состояние записи."
                                  : item.action.startsWith("forms.")
                                    ? "Откройте формы и опросы, чтобы ответить или посмотреть результат."
                                    : item.action === "push.test"
                                      ? "Это проверочное уведомление для вашего устройства."
                                      : item.action.startsWith("absence.")
                                        ? "Откройте «Отсутствия и объяснительные» в учебном журнале; ответственный учитель — в разделе «Работа с классами»."
                                        : item.action === "family.updated"
                                          ? "Откройте «Мои дети и доступ» в личном кабинете."
                                          : item.action.startsWith("message.")
                                            ? "Откройте раздел сообщений в личном кабинете."
                                            : item.action.startsWith("quiz.")
                                              ? "Откройте «Контрольные и тесты», чтобы посмотреть назначение и результаты."
                                              : item.action.startsWith(
                                                    "homework.",
                                                  )
                                                ? "Откройте домашнюю работу в учебном журнале."
                                                : item.action.startsWith(
                                                      "journal.",
                                                    )
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

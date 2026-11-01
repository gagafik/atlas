import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { startPolling } from "./polling";
import PushSettings from "./PushSettings";
import {
  TIMETABLE_ACTIONS,
  groupNotices,
  openEvent,
  pushLabel,
  type Notice,
  type OpenResult,
} from "./notifications";

interface Prefs {
  categories: { id: string; label: string; enabled: boolean }[];
  quietStart: string | null;
  quietEnd: string | null;
  zone: string;
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
  "forms.reminder": "Напоминание о школьной форме",
  "staff-time.submitted": "Табель или отсутствие ожидает проверки",
  "staff-time.changed": "Изменилось состояние записи в табеле",
  "timetable.leave_approved":
    "Согласовано отсутствие учителя. Проверьте «Отсутствия учителей»",
  "timetable.leave_cancelled":
    "Отменено согласованное отсутствие учителя. Проверьте «Отсутствия учителей»",
  "library.issued": "В библиотеке оформлена выдача",
  "library.changed": "Изменилось состояние библиотечной выдачи",
  "library.reminder": "Напоминание о библиотечной книге",
  "lost-found.claimed": "Есть изменение в заявках на найденные вещи",
  "lost-found.changed": "Изменилось решение по вашей находке",
  "registers.visitor": "К вам пришёл посетитель",
  "registers.incident": "Зарегистрировано происшествие",
  "registers.inventory": "Вы назначены ответственным за предмет инвентаря",
  "registers.summary": "Школа опубликовала сведения о происшествии с ребёнком",
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
  "hall.link_issued": "Выдана новая ссылка информационного экрана",
  "hall.link_revoked": "Ссылка информационного экрана отозвана",
  "absence.submitted": "Поступило заявление об отсутствии",
  "absence.updated": "Изменилось решение по объяснительной",
  "absence.recorded": "Школа внесла записку об отсутствии",
  "message.received": "Новое сообщение",
  "message.feed_reply": "Ответ на ваше объявление",
  "message.reported": "Получена жалоба на сообщение",
  "message.moderated": "Изменилось решение по жалобе на сообщение",
  "family.updated": "Изменились сведения или доступ к ребёнку",
  "journal.updated": "Обновлён учебный журнал",
  "journal.coverage_reminder": "Не заполнен журнал урока",
  "journal.coverage_digest": "Недельная сводка незаполненных журналов",
  "journal.attendance_corrected":
    "Классный руководитель исправил отметки посещаемости",
  "journal.attendance_confirmation": "День посещаемости ждёт подтверждения",
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
  "journal.grade": "Новая оценка",
  "journal.homework": "Домашнее задание",
  "timetable.published": "Изменилось расписание",
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
  const [draft, setDraft] = useState<Prefs | null>(null),
    [settingsSaved, setSettingsSaved] = useState(false);
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
    if (!open) return;
    let live = true;
    api("notifications/preferences", "school")
      .then((p: Prefs) => {
        if (live) setDraft(p);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
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
        // The newest unread entry names the alert by its category only, never by its title.
        const newest =
          latest.current === null
            ? undefined
            : data.items.find((n) => n.id > latest.current! && !n.read);
        const fresh = newest !== undefined;
        latest.current = Math.max(latest.current || 0, data.latestId);
        if (
          fresh &&
          browserOn &&
          !pushActive &&
          data.enabled &&
          newest &&
          typeof Notification !== "undefined" &&
          Notification.permission === "granted"
        ) {
          const registration = await navigator.serviceWorker?.getRegistration();
          if (!live) return;
          if (registration?.active)
            await registration.showNotification("Атлас: есть изменения", {
              body: `${pushLabel(newest.category)}. Откройте приложение и проверьте список уведомлений.`,
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
  async function savePreferences() {
    if (!draft) return;
    const saved: Prefs = await api(
      "notifications/preferences",
      "school",
      "PUT",
      {
        muted: draft.categories.filter((c) => !c.enabled).map((c) => c.id),
        quietStart: draft.quietStart,
        quietEnd: draft.quietEnd,
      },
    );
    setDraft(saved);
    setSettingsSaved(true);
    setInbox(
      (current) =>
        current && {
          ...current,
          enabled: saved.categories.some((c) => c.enabled),
        },
    );
  }
  // The server checks access again; a record that is gone answers 404 and the entry is marked read.
  async function openNotice(item: Notice) {
    let problem = "";
    try {
      const target = openEvent(
        (await api(
          `notifications/${item.id}/open`,
          "school",
          "POST",
        )) as OpenResult,
      );
      if (target) {
        window.dispatchEvent(new CustomEvent(target[0], { detail: target[1] }));
        setOpen(false);
      }
    } catch (e: any) {
      problem = e.message;
    }
    setInbox(
      await api(`notifications/inbox?unreadOnly=${unreadOnly}`, "school"),
    );
    if (problem) throw new Error(problem);
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
              <details className="notification-prefs">
                <summary>Настройки уведомлений</summary>
                {draft ? (
                  <>
                    <fieldset>
                      <legend>Какие уведомления сохранять</legend>
                      {draft.categories.map((c) => (
                        <label key={c.id}>
                          <input
                            type="checkbox"
                            checked={c.enabled}
                            disabled={busy}
                            onChange={(e) => {
                              setSettingsSaved(false);
                              setDraft({
                                ...draft,
                                categories: draft.categories.map((x) =>
                                  x.id === c.id
                                    ? { ...x, enabled: e.target.checked }
                                    : x,
                                ),
                              });
                            }}
                          />{" "}
                          {c.label}
                        </label>
                      ))}
                    </fieldset>
                    <fieldset>
                      <legend>
                        Тихие часы для фоновых уведомлений (время школы)
                      </legend>
                      <label>
                        <input
                          type="checkbox"
                          checked={draft.quietStart === null}
                          disabled={busy}
                          onChange={(e) => {
                            setSettingsSaved(false);
                            setDraft({
                              ...draft,
                              quietStart: e.target.checked ? null : "22:00",
                              quietEnd: e.target.checked ? null : "07:00",
                            });
                          }}
                        />{" "}
                        без тихих часов
                      </label>
                      {draft.quietStart !== null && (
                        <div className="quiet-hours">
                          <label>
                            с{" "}
                            <input
                              type="time"
                              value={draft.quietStart}
                              disabled={busy}
                              onChange={(e) => {
                                setSettingsSaved(false);
                                setDraft({
                                  ...draft,
                                  quietStart: e.target.value,
                                });
                              }}
                            />
                          </label>
                          <label>
                            до{" "}
                            <input
                              type="time"
                              value={draft.quietEnd || ""}
                              disabled={busy}
                              onChange={(e) => {
                                setSettingsSaved(false);
                                setDraft({
                                  ...draft,
                                  quietEnd: e.target.value,
                                });
                              }}
                            />
                          </label>
                        </div>
                      )}
                      <small>
                        В тихие часы запись появляется в списке сразу, а
                        уведомление на экран устройства приходит после их
                        окончания.
                      </small>
                    </fieldset>
                    <button
                      disabled={busy}
                      onClick={() => void run(savePreferences)}
                    >
                      Сохранить настройки
                    </button>
                    {settingsSaved && (
                      <small role="status">Настройки сохранены.</small>
                    )}
                  </>
                ) : (
                  <small>Загружаю настройки…</small>
                )}
              </details>
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
                Включаются отдельно для этой вкладки и учётной записи. В
                системных уведомлениях только категория («Новая оценка», «Новое
                ДЗ»): без имён, оценок и содержания уроков.
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
              {groupNotices(inbox.items).map((group) => (
                <section key={group.key} className="notice-group-block">
                  {group.label && (
                    <h3 className="notice-group">{group.label}</h3>
                  )}
                  {group.items.map((item) => {
                    // Entries written before titles existed keep the module buttons below.
                    const legacy = !item.link && !item.restricted;
                    return (
                      <article
                        key={item.id}
                        className={item.read ? "" : "unread"}
                      >
                        <h4>{titles[item.action] || titles.saved}</h4>
                        {item.title && (
                          <p className="notice-title">{item.title}</p>
                        )}
                        {item.students?.length ? (
                          <p className="notice-child">
                            {item.students.map((x) => x.name).join(", ")}
                          </p>
                        ) : null}
                        {item.restricted && (
                          <p>Доступ к этой записи изменился.</p>
                        )}
                        {item.link && (
                          <button
                            disabled={busy}
                            onClick={() => void run(() => openNotice(item))}
                          >
                            Открыть
                          </button>
                        )}
                        {legacy && item.action.startsWith("library.") && (
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
                        {legacy && item.action.startsWith("clubs.") && (
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
                        {legacy && item.action.startsWith("lost-found.") && (
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
                        {legacy && item.action.startsWith("registers.") && (
                          <button
                            onClick={() => {
                              window.dispatchEvent(
                                new CustomEvent("atlas-open-registers", {
                                  detail: { workspace: item.workspace },
                                }),
                              );
                              setOpen(false);
                            }}
                          >
                            Открыть журналы
                          </button>
                        )}
                        {legacy && item.action.startsWith("documents.") && (
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
                        {legacy && item.action.startsWith("staff-time.") && (
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
                        {legacy && item.action.startsWith("handover:") && (
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
                        {legacy && item.action.startsWith("meeting.") && (
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
                        {legacy && item.action.startsWith("forms.") && (
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
                          {item.workspace === "school"
                            ? "Школа"
                            : "Демонстрация"}
                          {TIMETABLE_ACTIONS.includes(item.action) &&
                            ` · версия расписания ${item.revision}`}
                        </p>
                        {legacy && (
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
                                              : item.action.startsWith(
                                                    "absence.",
                                                  )
                                                ? "Откройте «Отсутствия и объяснительные» в учебном журнале; ответственный учитель — в разделе «Работа с классами»."
                                                : item.action ===
                                                    "family.updated"
                                                  ? "Откройте «Мои дети и доступ» в личном кабинете."
                                                  : item.action.startsWith(
                                                        "message.",
                                                      )
                                                    ? "Откройте раздел сообщений в личном кабинете."
                                                    : item.action.startsWith(
                                                          "quiz.",
                                                        )
                                                      ? "Откройте «Контрольные и тесты», чтобы посмотреть назначение и результаты."
                                                      : item.action.startsWith(
                                                            "homework.",
                                                          )
                                                        ? "Откройте домашнюю работу в учебном журнале."
                                                        : item.action.startsWith(
                                                              "journal.",
                                                            )
                                                          ? "Откройте учебный журнал, чтобы проверить опубликованные записи."
                                                          : item.action.startsWith(
                                                                "registers.",
                                                              )
                                                            ? "Откройте журналы регистрации, чтобы посмотреть запись."
                                                            : "Проверьте доступное вам расписание, включая занятия по датам."}
                          </p>
                        )}
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
                    );
                  })}
                </section>
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

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import {
  applicationKey,
  browserSubscription,
  endpointFingerprint,
} from "./push";

interface Device {
  id: string;
  fingerprint: string;
  version: number;
  name: string;
  active: boolean;
  expires: number;
  lastSent: number | null;
  status: string;
}
interface Options {
  configured: boolean;
  reason: string | null;
  publicKey: string | null;
  devices: Device[];
  limit: number;
}
const path = "notifications/push/";
const date = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString("ru-RU");
export default function PushSettings({
  identity,
  open,
  onActiveChange,
}: {
  identity: string;
  open: boolean;
  onActiveChange: (active: boolean) => void;
}) {
  const [options, setOptions] = useState<Options | null>(null);
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const [hasSubscription, setHasSubscription] = useState(false);
  const [name, setName] = useState("Моё устройство");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const sequence = useRef(0);
  const supported =
    window.isSecureContext &&
    "Notification" in window &&
    "PushManager" in window &&
    "serviceWorker" in navigator;
  const current = options?.devices.find(
    (device) => device.fingerprint === fingerprint,
  );
  const refresh = useCallback(async () => {
    const requested = ++sequence.current;
    const data: Options = await api(path + "options", "school");
    const subscription = await browserSubscription();
    const hash = subscription
      ? await endpointFingerprint(subscription.endpoint)
      : null;
    if (requested !== sequence.current) return;
    setOptions(data);
    setHasSubscription(!!subscription);
    setFingerprint(hash);
    onActiveChange(
      data.configured &&
        data.devices.some(
          (device) => device.active && device.fingerprint === hash,
        ) &&
        typeof Notification !== "undefined" &&
        Notification.permission === "granted",
    );
  }, [onActiveChange]);
  useEffect(() => {
    let alive = true;
    // AuthShell keys NotificationCenter by account; no state is shared between accounts.
    const update = () => {
      if (alive && document.visibilityState !== "hidden")
        void refresh().catch((e) => {
          if (alive) setError(e.message);
        });
    };
    update();
    document.addEventListener("visibilitychange", update);
    navigator.serviceWorker?.addEventListener("message", update);
    return () => {
      alive = false;
      ++sequence.current;
      document.removeEventListener("visibilitychange", update);
      navigator.serviceWorker?.removeEventListener("message", update);
    };
  }, [identity, open, refresh]);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    ++sequence.current;
    try {
      await action();
    } catch (e: any) {
      setError(
        e.message ||
          "Не удалось изменить уведомления. Обновите список и повторите.",
      );
    } finally {
      try {
        await refresh();
      } catch (e: any) {
        setError(e.message);
      }
      setBusy(false);
    }
  }
  async function enable() {
    if (!options?.publicKey) return;
    if ((await Notification.requestPermission()) !== "granted")
      throw new Error(
        "Разрешите уведомления в настройках этого сайта, затем повторите.",
      );
    const registration = await navigator.serviceWorker.getRegistration();
    if (!registration?.active)
      throw new Error(
        "Приложение ещё не готово. Перезагрузите страницу и повторите.",
      );
    const subscription =
      (await registration.pushManager.getSubscription()) ||
      (await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: applicationKey(options.publicKey),
      }));
    const previousKey = subscription.options.applicationServerKey;
    const key = applicationKey(options.publicKey);
    if (
      previousKey &&
      (previousKey.byteLength !== key.length ||
        new Uint8Array(previousKey).some((byte, index) => byte !== key[index]))
    )
      throw new Error(
        "Ключ сервера изменился. Сбросьте подписку этого браузера и включите уведомления заново.",
      );
    await api(path + "devices", "school", "POST", {
      ...subscription.toJSON(),
      publicKey: options.publicKey,
      name: name.trim() || "Моё устройство",
      version: current?.version ?? 0,
    });
    setNotice(
      "Фоновые уведомления включены. Их можно проверить кнопкой у устройства.",
    );
  }
  async function remove(device: Device) {
    await api(path + `devices/${device.id}/remove`, "school", "POST", {
      version: device.version,
    });
    if (device.fingerprint === fingerprint) {
      try {
        await (await browserSubscription())?.unsubscribe();
      } catch {
        setNotice(
          "Доставка на сервере отключена. Подписку браузера можно сбросить отдельно.",
        );
        return;
      }
    }
    setNotice("Доставка на устройство отключена.");
  }
  return (
    <details className="push-settings">
      <summary>Уведомления при закрытой вкладке</summary>
      <p>
        На экране будет только «В Атласе есть обновления», без имён и содержания
        сообщений. Выход из аккаунта отключает доставку на этом устройстве.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!options ? (
        <p>Проверяю настройки…</p>
      ) : (
        <>
          {!options.configured && (
            <p>
              {options.reason === "login"
                ? "Для фоновых уведомлений войдите в свою учётную запись."
                : "Администратор ещё не включил фоновую доставку на сервере."}
            </p>
          )}
          {!supported && (
            <p>
              Этот браузер не поддерживает фоновые уведомления в текущем режиме.
              Нужны HTTPS и поддержка Web Push. На iPhone/iPad откройте
              установленное на главный экран приложение.
            </p>
          )}
          {supported && options.configured && (
            <>
              <p>
                {current?.active
                  ? "На этом устройстве включены."
                  : "На этом устройстве выключены."}
              </p>
              {Notification.permission === "denied" && (
                <p>
                  Браузер запретил показ уведомлений. Измените разрешение в
                  настройках сайта.
                </p>
              )}
              {!current?.active && (
                <div className="dated-toolbar">
                  <label>
                    Название устройства{" "}
                    <input
                      maxLength={80}
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      disabled={busy}
                    />
                  </label>
                  <button disabled={busy} onClick={() => void run(enable)}>
                    Включить на этом устройстве
                  </button>
                </div>
              )}
              {hasSubscription && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      if (current) await remove(current);
                      else await (await browserSubscription())?.unsubscribe();
                      setNotice(
                        "Подписка браузера сброшена. Теперь можно включить уведомления заново.",
                      );
                    })
                  }
                >
                  Сбросить подписку этого браузера
                </button>
              )}
              {hasSubscription && !current && (
                <small>
                  В браузере уже есть подписка, которая не связана с этим
                  аккаунтом. Сбросьте её перед включением.
                </small>
              )}
            </>
          )}
          <h4>
            Мои устройства ({options.devices.filter((d) => d.active).length}/
            {options.limit || 10})
          </h4>
          {!options.devices.length && <p>Подключённых устройств пока нет.</p>}
          <ul className="push-devices">
            {options.devices.map((device) => (
              <li key={device.id}>
                <strong>
                  {device.name}
                  {device.fingerprint === fingerprint
                    ? " · это устройство"
                    : ""}
                </strong>
                <span>
                  {device.active
                    ? "Включено"
                    : "Выключено — включите заново на устройстве"}
                </span>
                {device.active && (
                  <small>Подписка до {date(device.expires)}</small>
                )}
                {device.lastSent && (
                  <small>
                    Сервис принял уведомление: {date(device.lastSent)}. Это не
                    подтверждает показ на экране.
                  </small>
                )}
                {(device.status === "network" ||
                  device.status.startsWith("http_")) && (
                  <small>
                    Последняя попытка доставки не удалась. Проверьте уведомления
                    в приложении.
                  </small>
                )}
                <div className="dated-toolbar">
                  <button
                    disabled={busy || !device.active || !options.configured}
                    onClick={() =>
                      void run(async () => {
                        await api(
                          path + `devices/${device.id}/test`,
                          "school",
                          "POST",
                          { version: device.version },
                        );
                        setNotice(
                          "Проверочное уведомление поставлено в очередь. Устройство должно быть подключено к интернету.",
                        );
                      })
                    }
                  >
                    Проверить доставку
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => void run(() => remove(device))}
                  >
                    Отключить и удалить
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <button disabled={busy} onClick={() => void run(async () => {})}>
            Обновить устройства
          </button>
          <small>
            Доставка зависит от браузера, подключения и настроек системы.
            Непрочитанные уведомления остаются в приложении.
          </small>
        </>
      )}
    </details>
  );
}

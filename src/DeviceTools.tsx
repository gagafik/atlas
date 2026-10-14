import { useEffect, useRef, useState } from "react";

interface InstallPrompt extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: string }>;
}
// Retain the browser's one-shot prompt across the loading/login/app screens.
let pendingInstall: InstallPrompt | null = null;
let appInstalled = matchMedia("(display-mode: standalone)").matches;
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  pendingInstall = event as InstallPrompt;
});
window.addEventListener("appinstalled", () => {
  pendingInstall = null;
  appInstalled = true;
});
export default function DeviceTools() {
  const [online, setOnline] = useState(navigator.onLine),
    [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState<InstallPrompt | null>(pendingInstall),
    [installed, setInstalled] = useState(appInstalled);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const connection = () => setOnline(navigator.onLine);
    const available = (event: Event) => {
      event.preventDefault();
      setPrompt(event as InstallPrompt);
    };
    const done = () => {
      setPrompt(null);
      setInstalled(true);
    };
    window.addEventListener("online", connection);
    window.addEventListener("offline", connection);
    window.addEventListener("beforeinstallprompt", available);
    window.addEventListener("appinstalled", done);
    return () => {
      window.removeEventListener("online", connection);
      window.removeEventListener("offline", connection);
      window.removeEventListener("beforeinstallprompt", available);
      window.removeEventListener("appinstalled", done);
    };
  }, []);
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  return (
    <>
      <button onClick={() => setOpen(true)}>Приложение</button>
      {!online && (
        <p className="connection-status" role="status">
          Нет сети. Сохранение и обновление расписания недоступны.
        </p>
      )}
      <dialog
        ref={dialog}
        className="notifications-dialog"
        onClose={() => setOpen(false)}
      >
        <header>
          <h2>Атлас на устройстве</h2>
          <button autoFocus onClick={() => setOpen(false)}>
            Закрыть
          </button>
        </header>
        <p>
          {installed
            ? "Приложение установлено и открыто отдельным окном."
            : "Установите Атлас для запуска с домашнего экрана или рабочего стола."}
        </p>
        {prompt ? (
          <button
            className="primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                await prompt.prompt();
                await prompt.userChoice;
                pendingInstall = null;
                setPrompt(null);
              } catch {
                setError(
                  "Не удалось открыть установку. Используйте меню браузера.",
                );
              } finally {
                setBusy(false);
              }
            }}
          >
            Установить приложение
          </button>
        ) : (
          !installed && (
            <p>
              Если кнопки установки нет, откройте меню браузера → «Установить
              приложение» или «Добавить на главный экран», если этот пункт
              доступен. На iPhone/iPad используйте меню «Поделиться» в Safari.
            </p>
          )
        )}
        <p>
          Для телефона нужен адрес сервера школы с HTTPS. Адрес 127.0.0.1
          работает только на самом компьютере сервера.
        </p>
        <p>
          Без сети доступен экран подключения. Расписание, списки учеников и
          опубликованные документы в офлайн-кэш не сохраняются. Отправка
          изменений после восстановления сети выполняется вручную.
        </p>
        <p>
          Обновление приложения применяется при следующем открытии после
          закрытия всех его окон. Перед закрытием сохраните незавершённые
          правки.
        </p>
        {error && (
          <p role="alert" className="error-banner">
            {error}
          </p>
        )}
      </dialog>
    </>
  );
}

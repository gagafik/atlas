import { useEffect, useState } from "react";
import { startPolling } from "./polling";
import HallBoard from "./HallBoard";
import { stale, tokenFromHash, type HallPayload } from "./hallScreen";
import "./HallDisplay.css";

const GONE =
  "Ссылка экрана недействительна или отозвана. Обратитесь к администратору.";

/** The hall TV page: no login, no /api/ requests. The token stays in the URL fragment and travels in one header. */
export default function HallDisplay() {
  const [token, setToken] = useState(() => tokenFromHash(location.hash)),
    [data, setData] = useState<HallPayload | null>(null),
    [gone, setGone] = useState(false),
    [failed, setFailed] = useState(false);
  const [lastOk, setLastOk] = useState(0),
    [offset, setOffset] = useState(0),
    [tick, setTick] = useState(Date.now());
  useEffect(() => {
    const change = () => setToken(tokenFromHash(location.hash));
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    setData(null);
    setGone(false);
    setFailed(false);
    setLastOk(0);
    if (!token) return;
    let active = true,
      etag = "";
    const controller = new AbortController();
    const poll = startPolling(
      async () => {
        try {
          const r = await fetch("/hall-api/display", {
            cache: "no-store",
            signal: controller.signal,
            headers: {
              "X-Hall-Token": token,
              ...(etag ? { "If-None-Match": etag } : {}),
            },
          });
          if (!active) return false;
          const server = Date.parse(r.headers.get("X-Atlas-Server-Time") || "");
          if (Number.isFinite(server)) setOffset(server - Date.now());
          if (r.status === 304) {
            setLastOk(Date.now());
            setFailed(false);
            return true;
          }
          if (r.status === 404) {
            // A revoked, expired or archived link must stop showing data at once.
            etag = "";
            setData(null);
            setGone(true);
            setFailed(false);
            return true;
          }
          if (!r.ok) {
            setFailed(true);
            return false;
          }
          const value = await r.json();
          if (!active) return false;
          if (!Array.isArray(value?.days)) {
            setFailed(true);
            return false;
          }
          etag = r.headers.get("ETag") || "";
          setData(value);
          setGone(false);
          setFailed(false);
          setLastOk(Date.now());
          return true;
        } catch {
          // The last data stays on the wall while the server is away.
          if (active) setFailed(true);
          return false;
        }
      },
      { interval: 60000, background: () => true, maxDelay: 120000 },
    );
    return () => {
      active = false;
      controller.abort();
      poll.stop();
    };
  }, [token]);
  useEffect(() => {
    const timer = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (data) document.title = `${data.screen} · Атлас`;
  }, [data?.screen]);
  useEffect(() => {
    document.body.style.background = "#0b1714";
    return () => {
      document.body.style.background = "";
    };
  }, []);
  useEffect(() => {
    let lock: WakeLockSentinel | null = null;
    const request = async () => {
      try {
        lock = (await navigator.wakeLock?.request("screen")) ?? null;
      } catch {
        // Best effort: not every TV browser can keep the screen awake.
      }
    };
    const again = () => {
      if (document.visibilityState === "visible") void request();
    };
    void request();
    document.addEventListener("visibilitychange", again);
    return () => {
      document.removeEventListener("visibilitychange", again);
      void lock?.release().catch(() => undefined);
    };
  }, []);
  if (!token)
    return (
      <main className="hall-board hall-message">
        <p>Откройте ссылку экрана, выданную администратором.</p>
      </main>
    );
  if (gone)
    return (
      <main className="hall-board hall-message">
        <p role="alert">{GONE}</p>
      </main>
    );
  if (!data)
    return (
      <main className="hall-board hall-message">
        <p role="status">
          {failed
            ? "Нет связи с сервером. Повторяем подключение…"
            : "Загрузка…"}
        </p>
      </main>
    );
  const offline = failed || stale(lastOk, tick) || navigator.onLine === false;
  return (
    <HallBoard
      data={data}
      clock={tick + offset}
      offline={offline}
      lastUpdated={lastOk ? lastOk + offset : undefined}
      tools={
        <div className="hall-tools">
          <button
            onClick={async () => {
              try {
                const el = document.documentElement;
                if (document.fullscreenElement) await document.exitFullscreen();
                else await el.requestFullscreen();
              } catch {
                // Some TV browsers refuse; kiosk mode is the alternative.
              }
            }}
          >
            Полный экран
          </button>
        </div>
      }
    />
  );
}

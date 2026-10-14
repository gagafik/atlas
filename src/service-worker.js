/* Only this public allowlist is cached. Never cache authenticated responses. */
const SHELL_CACHE = "atlas-public-shell-__SHELL_VERSION__";
const PUBLIC_FILES = [
  "/offline.html",
  "/manifest.webmanifest",
  "/icons/atlas-192.png",
  "/icons/atlas-512.png",
];
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(PUBLIC_FILES)),
  );
  // Do not skipWaiting: an update must not reload a page containing unsaved work.
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys())
        if (key.startsWith("atlas-public-shell-") && key !== SHELL_CACHE)
          await caches.delete(key);
      await self.clients.claim();
    })(),
  );
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin)
    return;
  if (url.pathname === "/" || url.pathname === "/index.html") {
    if (event.request.mode !== "navigate") return;
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(event.request);
          if (response.ok) return response;
        } catch {
          /* Server unavailable: show the public connection screen. */
        }
        return (await caches.open(SHELL_CACHE)).match("/offline.html");
      })(),
    );
  } else if (PUBLIC_FILES.includes(url.pathname) && !url.search) {
    event.respondWith(
      (async () =>
        (await (await caches.open(SHELL_CACHE)).match(url.pathname)) ||
        fetch(event.request))(),
    );
  }
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      const app = windows.find(
        (client) =>
          new URL(client.url).origin === self.location.origin &&
          ["/", "/index.html"].includes(new URL(client.url).pathname),
      );
      if (app) {
        await app.focus();
        app.postMessage({ type: "atlas-open-notifications" });
      } else await self.clients.openWindow("/?notifications=1");
    })(),
  );
});

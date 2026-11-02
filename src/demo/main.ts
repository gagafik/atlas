import { mountBar, ROLES, type DemoRole } from "./bar";
import { installClock } from "./clock";
import { installLinkGuard } from "./linkGuard";
import { installFetch, type DemoSnapshot } from "./runtime";

function chooseRole(): DemoRole {
  const asked = new URLSearchParams(window.location.search).get("as");
  let stored: string | null = null;
  try {
    stored = window.sessionStorage.getItem("atlas-demo-role");
  } catch {
    // Storage may be blocked; the default role is used.
  }
  const role = [asked, stored].find((value) =>
    (ROLES as readonly string[]).includes(value ?? ""),
  );
  const chosen = (role as DemoRole | undefined) ?? "admin";
  try {
    window.sessionStorage.setItem("atlas-demo-role", chosen);
  } catch {
    // Not remembered; the link in the bar still works.
  }
  return chosen;
}

function fail(text: string): void {
  const root = document.getElementById("root");
  if (root) root.textContent = text;
}

async function boot() {
  const role = chooseRole();
  let snapshot: DemoSnapshot;
  try {
    const response = await fetch(
      `${import.meta.env.BASE_URL}demo-data/${role}.json`,
    );
    if (!response.ok) throw new Error(String(response.status));
    snapshot = await response.json();
  } catch {
    fail("Не удалось загрузить данные демо. Обновите страницу.");
    return;
  }
  installClock(snapshot.capturedAt);
  installFetch(snapshot);
  installLinkGuard();
  if ("serviceWorker" in navigator) {
    // The demo is static; an installed shell would only serve stale pages.
    Object.defineProperty(navigator.serviceWorker, "register", {
      configurable: true,
      value: () => Promise.reject(new Error("Not available in the demo.")),
    });
  }
  mountBar(role);
  await import("../main");
}

void boot();

import { getKey, postKey } from "./requestKey";

export interface DemoEntry {
  s: number;
  b: unknown;
}
export interface DemoSnapshot {
  capturedAt: string;
  /** POSTs that only read: matched by path, then by body unless `anyBody`. */
  readPosts: { pattern: string; anyBody?: boolean }[];
  /** Writes the app fires on its own; they answer 200 without a snapshot. */
  silent: { method: string; pattern: string }[];
  entries: Record<string, DemoEntry>;
}

const API_PREFIXES = ["/api/", "/hall-api/", "/admissions-api/", "/published"];
export const MISSING_TEXT =
  "В демо нет данных для этого выбора: сохранены не все дни, фильтры и разделы. Вернитесь к предыдущему выбору.";
export const READ_ONLY_TEXT = "Это демо: изменения не сохраняются.";

export function isApiUrl(url: URL, origin: string): boolean {
  return (
    url.origin === origin &&
    API_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))
  );
}

function json(status: number, body: unknown): Response {
  const empty = status === 204 || status === 205 || status === 304;
  return new Response(empty ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function createDemoFetch(
  snapshot: DemoSnapshot,
  realFetch: typeof fetch,
  log: (...args: unknown[]) => void = (...args) => console.info(...args),
  origin: string = globalThis.location?.origin ?? "http://localhost",
): typeof fetch {
  const readPosts = snapshot.readPosts.map((rule) => ({
    pattern: new RegExp(rule.pattern),
    anyBody: rule.anyBody === true,
  }));
  const silent = snapshot.silent.map((rule) => ({
    method: rule.method.toUpperCase(),
    pattern: new RegExp(rule.pattern),
  }));
  const answer = (key: string) => {
    const entry = Object.hasOwn(snapshot.entries, key)
      ? snapshot.entries[key]
      : undefined;
    if (entry) return json(entry.s, entry.b);
    log("[demo] no snapshot", key);
    return json(404, { detail: MISSING_TEXT });
  };
  return async function demoFetch(input, init) {
    const request = input instanceof Request ? input : null;
    let url: URL;
    try {
      url = new URL(request ? request.url : String(input), origin);
    } catch {
      return realFetch(input, init);
    }
    if (!isApiUrl(url, origin)) return realFetch(input, init);
    const method = (init?.method ?? request?.method ?? "GET").toUpperCase();
    if (method === "GET" || method === "HEAD") return answer(getKey(url));
    if (
      silent.some(
        (rule) => rule.method === method && rule.pattern.test(url.pathname),
      )
    )
      return json(200, {});
    if (method === "POST") {
      const rule = readPosts.find((candidate) =>
        candidate.pattern.test(url.pathname),
      );
      if (rule) {
        let bodyText: string | null = null;
        if (!rule.anyBody) {
          const body =
            init?.body ?? (request ? await request.clone().text() : null);
          bodyText = typeof body === "string" ? body : null;
        }
        return answer(postKey(url, bodyText, rule.anyBody));
      }
    }
    log("[demo] write refused", method, url.pathname);
    return json(403, { detail: READ_ONLY_TEXT });
  };
}

export function installFetch(snapshot: DemoSnapshot): void {
  window.fetch = createDemoFetch(snapshot, window.fetch.bind(window));
}

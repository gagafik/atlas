// Request keys of the static demo snapshot. tools/demo/request-key.mjs is the
// Node twin used by the capture tool; tests/demo.test.ts keeps them identical.

function sortedQuery(searchParams: URLSearchParams): string {
  const pairs = [...searchParams.entries()].filter(
    ([name]) => name !== "workspace",
  );
  pairs.sort(([a, x], [b, y]) =>
    a === b ? (x < y ? -1 : x > y ? 1 : 0) : a < b ? -1 : 1,
  );
  return new URLSearchParams(pairs).toString();
}

export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .filter((key) => key !== "token" && record[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

export function pathKey(method: string, url: URL): string {
  const query = sortedQuery(url.searchParams);
  return `${method} ${url.pathname}${query ? `?${query}` : ""}`;
}

export function getKey(url: URL): string {
  return pathKey("GET", url);
}

export function postKey(
  url: URL,
  bodyText: string | null | undefined,
  anyBody = false,
): string {
  const key = pathKey("POST", url);
  if (anyBody) return key;
  let canonical = "";
  if (bodyText) {
    try {
      canonical = canonicalJson(JSON.parse(bodyText));
    } catch {
      canonical = bodyText;
    }
  }
  return `${key}#${canonical}`;
}

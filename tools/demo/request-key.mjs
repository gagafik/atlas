// Node twin of src/demo/requestKey.ts. Keep both in step: tests/demo.test.ts
// compares them on shared vectors. The capture tool and the browser runtime
// must derive identical keys for the same request.

function sortedQuery(searchParams) {
  const pairs = [...searchParams.entries()].filter(
    ([name]) => name !== "workspace",
  );
  pairs.sort(([a, x], [b, y]) =>
    a === b ? (x < y ? -1 : x > y ? 1 : 0) : a < b ? -1 : 1,
  );
  return new URLSearchParams(pairs).toString();
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .filter((key) => key !== "token" && value[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

export function pathKey(method, url) {
  const query = sortedQuery(url.searchParams);
  return `${method} ${url.pathname}${query ? `?${query}` : ""}`;
}

export function getKey(url) {
  return pathKey("GET", url);
}

export function postKey(url, bodyText, anyBody = false) {
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

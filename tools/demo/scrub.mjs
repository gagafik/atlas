// Scrubbing of a captured snapshot. The repository and the demo URL are public,
// so anything that could be real or secret is replaced or the capture fails.
import { userInfo } from "node:os";

const SECRET_KEY =
  /token|hash|secret|password|session|cookie|endpoint|p256dh|recovery/i;

// A leak in any string (keys of entries included) stops the capture.
export function leakPatterns(osUser = userInfo().username) {
  const patterns = [
    [/[A-Za-z]:\\/, "Windows path"],
    [/\/home\//, "Unix home path"],
    [/\/Users\//, "macOS or Windows user path"],
    [/127\.0\.0\.1/, "loopback address"],
    [/localhost/i, "localhost"],
    [/[\w.+-]+@[\w-]+\.[\w.-]+/, "e-mail address"],
    [/scrypt|pbkdf2|\$argon|Bearer /i, "credential or hash"],
    [/gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}/, "access token"],
  ];
  if (osUser && osUser.length > 2)
    patterns.push([new RegExp(osUser.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), "operating system user name"]);
  return patterns;
}

export function scrubValue(value, secretish = false) {
  if (Array.isArray(value)) return value.map((item) => scrubValue(item, secretish));
  if (value && typeof value === "object") {
    const result = {};
    for (const [key, inner] of Object.entries(value))
      result[key] = scrubValue(inner, secretish || SECRET_KEY.test(key));
    return result;
  }
  if (secretish && typeof value === "string" && value !== "") return "demo";
  return value;
}

export function findLeaks(snapshot, osUser) {
  const patterns = leakPatterns(osUser);
  const found = [];
  const visit = (value, where) => {
    if (typeof value === "string") {
      for (const [pattern, label] of patterns)
        if (pattern.test(value)) found.push(`${label} in ${where}: ${value.slice(0, 80)}`);
    } else if (Array.isArray(value)) value.forEach((item, i) => visit(item, `${where}[${i}]`));
    else if (value && typeof value === "object")
      for (const [key, inner] of Object.entries(value)) {
        visit(key, `${where} key`);
        visit(inner, `${where}.${key}`);
      }
  };
  for (const [key, entry] of Object.entries(snapshot.entries)) {
    visit(key, "request key");
    visit(entry.b, key);
  }
  return found;
}

// Entries and fields that make the demo behave: no setup button, one workspace.
function adjustStatus(body) {
  if (!body || typeof body !== "object") return body;
  const result = { ...body, canSetup: false };
  if (result.user) {
    result.user = { ...result.user, workspaces: ["school"] };
    if (Array.isArray(result.user.grants))
      result.user.grants = result.user.grants.filter((grant) => grant.workspace === "school");
  }
  return result;
}

export function scrubSnapshot(snapshot, osUser) {
  const entries = {};
  for (const [key, entry] of Object.entries(snapshot.entries)) {
    let body = scrubValue(entry.b);
    if (key === "GET /api/auth/status") body = adjustStatus(body);
    // The assistant page names the model server of the capture machine.
    if (key.startsWith("GET /api/assistant/status") && body && typeof body === "object" && "server" in body)
      body = { ...body, server: "локальный сервер модели" };
    entries[key] = { s: entry.s, b: body };
  }
  const clean = { ...snapshot, entries };
  const leaks = findLeaks(clean, osUser);
  if (leaks.length)
    throw new Error(`Snapshot refused, ${leaks.length} possible leak(s):\n${leaks.slice(0, 20).join("\n")}`);
  return clean;
}

// One entry per line with sorted keys keeps the diffs of refreshed snapshots readable.
export function serializeSnapshot(snapshot) {
  const keys = Object.keys(snapshot.entries).sort();
  const lines = keys.map(
    (key) => `${JSON.stringify(key)}:${JSON.stringify(snapshot.entries[key])}`,
  );
  return (
    `{"capturedAt":${JSON.stringify(snapshot.capturedAt)},\n` +
    `"readPosts":${JSON.stringify(snapshot.readPosts)},\n` +
    `"silent":${JSON.stringify(snapshot.silent)},\n` +
    `"entries":{\n${lines.join(",\n")}\n}}\n`
  );
}

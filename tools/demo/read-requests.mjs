// What the capture tool treats specially. The patterns are written into every
// snapshot, so the browser runtime hard-codes no path lists.

// POSTs that only read. The demo answers them from the snapshot by body.
export const READ_POSTS = [
  { pattern: "^/api/collaboration/presence$", anyBody: true },
  { pattern: "^/api/journal/coverage/(report|classes|details|days)$" },
  { pattern: "^/api/journal/attendance/(report|details)$" },
  { pattern: "^/api/calendar/day-plan$" },
  { pattern: "^/api/journal/quizzes/[^/]+/transfer/preview$" },
];

// Writes the app fires by itself when a view opens; they answer 200 quietly.
export const SILENT = [
  { method: "PUT", pattern: "^/api/messages/threads/[^/]+/read$" },
];

export const API_PREFIXES = ["/api/", "/hall-api/", "/admissions-api/", "/published"];

// Never captured: operations, audit, invitations, push, recovery codes, public
// links, hall screens, the applicant portal and diagnostics.
export const DENY = [
  /^\/api\/access\/(operations|audit|invitations|push)/,
  /^\/api\/auth\/recovery/,
  /^\/api\/publications/,
  /^\/api\/hall/,
  /^\/hall-api\//,
  /^\/admissions-api\//,
  /^\/api\/diagnostics/,
  /^\/api\/ready$/,
  /^\/api\/docs/,
  /^\/api\/server-metrics/,
  /^\/published/,
];

// Screens of the administrator that read denied paths get an empty list instead of
// an error: the real answers (invitations, access audit) are never captured.
export const DENY_STUBS = [
  { pattern: /^\/api\/access\/audit$/, body: [] },
  { pattern: /^\/api\/access\/invitations(\/batches)?$/, body: { items: [], nextBefore: null } },
];

export function denyStub(pathname) {
  return DENY_STUBS.find((stub) => stub.pattern.test(pathname));
}

export function isApiPath(pathname) {
  return API_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

export function isDenied(pathname) {
  return DENY.some((pattern) => pattern.test(pathname));
}

export function readPostRule(pathname) {
  return READ_POSTS.find((rule) => new RegExp(rule.pattern).test(pathname));
}

export function isSilent(method, pathname) {
  return SILENT.some(
    (rule) => rule.method === method && new RegExp(rule.pattern).test(pathname),
  );
}

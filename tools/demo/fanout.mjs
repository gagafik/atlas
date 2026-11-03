// After the walk and the journeys: ask the backend for the details the app opens
// from the lists it already showed (every lesson of a day, every message thread,
// every course of the picker ...), with exactly the URL the app builds, so that
// a viewer of the demo does not meet an empty answer after the first click.
// Only GET requests are made; every answer is kept only when it has data.
import { getKey, postKey } from "./request-key.mjs";

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const weekdayOf = (day) => new Date(`${day}T00:00:00Z`).getUTCDay();
export const isoShift = (day, delta) => new Date(Date.parse(`${day}T00:00:00Z`) + delta * 86400000).toISOString().slice(0, 10);

export function daysBetween(first, last, { schoolOnly = false } = {}) {
  const result = [];
  for (let day = first; day <= last; day = isoShift(day, 1)) {
    const weekday = weekdayOf(day);
    if (!schoolOnly || (weekday !== 0 && weekday !== 6)) result.push(day);
  }
  return result;
}

// A month as the app asks for it: first and last day.
export function monthRange(year, month) {
  const first = `${year}-${String(month).padStart(2, "0")}-01`;
  const last = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
  return [first, last];
}

export function createReplayer({ context, origin, entries, log }) {
  const done = new Set(entries.keys());
  const stats = { requests: 0, stored: 0 };
  // The body of a GET: from the capture if the app asked for it already, else from the backend (data answers only).
  async function get(target) {
    const url = new URL(target, origin);
    const key = getKey(url);
    if (entries.has(key)) return entries.get(key).b;
    if (done.has(key)) return undefined;
    done.add(key);
    stats.requests += 1;
    const response = await context.request.get(url.toString());
    if (response.status() !== 200 || !/json/.test(response.headers()["content-type"] || "")) return undefined;
    const body = await response.json().catch(() => undefined);
    if (body === undefined) return undefined;
    entries.set(key, { s: 200, b: body });
    stats.stored += 1;
    return body;
  }
  // The answer of a POST that only reads (the coverage report), kept under the key the browser derives from the same body.
  async function post(target, body) {
    const url = new URL(target, origin);
    const key = postKey(url, JSON.stringify(body));
    if (entries.has(key)) return entries.get(key).b;
    if (done.has(key)) return undefined;
    done.add(key);
    stats.requests += 1;
    const response = await context.request.post(url.toString(), { data: body, headers: { "X-Atlas-Request": "1" } });
    if (response.status() !== 200 || !/json/.test(response.headers()["content-type"] || "")) return undefined;
    const answer = await response.json().catch(() => undefined);
    if (answer === undefined) return undefined;
    entries.set(key, { s: 200, b: answer });
    stats.stored += 1;
    return answer;
  }
  const lists = (prefix) =>
    [...entries].filter(([key]) => key.startsWith(`GET ${prefix}`)).map(([, entry]) => entry.b);
  return { get, post, lists, stats, entries, log };
}

const q = (params) => new URLSearchParams(params).toString();

// ---- lessons: the four requests the journal makes when a lesson card is opened
async function openLesson(r, day, lessonKey) {
  const base = { lessonKey, day };
  await r.get(`/api/journal/entry?${q({ ...base, syncRoster: "false" })}`);
  await r.get(`/api/journal/curriculum/lesson?${q(base)}`);
  await r.get(`/api/journal/assignment-files?${q(base)}`);
  await r.get(`/api/behaviour/lesson?${q(base)}`);
}

async function lessonsOfDays(r, days) {
  let opened = 0;
  for (const day of days) {
    const list = await r.get(`/api/journal/day?${q({ day })}`);
    for (const row of list?.lessons || []) {
      if (row.archived) continue;
      await openLesson(r, day, row.lessonKey);
      opened += 1;
    }
  }
  r.log(`  lessons opened: ${opened}`);
}

// ---- threads of the message lists and of the feed
async function threads(r) {
  const ids = new Set();
  for (const list of [...r.lists("/api/messages/threads?"), ...r.lists("/api/messages/feed")])
    for (const item of list?.items || []) ids.add(item.id);
  for (const id of ids) {
    const detail = await r.get(`/api/messages/threads/${id}`);
    if (detail && (detail.replyCount > 0 || (detail.posts || []).length > 1)) await r.get(`/api/messages/threads/${id}/replies`);
  }
  for (const list of r.lists("/api/messages/feed"))
    for (const item of list?.items || []) if (item.replyCount > 0) await r.get(`/api/messages/threads/${item.id}/replies`);
  r.log(`  message threads: ${ids.size}`);
}

// ---- forms: the results tab of every form
async function forms(r) {
  const ids = new Set();
  for (const list of r.lists("/api/forms/items")) for (const item of list?.items || []) ids.add(item.id);
  for (const id of ids) {
    await r.get(`/api/forms/items/${id}`);
    await r.get(`/api/forms/items/${id}/responses?limit=20`);
    await r.get(`/api/forms/items/${id}/summary`);
    await r.get(`/api/forms/items/${id}/pending?limit=50&offset=0`);
  }
}

// ---- consultations: every date of every series with its slots and bookings
async function meetings(r) {
  await r.get("/api/meetings/consultations");
  await r.get("/api/meetings/class-meetings");
  for (const list of r.lists("/api/meetings/consultations"))
    for (const item of list?.items || []) {
      await r.get(`/api/meetings/events/${item.id}`);
      const pattern = item.pattern;
      if (!pattern) continue;
      for (const day of daysBetween(pattern.startDate, pattern.endDate)) {
        // The weekday numbers of the backend: Monday is 0.
        if (!pattern.weekdays.includes((weekdayOf(day) + 6) % 7)) continue;
        await r.get(`/api/meetings/events/${item.id}/slots?${q({ hostId: "", onDate: day })}`);
        await r.get(`/api/meetings/bookings?${q({ eventId: item.id, onDate: day })}`);
      }
    }
}

// ---- course tables: every course of the picker in every period, as the gradebook asks for it: with the scale it opens on
// (the first active scale with thresholds, the only one of the demo school) and the period, which brings the final grades;
// the notes column asks for the praise and remarks of the period in the subject of the course.
async function courseTables(r) {
  const picker = (await r.get("/api/journal/course-table/courses")) || null;
  if (!picker) return;
  const options = await r.get("/api/final-grades/options");
  const scale = (options?.scales || []).find((s) => s.active && s.kind === "thresholds");
  let count = 0;
  for (const course of picker.courses || [])
    for (const period of picker.periods || []) {
      const table = { groupId: course.groupId, subjectId: course.subjectId, start: period.start, end: period.end, periodId: period.id };
      await r.get(`/api/journal/course-table?${q(scale ? { ...table, scaleId: scale.id } : table)}`);
      await r.get(`/api/behaviour/summary?${q({ start: period.start, end: period.end, subjectId: course.subjectId })}`);
      count += 1;
    }
  r.log(`  course tables: ${count}`);
}

// ---- the month calendar of «Классный журнал»: the days with unfilled lessons, as the hook asks for them (the own lessons of a
// teacher, one class at a time for the administrator), for the months around the demo day.
const COVERAGE_MONTHS = [
  [2026, 10],
  [2026, 11],
];
async function coverageMonths(r, role) {
  const options = await r.get("/api/journal/options");
  if (!options?.staff) return;
  const scopes = [];
  if (role === "teacher") scopes.push({ mode: "teaching", classId: "" });
  else {
    const classes = (await r.get("/api/journal/coverage/options"))?.classes?.classes || [];
    for (const school of classes) scopes.push({ mode: "classes", classId: school.id });
  }
  for (const [year, month] of COVERAGE_MONTHS) {
    // Mirrors monthWindow (src/journalCoverage.ts): the month cut to the school year.
    const [first, last] = monthRange(year, month);
    const start = first < options.yearStart ? options.yearStart : first;
    const end = last > options.yearEnd ? options.yearEnd : last;
    if (start > end) continue;
    for (const scope of scopes) {
      let offset = 0,
        pin = {};
      for (let page = 0; offset !== null && page < 20; page += 1) {
        const answer = await r.post("/api/journal/coverage/report", { start, end, ...scope, status: "issues", offset, ...pin });
        offset = answer?.nextOffset ?? null;
        pin = { token: answer?.token, asOf: answer?.asOf };
      }
    }
  }
}

// ---- final grades: the sheet, the matrix, the class reports. A page of the sheet is large (50 rows,
// about 3 KB each), so only what a presentation opens is kept: the first page of the whole school, the
// first page of every class, and every subject of the first class. The grading table (a scale chosen) is
// kept for the first subject only.
async function sheetPages(r, bookId, params, maxPages) {
  let offset = 0;
  for (let page = 0; offset !== null && offset !== undefined && page < maxPages; page += 1) {
    const body = await r.get(`/api/final-grades/books/${bookId}?${q({ ...params, offset: String(offset) })}`);
    offset = body?.nextOffset ?? null;
  }
}

async function finalGrades(r) {
  const options = await r.get("/api/final-grades/options");
  if (!options) return;
  const state = await r.get("/api/state");
  const classes = state?.classes?.map((c) => c.id) || [];
  const subjects = state?.subjects?.map((s) => s.id) || [];
  const scale = (options.scales || []).find((s) => s.active)?.id;
  for (const book of options.books || []) {
    await r.get(`/api/final-grades/books/${book.id}/matrix`);
    await sheetPages(r, book.id, { classId: "", subjectId: "", status: "all" }, 1);
    for (const classId of classes) await sheetPages(r, book.id, { classId, subjectId: "", status: "all" }, 1);
    for (const [index, subjectId] of subjects.entries()) {
      if (!classes[0]) break;
      await sheetPages(r, book.id, { classId: classes[0], subjectId, status: "all" }, 1);
      if (index === 0) await sheetPages(r, book.id, { classId: classes[0], subjectId, ...(scale ? { scaleId: scale } : {}) }, 1);
    }
    for (const classId of classes) {
      for (const subjectId of subjects) await r.get(`/api/final-grades/books/${book.id}/handles?${q({ classId, subjectId })}`);
      await r.get(`/api/final-grades/books/${book.id}/handles?${q({ classId })}`);
      await r.get(`/api/final-grades/books/${book.id}/handles?${q({ classId, status: "approved" })}`);
      await r.get(`/api/final-grades/report-layouts/${book.period.id}/${classId}`);
    }
    for (const classId of classes.slice(0, 2)) await r.get(`/api/final-grades/class-report?${q({ periodId: book.period.id, classId })}`);
    // The personal report card of the pupils of the first class and of one pupil of each other class.
    const wanted = new Map();
    for (const student of state?.students || []) {
      const seen = wanted.get(student.classId) || [];
      if (seen.length < (student.classId === classes[0] ? 15 : 1)) wanted.set(student.classId, [...seen, student.id]);
    }
    for (const ids of wanted.values()) for (const studentId of ids) await r.get(`/api/final-grades/report?${q({ studentId, periodId: book.period.id })}`);
  }
}

// ---- praise and remarks of every class
async function behaviour(r) {
  const options = await r.get("/api/behaviour/options");
  for (const school of options?.classes || []) await r.get(`/api/behaviour/notes?${q({ classId: school.id })}`);
  for (const student of options?.students || []) await r.get(`/api/behaviour/notes?${q({ studentId: student.id })}`);
}

// ---- the family journal: «Оценки» and «Посещаемость» ask for one range at a time (every period and the school year), the day view
// of «Классный журнал» for the week of the day it shows. Further pages of notes are read the way the tab «Заметки» reads them.
const WEEKS = ["2026-10-05", "2026-11-13"];
async function familyJournal(r) {
  const options = await r.get("/api/journal/options");
  if (!options) return;
  const periods = options.periods || [];
  // Mirrors periodChoices (src/pupilJournal.ts): the school's periods and the year, unless a year period covers it.
  const yearEnd = options.yearEnd < isoShift(options.yearStart, 366) ? options.yearEnd : isoShift(options.yearStart, 366);
  const ranges = periods.map((p) => [p.start, p.end]);
  if (!periods.some((p) => p.kind === "year" && p.start <= options.yearStart && p.end >= yearEnd)) ranges.push([options.yearStart, yearEnd]);
  const finals = await r.get("/api/final-grades/options");
  for (const student of options.students || []) {
    for (const [start, end] of ranges) {
      await r.get(`/api/journal/pupil-period?${q({ studentId: student.id, start, end })}`);
      let page = await r.get(`/api/behaviour/family?${q({ studentId: student.id, start, end })}`);
      for (let more = 0; page?.nextBefore && more < 9; more += 1)
        page = await r.get(`/api/behaviour/family?${q({ studentId: student.id, start, end, before: page.nextBefore })}`);
    }
    for (const period of finals?.periods || []) await r.get(`/api/final-grades/report?${q({ studentId: student.id, periodId: period.id })}`);
    for (let monday = WEEKS[0]; monday <= WEEKS[1]; monday = isoShift(monday, 7))
      await r.get(`/api/journal/report?${q({ studentId: student.id, start: monday, end: isoShift(monday, 6), limit: 200 })}`);
  }
}

// ---- the family's homework: the work behind every "Открыть домашнюю работу" of the journal
async function familyHomework(r) {
  let count = 0;
  for (const report of r.lists("/api/journal/report")) {
    for (const row of report?.rows || []) {
      if (!row.content?.homework) continue;
      await r.get(`/api/journal/homework/${row.lessonKey}?${q({ studentId: row.student.id })}`);
      count += 1;
    }
  }
  r.log(`  homework opened from the journal: ${count}`);
}

// ---- calendars shown a month at a time: a few months either way
async function months(r) {
  const seen = new Set();
  for (const [key] of [...r.entries]) {
    if (!key.startsWith("GET /api/assessment-calendar/")) continue;
    const url = new URL(key.slice(4), "http://x");
    const start = url.searchParams.get("start");
    const end = url.searchParams.get("end");
    if (!start || !end || !DAY.test(start) || !DAY.test(end) || !start.endsWith("-01")) continue;
    const [year, month] = start.split("-").map(Number);
    if (monthRange(year, month)[1] !== end) continue;
    for (let delta = -2; delta <= 4; delta += 1) {
      const index = year * 12 + (month - 1) + delta;
      const [first, last] = monthRange(Math.floor(index / 12), (index % 12) + 1);
      const next = new URL(url);
      next.searchParams.set("start", first);
      next.searchParams.set("end", last);
      const text = next.pathname + next.search;
      if (seen.has(text)) continue;
      seen.add(text);
      await r.get(text);
    }
  }
}

const STAFF_DAYS = { admin: ["2026-10-21", "2026-10-30", "2026-11-02"] };

export async function fanOut(r, role, schoolDays) {
  const staff = role === "admin" || role === "teacher";
  // The notification settings open from the bell of every role.
  await r.get("/api/notifications/preferences");
  if (role === "teacher") await lessonsOfDays(r, daysBetween("2026-10-05", "2026-11-06", { schoolOnly: true }));
  else if (STAFF_DAYS[role]) await lessonsOfDays(r, STAFF_DAYS[role]);
  await threads(r);
  if (staff) {
    await forms(r);
    await behaviour(r);
    await meetings(r);
    await courseTables(r);
    await coverageMonths(r, role);
    await finalGrades(r);
  } else {
    await familyJournal(r);
    await familyHomework(r);
  }
  await months(r);
  void schoolDays;
}

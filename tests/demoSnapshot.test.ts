import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { getKey } from "../src/demo/requestKey";
import { gridWindow } from "../src/journalCoverage";
import { periodChoices } from "../src/pupilJournal";

// The committed demo snapshots answer the requests the client makes: a request the client builds that the snapshot lacks
// shows the visitor an error banner. The windows are built by the client's own functions, never by a copy of their rules.
type Entries = Record<string, { s: number; b: any }>;
const load = (role: string): Entries =>
  JSON.parse(
    readFileSync(new URL(`../tools/demo/snapshot/${role}.json`, import.meta.url), "utf8"),
  ).entries;
const key = (path: string, params: Record<string, string> = {}) =>
  getKey(new URL(`http://x${path}?${new URLSearchParams(params)}`));

describe.each(["admin", "teacher"])("calendar of the day view for %s", (role) => {
  it("has the months the client asks for", () => {
    const entries = load(role);
    const options = entries[key("/api/journal/options")].b;
    const asked = new Set(
      Object.keys(entries)
        .filter((k) => k.startsWith("POST /api/journal/coverage/days#"))
        .map((k) => {
          const body = JSON.parse(k.split("#", 2)[1]);
          return [body.mode, body.classId, body.start, body.end].join("|");
        }),
    );
    const scopes: [string, string][] =
      role === "teacher"
        ? [["teaching", ""]]
        : entries[key("/api/journal/coverage/options")].b.classes.classes.map(
            (c: { id: string }) => ["classes", c.id],
          );
    expect(scopes.length).toBeGreaterThan(0);
    for (const [mode, classId] of scopes)
      for (const month of ["2026-09", "2026-10", "2026-11", "2026-12"]) {
        const window = gridWindow(month, options.yearStart, options.yearEnd);
        if (window)
          expect(asked, `${mode} ${classId} ${month}`).toContain(
            [mode, classId, window.start, window.end].join("|"),
          );
      }
  });
});

describe.each(["parent", "student"])("family journal for %s", (role) => {
  it("has an answer for every period choice of every child", () => {
    const entries = load(role);
    const options = entries[key("/api/journal/options")].b;
    const choices = periodChoices(options.periods, options.yearStart, options.yearEnd);
    expect(choices.length).toBeGreaterThan(0);
    for (const child of options.students.map((s: { id: string }) => s.id))
      for (const { start, end } of choices)
        for (const path of ["/api/journal/pupil-period", "/api/behaviour/family"])
          expect(entries, `${child} ${path} ${start}..${end}`).toHaveProperty([
            key(path, { studentId: child, start, end }),
          ]);
  });
});

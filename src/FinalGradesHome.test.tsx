import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { NoStatement, ReportClassesTable } from "./FinalGradesHome";
import type { Job, Matrix, Period } from "./finalGradesTypes";
import { classRows } from "./reportCardsView";

const period = (status = "closed"): Period => ({
  id: "q1",
  name: "Первая четверть",
  version: 2,
  status,
  start: "2026-09-01",
  end: "2026-10-25",
});
const cell = (classId: string, subjectId: string, total: number) => ({
  classId,
  subjectId,
  total,
  draft: total,
  submitted: 0,
  approved: 0,
  published: 0,
  proposed: 0,
  unassigned: 0,
  unavailable: 0,
});
const matrix: Matrix = {
  book: { id: "b1", period: period(), closureVersion: 2, current: true },
  classes: [
    { id: "c5", name: "5А", pupils: 24 },
    { id: "c7", name: "7Б", pupils: 21 },
    { id: "c9", name: "9В", pupils: 18 },
  ],
  subjects: [{ id: "s1", name: "Алгебра" }],
  cells: [cell("c5", "s1", 24), cell("c7", "s1", 21), cell("c9", "s1", 18)],
};
const job = (status: string): Job => ({
  id: "j1",
  periodId: "q1",
  periodName: "Первая четверть",
  closureVersion: 2,
  status,
  phase: "rows",
  completed: 3,
  total: 10,
  attempts: 1,
  message: "",
  bookId: "",
});
const buttons = (html: string) =>
  [...html.matchAll(/<button[^>]*>/g)].map((m) => m[0]);
const disabled = (tag: string) => /\sdisabled(=|\s|>)/.test(tag);

describe("ReportClassesTable", () => {
  const rows = classRows(matrix);
  const render = (busy: boolean) =>
    renderToStaticMarkup(
      <ReportClassesTable
        rows={rows}
        busy={busy}
        onOpen={() => {}}
        onPrint={() => {}}
      />,
    );
  it("names every class and has controls for each of them", () => {
    const html = render(false);
    for (const r of rows) expect(html).toContain(r.name);
    expect(buttons(html).length).toBeGreaterThanOrEqual(rows.length);
    expect(buttons(html).some(disabled)).toBe(false);
  });
  it("blocks every control while something is busy", () => {
    const found = buttons(render(true));
    expect(found.length).toBeGreaterThanOrEqual(rows.length);
    expect(found.every(disabled)).toBe(true);
  });
});

describe("NoStatement", () => {
  const render = (p: Period | undefined, archived: boolean, j?: Job) =>
    renderToStaticMarkup(
      <NoStatement
        period={p}
        archived={archived}
        job={j}
        busy={false}
        onPrepare={() => {}}
      />,
    );
  it("offers one action to prepare the statement of a closed period", () => {
    const found = buttons(render(period(), false));
    expect(found).toHaveLength(1);
    expect(disabled(found[0])).toBe(false);
  });
  it("offers nothing in an archived year, nor for a period that is still open", () => {
    expect(buttons(render(period(), true))).toHaveLength(0);
    expect(buttons(render(period("open"), false))).toHaveLength(0);
    expect(buttons(render(undefined, false))).toHaveLength(0);
  });
  it("does not let a running preparation be started twice", () => {
    const found = buttons(render(period(), false, job("running")));
    expect(found).toHaveLength(1);
    expect(disabled(found[0])).toBe(true);
  });
});

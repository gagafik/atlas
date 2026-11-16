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
const opens = (html: string) =>
  [...html.matchAll(/<button[^>]*aria-label="Открыть класс [^"]*"[^>]*>/g)].map(
    (m) => m[0],
  );

describe("ReportClassesTable", () => {
  const rows = classRows(matrix);
  it("has one open control for every class", () => {
    const html = renderToStaticMarkup(
      <ReportClassesTable
        rows={rows}
        busy={false}
        onOpen={() => {}}
        onPrint={() => {}}
      />,
    );
    const found = opens(html);
    expect(found).toHaveLength(rows.length);
    for (const r of rows)
      expect(
        found.filter((b) => b.includes(`Открыть класс ${r.name}`)),
      ).toHaveLength(1);
    expect(found.every((b) => !b.includes("disabled"))).toBe(true);
  });
  it("blocks every open control while something is busy", () => {
    const html = renderToStaticMarkup(
      <ReportClassesTable
        rows={rows}
        busy
        onOpen={() => {}}
        onPrint={() => {}}
      />,
    );
    const found = opens(html);
    expect(found).toHaveLength(rows.length);
    expect(found.every((b) => b.includes("disabled"))).toBe(true);
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
  it("offers to prepare the statement of a closed period", () => {
    expect(render(period(), false)).toContain("Подготовить ведомость");
  });
  it("does not offer it in an archived year, nor for a period that is still open", () => {
    expect(render(period(), true)).not.toContain("Подготовить ведомость");
    expect(render(period("open"), false)).not.toContain(
      "Подготовить ведомость",
    );
  });
  it("shows the running job and does not let it be started twice", () => {
    const html = render(period(), false, job("running"));
    expect(html).toContain("<progress");
    expect(html).toMatch(/<button[^>]*disabled/);
  });
});

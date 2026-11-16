import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ClassGrid from "./ClassGrid";
import type { Row, Scale } from "./finalGradesTypes";
import { availableTiles } from "./ReportPrintDialog";
import { gridGpa, statusMark } from "./reportCardsView";

const scale = (withPoints: boolean): Scale => ({
  id: "sc",
  name: "Пятибалльная",
  version: 1,
  kind: "labels",
  rounding: "half_up",
  minCount: 1,
  active: true,
  bands: [
    { label: "5", minPercent: 85, points: withPoints ? "5" : null },
    { label: "4", minPercent: 65, points: withPoints ? "4" : null },
  ],
});
const row = (
  pupil: string,
  subject: string,
  status: string,
  label: string,
  points: boolean,
): Row =>
  ({
    id: `${pupil}-${subject}`,
    bookId: "b",
    studentId: pupil,
    subjectId: subject,
    classId: "c",
    assigned: "t",
    version: 1,
    status,
    proposal: {
      scale: scale(points),
      outcome: "graded",
      label,
      comment: "",
      explanation: "",
      calculation: {},
      author: "t",
    },
    published: null,
    source: {
      student: { id: pupil, name: pupil === "p1" ? "Образцов" : "Примеров" },
      subject: subject === "algebra" ? "Алгебра" : "Физика",
    },
  }) as unknown as Row;

const render = (rows: Row[]) =>
  renderToStaticMarkup(
    <ClassGrid
      rows={rows}
      layout={{ order: [], hidden: ["physics"] }}
      className="7А"
      periodName="Первая четверть"
      onSubject={() => undefined}
      onCell={() => undefined}
      onPupil={() => undefined}
    />,
  );
const statuses = ["draft", "submitted", "approved", "published"];
const controls = (html: string) => (html.match(/<button/g) ?? []).length;

describe("class grid", () => {
  it("says the status of every cell in words, so it never depends on colour", () => {
    // One subject per status, so every status has a cell of its own.
    const html = render(
      statuses.map((s, i) => row("p1", `subject${i}`, s, "5", true)),
    );
    for (const status of statuses)
      expect(html).toContain(statusMark(status).full);
    expect(new Set(statuses.map((s) => statusMark(s).short)).size).toBe(
      statuses.length,
    );
  });
  it("shows the average of a pupil only when the scale gives points", () => {
    const rows = [row("p1", "algebra", "draft", "5", true)];
    const average = gridGpa(rows, []);
    expect(average).toBeTruthy();
    expect(render(rows)).toContain(average!);
    expect(render([row("p1", "algebra", "draft", "5", false)])).not.toContain(
      average!,
    );
  });
  it("has a control for every subject, every pupil and every cell", () => {
    const pupils = ["p1", "p2"],
      subjects = ["algebra", "physics"];
    const rows = pupils.flatMap((p) =>
      subjects.map((s) => row(p, s, "draft", "5", true)),
    );
    const none = render([]);
    expect(controls(none)).toBe(0);
    // Subjects and pupils to open, and a cell for each of them: more controls than pupils and subjects alone.
    expect(controls(render(rows))).toBe(
      subjects.length + pupils.length + subjects.length * pupils.length,
    );
  });
  it("keeps a subject that is not on the report card in the grid", () => {
    const rows = [
      row("p1", "algebra", "draft", "5", true),
      row("p1", "physics", "draft", "4", true),
    ];
    // Hiding a subject from the card does not remove its column or its cells from the statement.
    expect(controls(render(rows))).toBe(
      controls(
        renderToStaticMarkup(
          <ClassGrid
            rows={rows}
            layout={{ order: [], hidden: [] }}
            className="7А"
            periodName="Первая четверть"
            onSubject={() => undefined}
            onCell={() => undefined}
            onPupil={() => undefined}
          />,
        ),
      ),
    );
  });
});

describe("print dialog", () => {
  const offered = (o: Partial<Parameters<typeof availableTiles>[0]> = {}) =>
    availableTiles({
      admin: true,
      currentBook: true,
      grid: true,
      bookId: "b",
      ...o,
    });
  it("offers no preview tile to anyone but the administrator on a current statement", () => {
    expect(offered()).toContain("preview");
    expect(offered({ admin: false })).not.toContain("preview");
    expect(offered({ currentBook: false })).not.toContain("preview");
  });
  it("offers a class teacher the published cards only", () => {
    expect(offered({ admin: false, grid: false })).toEqual(["final", "pupil"]);
  });
  it("offers the ledger only where a grid is on screen", () => {
    expect(offered()).toContain("grid");
    expect(offered({ grid: false })).not.toContain("grid");
  });
  it("offers the export only to the administrator with a statement", () => {
    expect(offered()).toContain("csv");
    expect(offered({ admin: false })).not.toContain("csv");
    expect(offered({ bookId: "" })).not.toContain("csv");
  });
});

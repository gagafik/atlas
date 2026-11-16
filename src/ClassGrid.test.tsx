import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ClassGrid from "./ClassGrid";
import type { Row, Scale } from "./finalGradesTypes";
import ReportPrintDialog, { availableTiles } from "./ReportPrintDialog";
import { statusMark } from "./reportCardsView";

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

describe("class grid", () => {
  it("gives every cell the full status of its row", () => {
    // One subject per status, so every status has a cell of its own.
    const html = render(
      statuses.map((s, i) => row("p1", `subject${i}`, s, "5", true)),
    );
    for (const status of statuses) {
      const { full } = statusMark(status);
      expect(html).toContain(`title="${full}"`);
      expect(html).toContain(`, ${full}"`);
    }
  });
  it("shows the average only when the scale gives points", () => {
    const withPoints = render([row("p1", "algebra", "draft", "5", true)]);
    const without = render([row("p1", "algebra", "draft", "5", false)]);
    expect(withPoints).toContain("Средний балл");
    expect(without).not.toContain("Средний балл");
  });
  it("marks a subject that is not on the report card", () => {
    const html = render([
      row("p1", "algebra", "draft", "5", true),
      row("p1", "physics", "draft", "4", true),
    ]);
    expect(html).toContain("не в табеле");
  });
  it("has a control for every subject and pupil", () => {
    const html = render([
      row("p1", "algebra", "draft", "5", true),
      row("p2", "algebra", "draft", "4", true),
    ]);
    expect(html).toContain("Образцов");
    expect(html).toContain("Примеров");
    expect(html).toContain("Алгебра");
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
  it("renders no preview tile without admin rights", () => {
    const html = renderToStaticMarkup(
      <ReportPrintDialog
        workspace="school"
        periodId="q1"
        classId="c"
        className="7А"
        admin={false}
        bookId=""
        currentBook={false}
        onClose={() => undefined}
      />,
    );
    expect(html).toContain("Табели класса");
    expect(html).not.toContain("Предварительные табели");
    expect(html).not.toContain("CSV ведомости");
  });
});

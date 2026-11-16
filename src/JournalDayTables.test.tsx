import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ClassesTable } from "./JournalDayTables";

const classes = [
  { name: "5А", lessons: 2, filled: 1, unfilled: 1, upcoming: 0, state: "Не заполнено: 1" },
];
const total = { lessons: 2, filled: 1, unfilled: 1, upcoming: 0 };

describe("ClassesTable", () => {
  it("lets the administrator open a class", () => {
    const html = renderToStaticMarkup(
      <ClassesTable classes={classes} total={total} busy={false} onOpen={() => {}} />,
    );
    expect(html).toContain("5А");
    expect(html).not.toMatch(/<button[^>]*disabled/);
  });
  it("does not let him pick a class while a lesson is still being opened", () => {
    const html = renderToStaticMarkup(
      <ClassesTable classes={classes} total={total} busy onOpen={() => {}} />,
    );
    expect(html).toMatch(/<button[^>]*disabled/);
  });
});

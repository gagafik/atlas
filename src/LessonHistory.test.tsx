import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LessonHistoryList } from "./LessonHistory";

const base = {
  history: [],
  historyMore: false,
  busy: false,
  historyPage: async () => true,
  showVersion: async () => {},
};

describe("LessonHistoryList", () => {
  it("offers a new try when the revisions could not be read, instead of saying there are none", () => {
    const empty = renderToStaticMarkup(<LessonHistoryList {...base} />);
    const failed = renderToStaticMarkup(
      <LessonHistoryList {...base} failed retry={() => {}} />,
    );
    expect(empty).not.toContain("<button");
    expect(failed).toContain("<button");
    expect(failed).not.toBe(empty);
  });
  it("shows no retry while the revisions are being read", () => {
    const html = renderToStaticMarkup(
      <LessonHistoryList {...base} failed busy retry={() => {}} />,
    );
    expect(html).not.toContain("<button");
  });
});

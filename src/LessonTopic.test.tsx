import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import LessonTopic from "./LessonTopic";
import type { TopicReference } from "./curriculum";

const topic: TopicReference = {
  planId: "plan",
  topicId: "topic",
  version: 3,
  units: 1,
  planTitle: "Примерный план по алгебре",
  title: "Линейные уравнения",
  standard: "Образцовый стандарт 7.2.1",
  plannedHours: 2,
};

const render = (disabled: boolean) =>
  renderToStaticMarkup(
    <LessonTopic
      workspace="demo"
      lessonKey="lesson"
      day="2026-11-16"
      value={topic}
      materials={[]}
      disabled={disabled}
      onChange={() => {}}
    />,
  );

describe("LessonTopic", () => {
  it("shows a read-only reader the topic, its plan and its standard as plain text", () => {
    const html = render(true);
    expect(html).toContain(topic.title);
    expect(html).toContain(topic.planTitle);
    expect(html).toContain(topic.standard);
    expect(html).not.toContain("<details");
  });
  it("gives the one who edits a picker that is folded until it is asked for", () => {
    const html = render(false);
    expect(html).toContain(topic.title);
    expect(html).toContain("<details");
    expect(html).not.toMatch(/<details[^>]* open/);
  });
});

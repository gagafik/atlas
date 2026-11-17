import { isValidElement, type ReactElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";
import {
  LESSON_PAGE,
  LessonsTable,
  TeachersTable,
  WeeksTable,
  WorkloadFiguresStrip,
} from "./WorkloadTables";
import type {
  WorkloadFigures,
  WorkloadLesson,
  WorkloadTeacher,
  WorkloadWeek,
} from "./workloadReport";

const figures = (over: Partial<WorkloadFigures> = {}): WorkloadFigures => ({
  planned: 0,
  plannedToDate: 0,
  taught: 0,
  minutes: 0,
  own: 0,
  substitutions: 0,
  extra: 0,
  journalOnly: 0,
  merged: 0,
  missed: 0,
  missedAbsent: 0,
  uncovered: 0,
  cancelled: 0,
  moved: 0,
  upcoming: 0,
  confirmed: 0,
  differs: 0,
  byType: {},
  delta: 0,
  ...over,
});
const lesson = (over: Partial<WorkloadLesson> = {}): WorkloadLesson => ({
  date: "2026-11-02",
  start: "08:30",
  end: "09:15",
  group: "7А",
  subject: "Алгебра",
  minutes: 45,
  outcome: "taught",
  kind: null,
  confirmed: true,
  differs: false,
  absent: false,
  ...over,
});
const week = (over: Partial<WorkloadWeek> = {}): WorkloadWeek => ({
  ...figures(),
  start: "2026-11-02",
  end: "2026-11-08",
  ...over,
});
const teacher = (over: Partial<WorkloadTeacher> = {}): WorkloadTeacher => ({
  ...figures(),
  teacherId: "t1",
  name: "Анна Примерова",
  ...over,
});

type Props = {
  onClick?: () => void;
  children?: ReactNode;
  "aria-label"?: string;
};
// The tables have no hooks: their components are expanded into elements, so that rows and buttons can be found.
function find(
  node: ReactNode,
  tag: string,
  out: ReactElement<Props>[] = [],
): ReactElement<Props>[] {
  if (Array.isArray(node)) {
    node.forEach((child) => find(child, tag, out));
    return out;
  }
  if (!isValidElement(node)) return out;
  const element = node as ReactElement<any>;
  if (typeof element.type === "function")
    return find((element.type as any)(element.props), tag, out);
  if (element.type === tag) out.push(element);
  return find(element.props.children, tag, out);
}
const textOf = (node: ReactNode): string =>
  Array.isArray(node)
    ? node.map(textOf).join("")
    : typeof node === "string" || typeof node === "number"
      ? String(node)
      : isValidElement(node)
        ? typeof node.type === "function"
          ? textOf((node.type as any)(node.props))
          : textOf((node.props as { children?: ReactNode }).children)
        : "";

const lessonsUi = (
  lessons: WorkloadLesson[],
  over: Partial<Parameters<typeof LessonsTable>[0]> = {},
) => (
  <LessonsTable
    lessons={lessons}
    filter="all"
    week={null}
    onMore={() => {}}
    {...over}
  />
);
const bodyRows = (ui: ReactNode) =>
  find(find(ui, "tbody")[0]?.props.children, "tr");

describe("WorkloadFiguresStrip", () => {
  it("lists only the parts that are not zero", () => {
    const text = textOf(
      <WorkloadFiguresStrip
        t={figures({ taught: 13, own: 12, substitutions: 1 })}
      />,
    );
    expect(text).toContain("свои 12, замены 1");
    expect(text).not.toContain("дополнительные");
    expect(text).not.toContain("при отсутствии");
  });
  it("mentions differences with the journal and merged lessons only when there are some", () => {
    const quiet = textOf(<WorkloadFiguresStrip t={figures()} />);
    expect(quiet).not.toContain("расхождений");
    expect(quiet).not.toContain("не добавляет");
    const loud = textOf(
      <WorkloadFiguresStrip t={figures({ differs: 3, merged: 2 })} />,
    );
    expect(loud).toContain("расхождений с журналом: 3");
    expect(loud).toContain("не добавляет");
  });
  it("shows the minutes next to the hours", () => {
    expect(
      textOf(<WorkloadFiguresStrip t={figures({ minutes: 90 })} />),
    ).toContain("90 мин");
  });
  it("has eight figures", () => {
    expect(find(<WorkloadFiguresStrip t={figures()} />, "dt")).toHaveLength(8);
  });
});

describe("TeachersTable", () => {
  it("opens the teacher whose name was pressed", () => {
    const picked: WorkloadTeacher[] = [];
    const a = teacher({ teacherId: "a", name: "Анна Примерова" });
    const b = teacher({ teacherId: "b", name: "Борис Образцов" });
    const ui = (
      <TeachersTable
        teachers={[a, b]}
        totals={figures()}
        onPick={(t) => picked.push(t)}
      />
    );
    const buttons = find(ui, "button");
    expect(buttons.map((x) => textOf(x))).toEqual([a.name, b.name]);
    buttons[1].props.onClick!();
    expect(picked).toEqual([b]);
  });
  it("ends with the totals row", () => {
    const ui = (
      <TeachersTable
        teachers={[teacher({ taught: 3 })]}
        totals={figures({ taught: 7 })}
        onPick={() => {}}
      />
    );
    const foot = textOf(find(ui, "tfoot"));
    expect(foot).toContain("Итого");
    expect(foot).toContain("7");
  });
});

describe("WeeksTable", () => {
  it("has one button per week, named after the week, that opens that week", () => {
    const opened: WorkloadWeek[] = [];
    const w1 = week();
    const w2 = week({ start: "2026-11-09", end: "2026-11-15" });
    const buttons = find(
      <WeeksTable weeks={[w1, w2]} onOpenWeek={(w) => opened.push(w)} />,
      "button",
    );
    expect(buttons.map((b) => b.props["aria-label"])).toEqual([
      "Уроки недели 02.11 – 08.11",
      "Уроки недели 09.11 – 15.11",
    ]);
    buttons[1].props.onClick!();
    expect(opened).toEqual([w2]);
  });
  it("shows a signed difference from the timetable", () => {
    const text = textOf(
      <WeeksTable
        weeks={[
          week({ delta: 2 }),
          week({ start: "2026-11-09", end: "2026-11-15", delta: -1 }),
        ]}
        onOpenWeek={() => {}}
      />,
    );
    expect(text).toContain("+2");
    expect(text).toContain("-1");
  });
});

describe("LessonsTable", () => {
  it("shows a row per lesson with the weekday, the time and the outcome", () => {
    const ui = lessonsUi([
      lesson(),
      lesson({
        date: "2026-11-03",
        outcome: "cancelled",
        start: "10:00",
        end: "10:45",
      }),
    ]);
    const rows = bodyRows(ui);
    expect(rows).toHaveLength(2);
    expect(textOf(rows[0])).toContain("Пн 02.11");
    expect(textOf(rows[0])).toContain("08:30–09:15");
    expect(textOf(rows[1])).toContain("Вт 03.11");
    expect(textOf(rows[1])).toContain("Отменён");
  });
  it("writes the raw value for an outcome it does not know", () => {
    expect(
      textOf(bodyRows(lessonsUi([lesson({ outcome: "from-the-future" })]))[0]),
    ).toContain("from-the-future");
  });
  it("tells the notes of a lesson in its row", () => {
    const row = bodyRows(
      lessonsUi([lesson({ kind: "substitution", confirmed: false })]),
    )[0];
    const text = textOf(row);
    expect(text).toContain("Замена");
    expect(text).toContain("не подтверждён журналом");
  });
  it("shows the first page and says how many are shown, with a button for more", () => {
    let more = 0;
    const many = Array.from({ length: LESSON_PAGE + 20 }, (_, i) =>
      lesson({ start: `${i}` }),
    );
    const ui = lessonsUi(many, { onMore: () => (more += 1) });
    expect(bodyRows(ui)).toHaveLength(LESSON_PAGE);
    expect(textOf(ui)).toContain(
      `Показано ${LESSON_PAGE} из ${LESSON_PAGE + 20}`,
    );
    const buttons = find(ui, "button");
    expect(buttons).toHaveLength(1);
    buttons[0].props.onClick!();
    expect(more).toBe(1);
  });
  it("shows more rows when the limit grows and drops the button when all are shown", () => {
    const many = Array.from({ length: LESSON_PAGE + 20 }, () => lesson());
    const ui = lessonsUi(many, { limit: LESSON_PAGE * 2 });
    expect(bodyRows(ui)).toHaveLength(LESSON_PAGE + 20);
    expect(find(ui, "button")).toHaveLength(0);
  });
  it("narrows the rows by the filter and the week", () => {
    const lessons = [
      lesson({ date: "2026-11-02" }),
      lesson({ date: "2026-11-03", outcome: "upcoming" }),
      lesson({ date: "2026-11-10", outcome: "upcoming" }),
    ];
    const rows = bodyRows(
      lessonsUi(lessons, {
        filter: "upcoming",
        week: { start: "2026-11-02", end: "2026-11-08" },
      }),
    );
    expect(rows).toHaveLength(1);
    expect(textOf(rows[0])).toContain("Вт 03.11");
  });
  it("says differently that there are no lessons and that the filter hides them all", () => {
    const none = textOf(lessonsUi([])).trim();
    const hidden = textOf(lessonsUi([lesson()], { filter: "upcoming" })).trim();
    expect(none).not.toBe("");
    expect(hidden).not.toBe("");
    expect(none).not.toBe(hidden);
    expect(
      find(lessonsUi([lesson()], { filter: "upcoming" }), "tr"),
    ).toHaveLength(0);
  });
});

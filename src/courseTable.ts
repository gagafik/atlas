/** Pure helpers of the course table: parsing and pasting marks, keyboard movement, the save requests and their partial results, CSV. */
import { csvCell, download } from "./utils";

export type CourseKind = "course" | "admin" | "class";
export interface CourseCategory {
  id: string;
  name: string;
  title: string;
  color: string;
  active: boolean;
}
export interface CourseLesson {
  lessonKey: string;
  day: string;
  start: string;
  end: string;
  version: number;
  publishedVersion: number | null;
  editable: boolean;
  reason: string;
  rosterFrozen: boolean;
  hasUnpublished: boolean;
}
export interface CourseWork {
  // Index into CourseTable.lessons.
  lesson: number;
  id: string;
  name: string;
  kind: "points" | "label";
  maxScore: number;
  weight: number;
  category: string;
}
export interface CourseMark {
  score: number | null;
  label: string;
  comment: string;
  // A draft mark the families do not see yet.
  unpublished?: boolean;
}
export interface CoursePupil {
  id: string;
  name: string;
  classId: string | null;
  className: string;
  from: string | null;
  to: string | null;
  // Indexes of the lessons whose roster lists the pupil; other columns are off the roster for them.
  lessons: number[];
  // By index into CourseTable.works.
  marks: Record<string, CourseMark>;
  categories: {
    id: string;
    name: string;
    color: string;
    numeric: number;
    percentage: number;
  }[];
  sharePercentage: number | null;
  blocked: boolean;
  missing: string[];
  excluded: string[];
  rule: string;
}
export interface CourseTable {
  revision: number;
  groupId: string;
  group: string;
  subjectId: string;
  subject: string;
  scope: CourseKind;
  canEdit: boolean;
  source: "draft" | "published";
  start: string;
  end: string;
  categories: CourseCategory[];
  lessons: CourseLesson[];
  works: CourseWork[];
  pupils: CoursePupil[];
}
export interface CourseItem {
  groupId: string;
  group: string;
  subjectId: string;
  subject: string;
  classes: string[];
  teachers: string[];
  kind: CourseKind;
  canEdit: boolean;
}
export interface CoursePeriod {
  id: string;
  name: string;
  start: string;
  end: string;
  kind: string;
  status: string;
}
export interface CourseList {
  revision: number;
  today: string;
  yearStart: string;
  yearEnd: string;
  periods: CoursePeriod[];
  courses: CourseItem[];
}
export interface Position {
  row: number;
  col: number;
}
/** One typed or pasted value. `error` is found here before saving, `message` comes back from the server. */
export interface Edit {
  studentId: string;
  lessonKey: string;
  assessmentId: string;
  raw: string;
  score: number | null;
  label: string;
  error?: string;
  message?: string;
}
export interface LessonBody {
  lessonKey: string;
  day: string;
  version: number;
  cells: {
    studentId: string;
    assessmentId: string;
    score: number | null;
    label: string;
  }[];
}
export interface SaveResponse {
  saved: { lessonKey: string }[];
  unchanged: string[];
  conflicts: {
    lessonKey: string;
    day: string;
    status: number;
    message: string;
    cells: { studentId: string; assessmentId: string; message: string }[];
  }[];
}
export interface SaveOutcome {
  edits: Edit[];
  saved: number;
  unchanged: number;
  conflicts: { lessonKey: string; day: string; message: string }[];
}

export const LESSONS_PER_REQUEST = 10;
const POINTS = /^(\d+([.,]\d+)?|[.,]\d+)$/;

export const editKey = (e: {
  studentId: string;
  lessonKey: string;
  assessmentId: string;
}) => `${e.studentId}|${e.lessonKey}|${e.assessmentId}`;

export function parseCell(
  raw: string,
  work: Pick<CourseWork, "kind" | "maxScore">,
): { score: number | null; label: string } | { error: string } {
  const text = raw.trim();
  if (work.kind === "label") {
    if (text.length > 30)
      return { error: "Текстовая оценка — не больше 30 символов." };
    return { score: null, label: text };
  }
  if (!text) return { score: null, label: "" };
  const range = `Баллы — число от 0 до ${work.maxScore}.`;
  if (!POINTS.test(text)) return { error: range };
  const score = Number(text.replace(",", "."));
  return score > work.maxScore ? { error: range } : { score, label: "" };
}
/** The text of a cell as the input shows it: a saved mark, or the typed edit. */
export function markText(
  mark: CourseMark | undefined,
  work: Pick<CourseWork, "kind">,
): string {
  if (!mark) return "";
  return work.kind === "points" ? (mark.score ?? "").toString() : mark.label;
}
export function editable(
  pupil: CoursePupil,
  work: CourseWork,
  table: CourseTable,
): boolean {
  return (
    table.canEdit &&
    !!table.lessons[work.lesson]?.editable &&
    pupil.lessons.includes(work.lesson)
  );
}
export function makeEdit(
  raw: string,
  pupil: CoursePupil,
  work: CourseWork,
  table: CourseTable,
): Edit {
  const parsed = parseCell(raw, work);
  return {
    studentId: pupil.id,
    lessonKey: table.lessons[work.lesson].lessonKey,
    assessmentId: work.id,
    raw,
    ...("error" in parsed
      ? { score: null, label: "", error: parsed.error }
      : parsed),
  };
}
/** Rows of tab-separated cells, as spreadsheets copy them; the newline that ends the last row is not another row. */
export function parsePaste(text: string): string[][] {
  const rows = text.split(/\r\n|\n/);
  if (rows.length && rows[rows.length - 1] === "") rows.pop();
  return rows.map((row) => row.split("\t"));
}
/** Pasted values from the active cell down and right. A blank never clears; places that are not editable or outside the table are counted. */
export function applyPaste(
  matrix: string[][],
  origin: Position,
  table: CourseTable,
): { edits: Edit[]; skipped: number } {
  const edits: Edit[] = [];
  let skipped = 0;
  matrix.forEach((cells, r) =>
    cells.forEach((raw, c) => {
      if (!raw.trim()) return;
      const pupil = table.pupils[origin.row + r];
      const work = table.works[origin.col + c];
      if (!pupil || !work || !editable(pupil, work, table)) skipped++;
      else edits.push(makeEdit(raw, pupil, work, table));
    }),
  );
  return { edits, skipped };
}
const STEPS: Record<string, [number, number]> = {
  ArrowUp: [-1, 0],
  ArrowDown: [1, 0],
  ArrowLeft: [0, -1],
  ArrowRight: [0, 1],
  Enter: [1, 0],
  "Shift+Enter": [-1, 0],
  Tab: [0, 1],
  "Shift+Tab": [0, -1],
};
/** The next editable cell in the direction of the key, stepping over cells off the roster or in locked lessons; the same place at the edge. */
export function nextCell(
  position: Position,
  key: string,
  table: CourseTable,
): Position {
  const step = STEPS[key];
  if (!step) return position;
  let { row, col } = position;
  for (;;) {
    row += step[0];
    col += step[1];
    const pupil = table.pupils[row];
    const work = table.works[col];
    if (!pupil || !work) return position;
    if (editable(pupil, work, table)) return { row, col };
  }
}
/** One request body per lesson that has sendable edits. Publishing also lists the lessons with unpublished marks, without cells. */
export function lessonBodies(
  table: CourseTable,
  edits: Edit[],
  publish: boolean,
): LessonBody[] {
  const sendable = edits.filter((e) => !e.error);
  return table.lessons.flatMap((lesson) => {
    if (!lesson.editable) return [];
    const cells = sendable
      .filter((e) => e.lessonKey === lesson.lessonKey)
      .map(({ studentId, assessmentId, score, label }) => ({
        studentId,
        assessmentId,
        score,
        label,
      }));
    if (!cells.length && !(publish && lesson.hasUnpublished)) return [];
    return [
      {
        lessonKey: lesson.lessonKey,
        day: lesson.day,
        version: lesson.version,
        cells,
      },
    ];
  });
}
export function chunks<T>(items: T[], size = LESSONS_PER_REQUEST): T[][] {
  const result: T[][] = [];
  for (let i = 0; i < items.length; i += size)
    result.push(items.slice(i, i + size));
  return result;
}
/** What is left to type after the server answered: the edits of saved lessons are done, those of conflicting lessons stay with the reason. */
export function afterSave(
  edits: Edit[],
  responses: SaveResponse[],
): SaveOutcome {
  const done = new Set<string>();
  const conflicts = new Map<
    string,
    { day: string; message: string; cells: Map<string, string> }
  >();
  let saved = 0;
  let unchanged = 0;
  for (const response of responses) {
    for (const s of response.saved) {
      done.add(s.lessonKey);
      saved++;
    }
    for (const key of response.unchanged) {
      done.add(key);
      unchanged++;
    }
    for (const c of response.conflicts)
      conflicts.set(c.lessonKey, {
        day: c.day,
        message: c.message,
        cells: new Map(
          c.cells.map((x) => [`${x.studentId}|${x.assessmentId}`, x.message]),
        ),
      });
  }
  return {
    edits: edits.flatMap((e) => {
      // Edits with a local error were never sent, so nothing was decided about them.
      if (e.error) return [e];
      const conflict = conflicts.get(e.lessonKey);
      if (conflict)
        return [
          {
            ...e,
            message:
              conflict.cells.get(`${e.studentId}|${e.assessmentId}`) ??
              conflict.message,
          },
        ];
      return done.has(e.lessonKey) ? [] : [e];
    }),
    saved,
    unchanged,
    conflicts: [...conflicts].map(([lessonKey, c]) => ({
      lessonKey,
      day: c.day,
      message: c.message,
    })),
  };
}
/** The period of the opening view: the one that contains today, else the latest that has ended, else the year so far. */
export function defaultCoursePeriod(
  today: string,
  yearStart: string,
  yearEnd: string,
  periods: Pick<CoursePeriod, "id" | "start" | "end">[],
): { start: string; end: string; periodId: string | null } {
  const current = periods
    .filter((p) => p.start <= today && today <= p.end)
    // The narrowest period that began last: a quarter rather than the year.
    .sort(
      (a, b) => b.start.localeCompare(a.start) || a.end.localeCompare(b.end),
    )[0];
  const ended = periods
    .filter((p) => p.end < today)
    .sort((a, b) => b.end.localeCompare(a.end))[0];
  const found = current ?? ended;
  if (found) return { start: found.start, end: found.end, periodId: found.id };
  const end = today > yearEnd ? yearEnd : today < yearStart ? yearStart : today;
  return { start: yearStart, end, periodId: null };
}
function ruDay(iso: string) {
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${d}.${m}.${y}` : iso;
}
export function workTitle(work: CourseWork, table: CourseTable): string {
  const category = table.categories.find((c) => c.id === work.category);
  return `${ruDay(table.lessons[work.lesson].day)} ${category?.name ?? work.category} ${work.name} (макс ${work.maxScore}, вес ${work.weight})`;
}
/** Only what the table shows as saved: marks, the category percentages and the share average. A column without a place for the pupil reads «—». */
export function courseCsv(table: CourseTable, filename?: string) {
  const values: unknown[][] = [
    [
      "Ученик",
      "Класс",
      ...table.works.map((w) => workTitle(w, table)),
      ...table.categories.map((c) => `${c.name} %`),
      "Итог по долям %",
    ],
  ];
  for (const pupil of table.pupils)
    values.push([
      pupil.name,
      pupil.className,
      ...table.works.map((w, i) => {
        const mark = pupil.marks[i];
        if (mark) return markText(mark, w);
        return pupil.lessons.includes(w.lesson) ? "" : "—";
      }),
      ...table.categories.map(
        (c) => pupil.categories.find((x) => x.id === c.id)?.percentage ?? "",
      ),
      pupil.sharePercentage ?? "",
    ]);
  download(
    filename ?? `Таблица курса ${table.group} ${table.subject}.csv`,
    "﻿" +
      values
        .map((r) => r.map((v) => csvCell(String(v ?? ""))).join(";"))
        .join("\r\n"),
    "text/csv;charset=utf-8",
  );
}

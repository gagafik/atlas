import { describe, expect, it } from "vitest";
import {
  NEW_WORK,
  canSend,
  closedLessons,
  formComplete,
  lessonOption,
  overwriteIds,
  pickLesson,
  reviewedScore,
  targetBody,
  transferBody,
  type Preview,
  type PreviewRow,
  type TransferForm,
  type TransferLesson,
} from "./quizTransfer";
import type { Attempt } from "./quiz";

const row = (
  studentId: string,
  outcome: PreviewRow["outcome"],
): PreviewRow => ({
  studentId,
  name: studentId,
  enrolled: true,
  attemptId: "a-" + studentId,
  attemptNumber: 1,
  score: 2,
  maxScore: 2,
  value: 2,
  scaled: false,
  draft: null,
  published: null,
  outcome,
  reason: "",
});
const preview = (
  rows: PreviewRow[],
  extra: Partial<Preview> = {},
): Preview => ({
  allowed: true,
  reason: "",
  work: null,
  workExists: false,
  rows,
  counts: Object.fromEntries(
    ["write", "update", "conflict", "same", "skip"].map((k) => [
      k,
      rows.filter((r) => r.outcome === k).length,
    ]),
  ),
  token: "t".repeat(64),
  otherPending: false,
  hasPublished: false,
  ...extra,
});
const form: TransferForm = {
  day: "2026-08-17",
  lessonKey: "k".repeat(64),
  workId: NEW_WORK,
  name: " Контрольная ",
  category: "sor",
  weight: 1,
  maxScore: 2,
  pick: "last",
  publish: false,
};

describe("overwriteIds", () => {
  it("ignores chosen pupils whose row is no longer a conflict", () => {
    const rows = [row("s1", "conflict"), row("s2", "same"), row("s3", "write")];
    expect(overwriteIds(rows, ["s1", "s2", "s3", "gone"])).toEqual(["s1"]);
    expect(overwriteIds(rows, [])).toEqual([]);
  });
  it("is what a confirmed transfer carries", () => {
    const rows = [row("s1", "conflict"), row("s2", "conflict")];
    const body = transferBody(
      preview(rows),
      form,
      "r".repeat(36),
      new Set(["s2", "s9"]),
      7,
    );
    expect(body.overwrite).toEqual(["s2"]);
    expect(body.token).toBe("t".repeat(64));
    expect(body.requestId).toBe("r".repeat(36));
  });
});

describe("targetBody", () => {
  it("sends a new work and no assessment id", () => {
    const body = targetBody(form, 3);
    expect(body).not.toHaveProperty("assessmentId");
    expect(body.newWork).toEqual({
      name: "Контрольная",
      category: "sor",
      weight: 1,
      maxScore: 2,
    });
    expect(body.revision).toBe(3);
  });
  it("sends an existing work and no new work", () => {
    const body = targetBody({ ...form, workId: "a" }, 3);
    expect(body).not.toHaveProperty("newWork");
    expect(body.assessmentId).toBe("a");
  });
});

describe("canSend", () => {
  it("is false when the preview is refused or there is nothing to change", () => {
    expect(canSend(null, false, [])).toBe(false);
    expect(
      canSend(preview([row("s1", "write")], { allowed: false }), false, []),
    ).toBe(false);
    expect(
      canSend(preview([row("s1", "write")], { token: "" }), false, []),
    ).toBe(false);
    const idle = preview([
      row("s1", "same"),
      row("s2", "skip"),
      row("s3", "conflict"),
    ]);
    expect(canSend(idle, false, [])).toBe(false);
    expect(canSend(idle, true, [])).toBe(false);
  });
  it("is true for marks to write, update or a chosen replacement", () => {
    expect(canSend(preview([row("s1", "write")]), false, [])).toBe(true);
    expect(canSend(preview([row("s1", "update")]), false, [])).toBe(true);
    const conflict = preview([row("s1", "conflict")]);
    expect(canSend(conflict, false, ["s1"])).toBe(true);
    expect(canSend(conflict, false, ["other"])).toBe(false);
  });
  it("is true for the publication of draft transfers that are still pending", () => {
    const pending = preview([row("s1", "same")], { otherPending: true });
    expect(canSend(pending, true, [])).toBe(true);
    expect(canSend(pending, false, [])).toBe(false);
  });
});

const lesson = (
  lessonKey: string,
  editable: boolean,
  extra: Partial<TransferLesson> = {},
): TransferLesson => ({
  lessonKey,
  day: "2026-08-17",
  start: "09:00",
  end: "09:45",
  version: 1,
  editable,
  reason: editable ? "" : "Урок ведёт другой учитель.",
  rosterFrozen: false,
  withdrawn: false,
  hasPublished: false,
  hasUnpublished: false,
  works: [],
  ...extra,
});

describe("lessons of the day", () => {
  it("lists a lesson that cannot take marks but does not let it be chosen", () => {
    expect(lessonOption(lesson("a", true)).disabled).toBe(false);
    expect(lessonOption(lesson("b", false)).disabled).toBe(true);
    expect(lessonOption(lesson("b", false, { withdrawn: true })).disabled).toBe(
      true,
    );
  });
  it("never holds a closed lesson while another one takes marks", () => {
    const day = [lesson("closed", false), lesson("open", true)];
    expect(pickLesson(day, "")?.lessonKey).toBe("open");
    expect(pickLesson(day, "closed")?.lessonKey).toBe("open");
    expect(pickLesson(day, "gone")?.lessonKey).toBe("open");
  });
  it("keeps the chosen lesson while it takes marks", () => {
    const day = [lesson("one", true), lesson("two", true)];
    expect(pickLesson(day, "two")?.lessonKey).toBe("two");
    expect(pickLesson(day, "gone")?.lessonKey).toBe("one");
  });
  it("falls back to a closed lesson only when every lesson is closed, so its reason shows", () => {
    const day = [lesson("one", false), lesson("two", false)];
    expect(pickLesson(day, "two")?.lessonKey).toBe("two");
    expect(pickLesson(day, "")?.lessonKey).toBe("one");
    expect(pickLesson([], "one")).toBeUndefined();
  });
  it("names the reason of every closed lesson", () => {
    const day = [
      lesson("open", true),
      lesson("c1", false, { reason: "Период закрыт." }),
      lesson("c2", false),
    ];
    expect(closedLessons(day).map((c) => [c.key, c.reason])).toEqual([
      ["c1", "Период закрыт."],
      ["c2", "Урок ведёт другой учитель."],
    ]);
    expect(closedLessons([lesson("open", true)])).toEqual([]);
  });
});

describe("helpers", () => {
  it("tells whether the form is complete", () => {
    expect(formComplete(form)).toBe(true);
    expect(formComplete({ ...form, name: "  " })).toBe(false);
    expect(formComplete({ ...form, lessonKey: "" })).toBe(false);
    expect(formComplete({ ...form, workId: "" })).toBe(false);
    expect(formComplete({ ...form, workId: "a", name: "" })).toBe(true);
  });
  it("takes the newest reviewed attempt", () => {
    const at = (number: number, status: Attempt["status"]) =>
      ({ id: String(number), number, status }) as Attempt;
    expect(
      reviewedScore([at(1, "reviewed"), at(2, "reviewed"), at(3, "active")])
        ?.number,
    ).toBe(2);
    expect(reviewedScore([at(1, "submitted")])).toBeNull();
  });
});

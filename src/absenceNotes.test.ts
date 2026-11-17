import { describe, expect, it } from "vitest";
import {
  allPicked,
  bulkEligible,
  changedLabel,
  factsLine,
  filedBy,
  periodLabel,
  pickEligible,
  statusTone,
  subjectName,
  type AbsenceNote,
} from "./absenceNotes";

const note = (over: Partial<AbsenceNote> = {}): AbsenceNote => ({
  id: "n1",
  studentId: "s1",
  studentName: "Алия Нурова",
  version: 1,
  status: "pending",
  start: "2026-10-19T00:00",
  end: "2026-10-19T23:59",
  created: "2026-10-18T10:00",
  ...over,
});

describe("periodLabel", () => {
  it("shows one full day as a date", () => {
    expect(periodLabel("2026-10-19T00:00", "2026-10-19T23:59")).toBe(
      "19.10.2026",
    );
  });
  it("adds the times of one day that is not whole", () => {
    expect(periodLabel("2026-10-19T08:00", "2026-10-19T12:30")).toBe(
      "19.10.2026 08:00–12:30",
    );
  });
  it("shows several full days as two dates", () => {
    expect(periodLabel("2026-10-19T00:00", "2026-10-22T23:59")).toBe(
      "19.10.2026 – 22.10.2026",
    );
  });
  it("keeps the times when the days are several and not whole", () => {
    expect(periodLabel("2026-10-19T08:00", "2026-10-22T12:30")).toBe(
      "19.10.2026 08:00 – 22.10.2026 12:30",
    );
    // A whole first day does not make the period whole when the last one ends early.
    expect(periodLabel("2026-10-19T00:00", "2026-10-22T12:30")).toBe(
      "19.10.2026 00:00 – 22.10.2026 12:30",
    );
  });
  it("does not move the day with the time zone", () => {
    expect(periodLabel("2026-12-31T00:00", "2026-12-31T23:59")).toBe(
      "31.12.2026",
    );
  });
});

describe("statusTone", () => {
  it("tells apart the outcomes", () => {
    expect(statusTone("pending")).toBe("neutral");
    expect(statusTone("approved")).toBe("positive");
    expect(statusTone("rejected")).toBe("danger");
    expect(statusTone("revoked")).toBe("danger");
    expect(statusTone("withdrawn")).toBe("muted");
  });
});

describe("filedBy", () => {
  it("names the school for notes it entered, whoever looks", () => {
    const school = { origin: "school", mine: false };
    expect(filedBy(school, { parent: true, reviewer: false })).toBe("Школа");
    expect(filedBy(school, { parent: false, reviewer: true })).toBe("Школа");
  });
  it("tells a parent their own note from a note of another guardian", () => {
    const parent = { parent: true, reviewer: false };
    expect(filedBy({ mine: true }, parent)).toBe("Вы");
    expect(filedBy({}, parent)).toBe("Вы");
    expect(filedBy({ mine: false }, parent)).toBe("Другой представитель");
  });
  it("calls a family note a family note for the reviewer", () => {
    expect(filedBy({ mine: false }, { parent: false, reviewer: true })).toBe(
      "Семья",
    );
  });
});

describe("shared decision selection", () => {
  const rows = [
    { id: "a", status: "pending" },
    { id: "b", status: "approved" },
    { id: "c", status: "pending" },
    { id: "d", status: "rejected" },
  ];
  const many = Array.from({ length: 70 }, (_, i) => ({
    id: `n${i}`,
    status: "pending",
  }));
  it("takes pending notes for approve and reject, approved ones for revoke", () => {
    expect(bulkEligible("pending", "approve")).toBe(true);
    expect(bulkEligible("pending", "reject")).toBe(true);
    expect(bulkEligible("approved", "approve")).toBe(false);
    expect(bulkEligible("approved", "revoke")).toBe(true);
    expect(bulkEligible("pending", "revoke")).toBe(false);
    expect(pickEligible(rows, "approve")).toEqual(["a", "c"]);
    expect(pickEligible(rows, "revoke")).toEqual(["b"]);
  });
  it("takes at most fifty, the first ones of the list", () => {
    const ids = pickEligible(many, "approve");
    expect(ids).toHaveLength(50);
    expect(ids[0]).toBe("n0");
    expect(ids[49]).toBe("n49");
  });
  it("counts the header checkbox as checked only when everything pickable is picked", () => {
    expect(allPicked(rows, ["a", "c"], "approve")).toBe(true);
    expect(allPicked(rows, ["a"], "approve")).toBe(false);
    expect(allPicked(rows, [], "approve")).toBe(false);
    // Nothing can be picked: nothing is "all picked".
    expect(allPicked([{ id: "x", status: "rejected" }], [], "approve")).toBe(
      false,
    );
  });
  it("is checked with fifty picked out of seventy", () => {
    expect(allPicked(many, pickEligible(many, "approve"), "approve")).toBe(
      true,
    );
  });
});

describe("subjectName", () => {
  it("says «Все» when the note has no subject", () => {
    expect(subjectName([{ id: "m", name: "Математика" }], null)).toBe("Все");
    expect(subjectName([{ id: "m", name: "Математика" }], "m")).toBe(
      "Математика",
    );
  });
});

describe("factsLine", () => {
  const names = (by = "Семья") => ({
    codes: [{ id: "ill", name: "Болезнь" }],
    subjects: [{ id: "m", name: "Математика" }],
    filedBy: by,
  });
  it("lists period, type, subject and who filed it", () => {
    const line = factsLine(note({ codeId: "ill", subjectId: "m" }), names());
    expect(line).toContain("19.10.2026");
    expect(line).toContain("Болезнь");
    expect(line).toContain("Математика");
    expect(line).toContain("Семья");
    expect(line).not.toContain("Автор");
  });
  it("adds the author only when it is known", () => {
    expect(factsLine(note({ authorName: "Мать" }), names())).toContain(
      "Автор: Мать",
    );
  });
  it("names a note without a type", () => {
    expect(factsLine(note(), names())).toContain("Без типа");
  });
});

describe("changedLabel", () => {
  it("is empty for a class that was never switched", () => {
    expect(changedLabel({ updated: null, actor: null })).toBe("");
  });
  it("names who switched it", () => {
    expect(
      changedLabel({ updated: "2026-10-19T08:00:00", actor: "Админ" }),
    ).toContain(", Админ");
  });
});

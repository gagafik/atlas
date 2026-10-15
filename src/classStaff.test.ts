import { expect, it } from "vitest";
import { classAssignmentChanges } from "./classStaff";

it("shows additions, removal and narrowing per class without confusing capability order", () => {
  const changes = classAssignmentChanges(
    {
      a: [
        {
          accountId: "same",
          capabilities: ["attendance_read", "journal_coverage"],
        },
        { accountId: "removed", capabilities: ["absence_review"] },
      ],
      b: [
        {
          accountId: "narrow",
          capabilities: ["attendance_read", "absence_review"],
        },
      ],
    },
    {
      a: [
        {
          accountId: "same",
          capabilities: ["journal_coverage", "attendance_read"],
        },
      ],
      b: [
        { accountId: "narrow", capabilities: ["attendance_read"] },
        { accountId: "new", capabilities: ["journal_coverage"] },
      ],
    },
  );
  expect(changes).toEqual([
    {
      classId: "a",
      accountId: "removed",
      before: ["absence_review"],
      after: [],
    },
    {
      classId: "b",
      accountId: "narrow",
      before: ["attendance_read", "absence_review"],
      after: ["attendance_read"],
    },
    { classId: "b", accountId: "new", before: [], after: ["journal_coverage"] },
  ]);
});

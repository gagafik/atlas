import { expect, it } from "vitest";
import {
  classAssignmentChanges,
  type ClassAssignments,
  type ClassCapability,
} from "./classStaff";

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
      beforeRevoked: [],
      afterRevoked: [],
    },
    {
      classId: "b",
      accountId: "narrow",
      before: ["attendance_read", "absence_review"],
      after: ["attendance_read"],
      beforeRevoked: [],
      afterRevoked: [],
    },
    {
      classId: "b",
      accountId: "new",
      before: [],
      after: ["journal_coverage"],
      beforeRevoked: [],
      afterRevoked: [],
    },
  ]);
});

it("reports a revocation from the class teacher and ignores the order of revoked duties", () => {
  const rows = (revoked: ClassCapability[]): ClassAssignments => ({
    a: [{ accountId: "u", capabilities: [], revoked }],
  });
  expect(
    classAssignmentChanges(
      { a: [{ accountId: "u", capabilities: [] }] },
      rows(["absence_review"]),
    ),
  ).toEqual([
    {
      classId: "a",
      accountId: "u",
      before: [],
      after: [],
      beforeRevoked: [],
      afterRevoked: ["absence_review"],
    },
  ]);
  expect(
    classAssignmentChanges(rows(["absence_review"]), rows(["absence_review"])),
  ).toEqual([]);
  expect(
    classAssignmentChanges(
      rows(["absence_review", "attendance_read"]),
      rows(["attendance_read", "absence_review"]),
    ),
  ).toEqual([]);
});

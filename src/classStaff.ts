export const classCapabilities = {
  absence_review: "Рассмотрение объяснительных",
  attendance_read: "Сводная посещаемость",
  journal_coverage: "Контроль заполнения журналов",
  behaviour_notes: "Похвалы и замечания",
};
export type ClassCapability = keyof typeof classCapabilities;
export type ClassAssignment = {
  accountId: string;
  capabilities: ClassCapability[];
  // Duties taken from this account as the class teacher of the class.
  revoked?: ClassCapability[];
};
export type ClassAssignments = Record<string, ClassAssignment[]>;

export function classAssignmentChanges(
  before: ClassAssignments,
  after: ClassAssignments,
) {
  const changes: {
    classId: string;
    accountId: string;
    before: ClassCapability[];
    after: ClassCapability[];
    beforeRevoked: ClassCapability[];
    afterRevoked: ClassCapability[];
  }[] = [];
  for (const classId of new Set([
    ...Object.keys(before),
    ...Object.keys(after),
  ])) {
    const old = before[classId] || [],
      next = after[classId] || [];
    for (const accountId of new Set([
      ...old.map((r) => r.accountId),
      ...next.map((r) => r.accountId),
    ])) {
      const was = old.find((r) => r.accountId === accountId);
      const now = next.find((r) => r.accountId === accountId);
      const a = was?.capabilities || [];
      const b = now?.capabilities || [];
      const ra = was?.revoked || [];
      const rb = now?.revoked || [];
      const same = (x: string[], y: string[]) =>
        [...x].sort().join() === [...y].sort().join();
      if (!same(a, b) || !same(ra, rb))
        changes.push({
          classId,
          accountId,
          before: a,
          after: b,
          beforeRevoked: ra,
          afterRevoked: rb,
        });
    }
  }
  return changes;
}

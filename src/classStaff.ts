export const classCapabilities = {
  absence_review: "Рассмотрение объяснительных",
  attendance_read: "Сводная посещаемость",
  journal_coverage: "Контроль заполнения журналов",
};
export type ClassCapability = keyof typeof classCapabilities;
export type ClassAssignment = {
  accountId: string;
  capabilities: ClassCapability[];
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
      const a = old.find((r) => r.accountId === accountId)?.capabilities || [];
      const b = next.find((r) => r.accountId === accountId)?.capabilities || [];
      if ([...a].sort().join() !== [...b].sort().join())
        changes.push({ classId, accountId, before: a, after: b });
    }
  }
  return changes;
}

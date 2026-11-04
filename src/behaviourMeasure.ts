/** The part of a category the choice of a measure looks at. */
export interface MeasureCandidate {
  id: string;
  kind: string;
  level: string;
  letter: boolean;
  active: boolean;
}

/**
 * The category «Воспитательная мера» opens the form with: a measure (a letter, or a decision of the class teacher or the director)
 * that the account may give to every one of the pupils marked; a remark before a praise. `undefined` when there is none.
 * The server decides again on every note: the class teacher's level holds only for the pupils of the classes they lead.
 */
export function measureCategory<C extends MeasureCandidate>(
  categories: C[],
  who: { admin: boolean; myLevels: string[]; myClassIds: string[] },
  pupils: { classId: string }[],
): C | undefined {
  const fits = (c: C) =>
    c.active &&
    who.myLevels.includes(c.level) &&
    (c.level === "teacher"
      ? c.letter
      : c.level !== "class_teacher" ||
        who.admin ||
        pupils.every((p) => who.myClassIds.includes(p.classId)));
  const found = categories.filter(fits);
  return found.find((c) => c.kind === "remark") ?? found[0];
}

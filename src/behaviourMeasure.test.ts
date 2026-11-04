import { describe, it, expect } from "vitest";
import { measureCategory } from "./behaviourMeasure";

const category = (
  id: string,
  kind: string,
  level: string,
  letter = false,
  active = true,
) => ({ id, kind, level, letter, active });
const categories = [
  category("activity", "praise", "teacher"),
  category("class-praise", "praise", "class_teacher"),
  category("class-remark", "remark", "class_teacher"),
  category("director-remark", "remark", "director", true),
];
const classTeacher = {
  admin: false,
  myLevels: ["class_teacher", "teacher"],
  myClassIds: ["c7a"],
};

describe("measureCategory", () => {
  it("takes a measure of the class teacher for the pupils of the class they lead, a remark first", () => {
    expect(
      measureCategory(categories, classTeacher, [{ classId: "c7a" }])?.id,
    ).toBe("class-remark");
  });
  it("gives nothing when a pupil is outside the classes the account leads", () => {
    expect(
      measureCategory(categories, classTeacher, [
        { classId: "c7a" },
        { classId: "c7b" },
      ]),
    ).toBeUndefined();
  });
  it("lets a teacher give a measure only when its category is a letter", () => {
    const teacher = { admin: false, myLevels: ["teacher"], myClassIds: [] };
    expect(
      measureCategory(categories, teacher, [{ classId: "c7b" }]),
    ).toBeUndefined();
    expect(
      measureCategory(
        [...categories, category("letter", "remark", "teacher", true)],
        teacher,
        [{ classId: "c7b" }],
      )?.id,
    ).toBe("letter");
  });
  it("gives the administrator the first remark of any level and skips categories that are switched off", () => {
    const admin = {
      admin: true,
      myLevels: ["teacher", "class_teacher", "director"],
      myClassIds: [],
    };
    expect(
      measureCategory(
        [
          category("off", "remark", "class_teacher", false, false),
          ...categories,
        ],
        admin,
        [{ classId: "c7b" }],
      )?.id,
    ).toBe("class-remark");
  });
});

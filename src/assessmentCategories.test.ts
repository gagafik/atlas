import { describe, it, expect } from "vitest";
import {
  FALLBACK,
  WEIGHT_PRESETS,
  bookingDefault,
  categoryOf,
  configProblem,
  presetOf,
  textColor,
  type Draft,
} from "./assessmentCategories";

const catalog = [
  FALLBACK,
  {
    id: "sor",
    name: "СОР",
    title: "Суммативное оценивание за раздел",
    color: "#d97706",
    calendar: "summative_unit" as const,
    active: true,
  },
  {
    id: "soch",
    name: "СОЧ",
    title: "Суммативное оценивание за четверть",
    color: "#dc2626",
    calendar: "summative_term" as const,
    active: true,
  },
];
const defaults = (): Draft => ({
  categories: structuredClone(catalog),
  defaultCategory: "fo",
  policy: {
    shares: [
      { categoryId: "fo", percent: "25" },
      { categoryId: "sor", percent: "25" },
      { categoryId: "soch", percent: "50" },
    ],
    method: "average",
    missing: "renormalize",
  },
  rules: [],
});

describe("categoryOf", () => {
  it("reads a missing category as ФО and an unknown one by its id", () => {
    expect(categoryOf({}, null)).toBe(FALLBACK);
    expect(categoryOf({ category: "" }, { categories: [] })).toBe(FALLBACK);
    expect(categoryOf({ category: "sor" }, { categories: catalog }).name).toBe(
      "СОР",
    );
    const unknown = categoryOf({ category: "gone" }, { categories: catalog });
    expect(unknown).toMatchObject({ id: "gone", name: "gone" });
    // A renamed ФО follows the school's catalog.
    const renamed = [{ ...FALLBACK, name: "ФО1" }, ...catalog.slice(1)];
    expect(categoryOf({}, { categories: renamed }).name).toBe("ФО1");
  });
});

describe("weight presets", () => {
  it("knows its presets and sends other weights to the custom input", () => {
    expect(WEIGHT_PRESETS.map((p) => p.value)).toEqual([0, 0.25, 0.5, 1, 2, 3]);
    expect(presetOf(0.5)).toBe(0.5);
    expect(presetOf(0)).toBe(0);
    expect(presetOf(1.5)).toBe("custom");
    expect(presetOf(Number.NaN)).toBe("custom");
  });
});

describe("configProblem", () => {
  it("accepts the defaults", () => {
    expect(configProblem(defaults())).toBe("");
  });
  it("reports a share sum other than 100 for the school and for a rule", () => {
    const cfg = defaults();
    cfg.policy.shares[2].percent = "49";
    expect(configProblem(cfg)).toBe(
      "Правило школы: доли категорий должны в сумме давать 100 % (сейчас 99 %).",
    );
    const withRule = defaults();
    withRule.rules.push({
      id: "a".repeat(32),
      name: "Физика",
      subjectIds: [],
      classIds: [],
      method: "points",
      missing: "block",
      shares: [
        { categoryId: "fo", percent: "60" },
        { categoryId: "sor", percent: "30" },
      ],
    });
    expect(configProblem(withRule)).toContain("Правило «Физика»");
    withRule.rules[0].shares[1].percent = "40";
    expect(configProblem(withRule)).toBe("");
  });
  it("sums decimal shares exactly", () => {
    const cfg = defaults();
    cfg.policy.shares = [
      { categoryId: "fo", percent: "33.33" },
      { categoryId: "sor", percent: "33.33" },
      { categoryId: "soch", percent: "33.34" },
    ];
    expect(configProblem(cfg)).toBe("");
    cfg.policy.shares[0].percent = "";
    expect(configProblem(cfg)).toContain("число от 0 до 100");
  });
  it("reports duplicate ids and names, an inactive default and a shared calendar type", () => {
    const duplicate = defaults();
    duplicate.categories[1].id = "fo";
    expect(configProblem(duplicate)).toBe(
      "Коды категорий не должны повторяться.",
    );
    const sameName = defaults();
    sameName.categories[1].name = " фо";
    expect(configProblem(sameName)).toBe(
      "Названия категорий не должны повторяться.",
    );
    const inactive = defaults();
    inactive.categories[0].active = false;
    expect(configProblem(inactive)).toBe(
      "Категория по умолчанию должна существовать и быть действующей.",
    );
    const shared = defaults();
    shared.categories[2].calendar = "summative_unit";
    expect(configProblem(shared)).toBe(
      "Тип календаря связан только с одной категорией.",
    );
    shared.categories[2].active = false;
    expect(configProblem(shared)).toBe("");
    const unnamed = defaults();
    unnamed.categories[1].name = "  ";
    expect(configProblem(unnamed)).toContain("Укажите название");
  });
  it("rejects a share that names an unknown category", () => {
    const cfg = defaults();
    cfg.policy.shares[2].categoryId = "ghost";
    expect(configProblem(cfg)).toContain("неизвестную категорию");
  });
});

describe("bookingDefault", () => {
  const booking = {
    id: "b".repeat(32),
    status: "published",
    typeId: "sor",
    type: "Суммативное оценивание за раздел",
    short: "СОР",
    topic: "Дроби",
    calendarCategory: "summative_unit",
    journalCategory: "sor",
    assessmentId: null,
  };
  it("takes the mapped category, the booking link and the topic as name", () => {
    expect(bookingDefault(booking)).toMatchObject({
      name: "Дроби",
      kind: "points",
      maxScore: 10,
      weight: 1,
      category: "sor",
      bookingId: booking.id,
    });
    expect(bookingDefault({ ...booking, topic: "" }).name).toBe(booking.type);
    expect(bookingDefault(booking).id).not.toBe(bookingDefault(booking).id);
  });
});

describe("textColor", () => {
  it("picks the readable colour", () => {
    expect(textColor("#ffffff")).toBe("#111");
    expect(textColor("#000000")).toBe("#fff");
    expect(textColor("#2563eb")).toBe("#fff");
    expect(textColor("#d97706")).toBe("#111");
  });
});

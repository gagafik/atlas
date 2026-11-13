import { describe, it, expect } from "vitest";
import {
  FALLBACK,
  bookingDefault,
  categoryOf,
  configProblem,
  presetOf,
  staleVersion,
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
  it("keeps a preset weight and sends other weights to the custom input", () => {
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
  it.each([
    ["33.33", "33.33", "33.34"],
    ["0.01", "33.34", "66.65"],
  ])("sums decimal shares %s, %s and %s exactly", (...percents) => {
    const cfg = defaults();
    cfg.policy.shares = ["fo", "sor", "soch"].map((categoryId, i) => ({
      categoryId,
      percent: percents[i],
    }));
    expect(configProblem(cfg)).toBe("");
  });
  it("accepts a share written without the leading zero", () => {
    const cfg = defaults();
    cfg.policy.shares = [
      { categoryId: "fo", percent: ".5" },
      { categoryId: "sor", percent: "49.5" },
      { categoryId: "soch", percent: "50" },
    ];
    expect(configProblem(cfg)).toBe("");
  });
  const rule = (shares: { categoryId: string; percent: string }[]) => ({
    id: "a".repeat(32),
    name: "Физика",
    subjectIds: [],
    classIds: [],
    method: "average" as const,
    missing: "renormalize" as const,
    shares,
  });
  const withShares =
    (...percents: string[]) =>
    (cfg: Draft) => {
      cfg.policy.shares = ["fo", "sor", "soch"].map((categoryId, i) => ({
        categoryId,
        percent: percents[i],
      }));
    };
  // Each case differs from the valid defaults by exactly one rule, and its fix gives the defaults back.
  const cases: {
    name: string;
    spoil: (cfg: Draft) => void;
    fix: (cfg: Draft) => void;
  }[] = [
    {
      name: "a school share sum other than 100",
      spoil: (cfg) => (cfg.policy.shares[2].percent = "49"),
      fix: (cfg) => (cfg.policy.shares[2].percent = "50"),
    },
    {
      name: "a rule share sum other than 100",
      spoil: (cfg) =>
        cfg.rules.push(
          rule([
            { categoryId: "fo", percent: "60" },
            { categoryId: "sor", percent: "30" },
          ]),
        ),
      fix: (cfg) => (cfg.rules[0].shares[1].percent = "40"),
    },
    {
      name: "an empty share",
      spoil: (cfg) => (cfg.policy.shares[0].percent = ""),
      fix: (cfg) => (cfg.policy.shares[0].percent = "25"),
    },
    {
      name: "a school share with three decimals",
      spoil: withShares("50.004", "49.996", "0"),
      fix: withShares("25", "25", "50"),
    },
    {
      name: "school shares of three decimals that sum to 100",
      spoil: withShares("33.333", "33.333", "33.336"),
      fix: withShares("25", "25", "50"),
    },
    {
      name: "a rule share with three decimals",
      spoil: (cfg) =>
        cfg.rules.push(
          rule([
            { categoryId: "fo", percent: "60.001" },
            { categoryId: "sor", percent: "39.999" },
          ]),
        ),
      fix: (cfg) => cfg.rules.pop(),
    },
    {
      name: "a share of an unknown category",
      spoil: (cfg) => (cfg.policy.shares[2].categoryId = "ghost"),
      fix: (cfg) => (cfg.policy.shares[2].categoryId = "soch"),
    },
    {
      name: "a repeated category id",
      spoil: (cfg) => {
        cfg.categories[1].id = "fo";
        cfg.policy.shares = [
          { categoryId: "fo", percent: "50" },
          { categoryId: "soch", percent: "50" },
        ];
      },
      fix: (cfg) => {
        cfg.categories[1].id = "sor";
        withShares("25", "25", "50")(cfg);
      },
    },
    {
      name: "a repeated category name",
      spoil: (cfg) => (cfg.categories[1].name = " фо"),
      fix: (cfg) => (cfg.categories[1].name = "СОР"),
    },
    {
      name: "an inactive default category",
      spoil: (cfg) => (cfg.categories[0].active = false),
      fix: (cfg) => (cfg.categories[0].active = true),
    },
    {
      name: "a calendar type shared by two active categories",
      spoil: (cfg) => (cfg.categories[2].calendar = "summative_unit"),
      fix: (cfg) => (cfg.categories[2].active = false),
    },
    {
      name: "a category without a name",
      spoil: (cfg) => (cfg.categories[1].name = "  "),
      fix: (cfg) => (cfg.categories[1].name = "СОР"),
    },
  ];
  it.each(cases)("refuses $name, and accepts it once fixed", (c) => {
    const cfg = defaults();
    c.spoil(cfg);
    expect(configProblem(cfg)).not.toBe("");
    c.fix(cfg);
    expect(configProblem(cfg)).toBe("");
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

describe("staleVersion", () => {
  it("reloads the editor only for a 409, so other failures keep the draft", () => {
    expect(staleVersion(Object.assign(new Error("x"), { status: 409 }))).toBe(
      true,
    );
    for (const status of [403, 422, 500, undefined])
      expect(staleVersion(Object.assign(new Error("x"), { status }))).toBe(
        false,
      );
    expect(staleVersion(new Error("Сервер недоступен."))).toBe(false);
    expect(staleVersion(null)).toBe(false);
  });
});

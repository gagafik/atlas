import { describe, expect, it } from "vitest";
import { parseMoney, formatMoney, moneyInput } from "./payments";
describe("financial decimal input and display", () => {
  it("preserves minor units without floating-point rounding", () => {
    expect(parseMoney("0,29", 2)).toBe(29);
    expect(parseMoney("10000000000.00", 2)).toBe(1000000000000);
    expect(parseMoney("12.345", 3)).toBe(12345);
    expect(parseMoney("0", 0)).toBe(0);
  });
  it("rejects ambiguous, rounded, negative and non-finite values", () => {
    for (const raw of [
      "1e3",
      "1 000",
      "1,234.50",
      "-1",
      "NaN",
      "Infinity",
      "0.001",
      "10000000000.01",
      "",
    ])
      expect(() => parseMoney(raw, 2)).toThrow();
    expect(() => parseMoney("1.0", 0)).toThrow();
  });
  it("formats very large totals exactly and preserves negative fractional credit", () => {
    expect(moneyInput("900719925474099199", 2)).toBe("9007199254740991.99");
    expect(formatMoney("-1", 2, "KZT")).toBe("−0,01 KZT");
    expect(formatMoney("900719925474099199", 2, "KZT").replace(/\s/g, "")).toBe(
      "9007199254740991,99KZT",
    );
  });
});

import { describe, it, expect } from "vitest";
import { sectionVisible } from "./schoolSections";

describe("school sections in the menu", () => {
  it("stays hidden until the setting has been loaded", () => {
    expect(sectionVisible(null, "canteen")).toBe(false);
  });
  it("hides the listed sections and shows the rest", () => {
    expect(sectionVisible(["canteen", "site"], "canteen")).toBe(false);
    expect(sectionVisible(["canteen", "site"], "library")).toBe(true);
    expect(sectionVisible([], "canteen")).toBe(true);
  });
});

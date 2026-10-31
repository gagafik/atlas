import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";

const folder = new URL("../src/", import.meta.url);

describe("print page rules", () => {
  it("are all named, so one feature cannot change the orientation of every printout", () => {
    const unnamed = readdirSync(folder)
      .filter((name) => name.endsWith(".css"))
      .filter((name) =>
        /@page\s*[{:]/.test(readFileSync(new URL(name, folder), "utf8")),
      );
    expect(unnamed).toEqual([]);
  });
});

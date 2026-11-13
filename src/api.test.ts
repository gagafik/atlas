import { describe, it, expect } from "vitest";
import { errorText } from "./api";
describe("errorText", () => {
  it("returns a plain message as it is", () => {
    expect(errorText("Текст")).toBe("Текст");
  });
  it("joins validation messages without the prefix", () => {
    expect(
      errorText([
        { msg: "Value error, Максимум баллов — число больше 0." },
        { msg: "Другое" },
      ]),
    ).toBe("Максимум баллов — число больше 0.\nДругое");
  });
  it("still says something when the detail carries no text", () => {
    expect(errorText(undefined)).not.toBe("");
    expect(errorText({ code: 1 })).not.toBe("");
  });
});

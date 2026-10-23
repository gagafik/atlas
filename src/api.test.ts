import { describe, it, expect } from "vitest";
import { errorText } from "./api";
describe("errorText", () => {
  it("returns plain messages and joins validation messages without the prefix", () => {
    expect(errorText("Текст")).toBe("Текст");
    expect(
      errorText([
        { msg: "Value error, Максимум баллов — число больше 0." },
        { msg: "Другое" },
      ]),
    ).toBe("Максимум баллов — число больше 0.\nДругое");
    expect(errorText(undefined)).toBe("Не удалось выполнить действие.");
    expect(errorText({ code: 1 })).toBe("Не удалось выполнить действие.");
  });
});

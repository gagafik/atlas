import { describe, it, expect } from "vitest";
import {
  currentChild,
  recallChoice,
  rememberChoice,
  type ChildChoice,
} from "./familyChild";

const children: ChildChoice[] = [
  { id: "s1", name: "Первый", className: "7А" },
  { id: "s2", name: "Второй", className: "7Б" },
];
const memory = () => {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
  };
};
// Storage that a private window or a blocked-cookies setting makes throw.
const blocked = () => {
  const fail = () => {
    throw new DOMException("blocked", "SecurityError");
  };
  return { getItem: fail, setItem: fail };
};

describe("the child the portal shows", () => {
  it("is the wanted one while the account has that child", () => {
    expect(currentChild(children, "s2")).toBe("s2");
  });

  it("falls back to the first child when nothing is wanted or the wanted child is no longer linked", () => {
    expect(currentChild(children)).toBe("s1");
    expect(currentChild(children, null)).toBe("s1");
    expect(currentChild(children, "s9")).toBe("s1");
  });

  it("is none for an account without children", () => {
    expect(currentChild([], "s1")).toBe("");
  });
});

describe("the remembered choice", () => {
  it("comes back for the same workspace and only for it", () => {
    const storage = memory();
    expect(recallChoice("school", storage)).toBeUndefined();
    rememberChoice("school", "s2", storage);
    expect(recallChoice("school", storage)).toBe("s2");
    expect(recallChoice("demo", storage)).toBeUndefined();
  });

  it("shows the first child again when the storage cannot be used", () => {
    expect(recallChoice("school", blocked())).toBeUndefined();
    expect(() => rememberChoice("school", "s2", blocked())).not.toThrow();
    expect(currentChild(children, recallChoice("school", blocked()))).toBe(
      "s1",
    );
  });

  it("never keeps an unlinked child on show, however it was remembered", () => {
    const storage = memory();
    rememberChoice("school", "gone", storage);
    expect(currentChild(children, recallChoice("school", storage))).toBe("s1");
  });
});

import { expect, it } from "vitest";
import { defaultChild, formLocks } from "./absenceForm";

const closed = { id: "s1", electronicNotes: false };
const open = { id: "s3", electronicNotes: true };

it("keeps the child picker and unsent file removal usable when the class is closed", () => {
  expect(formLocks(false, closed)).toEqual({
    pick: false,
    write: true,
    remove: false,
  });
  expect(formLocks(false, open)).toEqual({
    pick: false,
    write: false,
    remove: false,
  });
  // Servers older than the switch send no flag: notes are on.
  expect(formLocks(false, { id: "s2" }).write).toBe(false);
  expect(formLocks(false, undefined).write).toBe(false);
});

it("locks everything while a request is running", () => {
  expect(formLocks(true, open)).toEqual({
    pick: true,
    write: true,
    remove: true,
  });
});

it("starts on the first child whose class takes notices", () => {
  expect(defaultChild([closed, open], "")).toBe("s3");
  expect(defaultChild([open, closed], "")).toBe("s3");
  // Every class closed: still select someone, the form shows why it is locked.
  expect(defaultChild([closed], "")).toBe("s1");
  expect(defaultChild([], "")).toBe("");
});

it("never replaces a child the parent already chose", () => {
  expect(defaultChild([closed, open], "s1")).toBe("s1");
});

type Child = { id: string; electronicNotes?: boolean };

export const notesOff = (child?: Child) => child?.electronicNotes === false;

// A class with electronic notes off refuses only new notices: the parent still
// switches to a sibling in an open class and removes files not yet sent.
export const formLocks = (busy: boolean, child?: Child) => ({
  pick: busy,
  write: busy || notesOff(child),
  remove: busy,
});

// Keeps the chosen child; otherwise the first one whose class still takes notices.
export const defaultChild = (students: Child[], current: string) =>
  current || (students.find((s) => !notesOff(s)) ?? students[0])?.id || "";

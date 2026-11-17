import { describe, expect, it } from "vitest";
import {
  addPupils,
  buildQuery,
  emptyFilters,
  dayLabel,
  formDirty,
  freshForm,
  noteActions,
  noteChips,
  PICKER_CAP,
  pickerPupils,
  PUPIL_LIMIT,
  togglePupil,
  type Note,
  type NoteForm,
  type Options,
  type Student,
} from "./behaviourNotesModel";

const pupil = (id: string, name: string, classId: string): Student => ({
  id,
  name,
  classId,
  className: classId.toUpperCase(),
});
const pupils = [
  pupil("s1", "Примеров Данияр", "c7a"),
  pupil("s2", "Примерова Алина", "c7a"),
  pupil("s3", "Вымыслов Артём", "c8b"),
];
const many = (count: number) =>
  Array.from({ length: count }, (_, i) => pupil(`p${i}`, `Ученик ${i}`, "c7a"));
const note = (over: Partial<Note> = {}): Note => ({
  id: "n1",
  version: 1,
  status: "published",
  kind: "remark",
  day: "2026-11-16",
  categoryId: "c1",
  category: "Опоздание",
  text: "",
  author: "Примерова Алина",
  level: "teacher",
  levelTitle: "Учитель",
  studentId: "s1",
  student: "Примеров Данияр",
  requireAck: false,
  acknowledged: false,
  acknowledgedAt: null,
  acknowledgedBy: null,
  letter: false,
  letterNumber: "",
  lesson: "",
  ...over,
});
const keys = (n: Note, staff: boolean) =>
  noteChips(n, staff).map((chip) => chip.key);

describe("pickerPupils", () => {
  it("keeps the pupils of the chosen class", () => {
    const { shown, hint } = pickerPupils(pupils, "c8b", "", PUPIL_LIMIT);
    expect(shown.map((s) => s.id)).toEqual(["s3"]);
    expect(hint).toBe(false);
  });

  it("finds a part of a name in any case", () => {
    expect(
      pickerPupils(pupils, "", "ДАНИЯР", PUPIL_LIMIT).shown.map((s) => s.id),
    ).toEqual(["s1"]);
    expect(
      pickerPupils(pupils, "", "  ова ", PUPIL_LIMIT).shown.map((s) => s.id),
    ).toEqual(["s2"]);
  });

  it("does not tell «е» from «ё» in a search", () => {
    expect(
      pickerPupils(pupils, "", "артем", PUPIL_LIMIT).shown.map((s) => s.id),
    ).toEqual(["s3"]);
    expect(
      pickerPupils(pupils, "", "АРТЁМ", PUPIL_LIMIT).shown.map((s) => s.id),
    ).toEqual(["s3"]);
  });

  it("cuts a long match and says how long it was", () => {
    const all = many(PICKER_CAP + 25);
    const { shown, total, hint } = pickerPupils(all, "", "у", PUPIL_LIMIT);
    expect(hint).toBe(false);
    expect(shown).toHaveLength(PICKER_CAP);
    expect(total).toBe(all.length);
    const short = pickerPupils(pupils, "", "ов", PUPIL_LIMIT);
    expect(short.shown).toHaveLength(short.total);
  });

  it("narrows by class and search together", () => {
    expect(pickerPupils(pupils, "c8b", "данияр", PUPIL_LIMIT).shown).toEqual(
      [],
    );
  });

  it("asks to choose a class when nothing is chosen and there are too many pupils", () => {
    const all = many(PUPIL_LIMIT + 1);
    const { shown, hint } = pickerPupils(all, "", "", PUPIL_LIMIT);
    expect(shown).toEqual([]);
    expect(hint).toBe(true);
    expect(pickerPupils(all, "c7a", "", PUPIL_LIMIT).shown).toHaveLength(
      PUPIL_LIMIT + 1,
    );
    expect(pickerPupils(all, "", "Ученик 7", PUPIL_LIMIT).hint).toBe(false);
  });

  it("shows everybody when they fit the limit", () => {
    const all = many(PUPIL_LIMIT);
    const { shown, hint } = pickerPupils(all, "", "", PUPIL_LIMIT);
    expect(shown).toHaveLength(PUPIL_LIMIT);
    expect(hint).toBe(false);
  });
});

describe("the limit of pupils", () => {
  it("takes a pupil off and puts one on", () => {
    expect(togglePupil(["a", "b"], "a", 3)).toEqual(["b"]);
    expect(togglePupil(["a", "b"], "c", 3)).toEqual(["a", "b", "c"]);
  });

  it("does not put a pupil over the limit", () => {
    expect(togglePupil(["a", "b"], "c", 2)).toEqual(["a", "b"]);
    expect(togglePupil(["a", "b"], "a", 2)).toEqual(["b"]);
  });

  it("adds several pupils in order and without repeats", () => {
    expect(addPupils(["a", "b"], ["b", "c"], 5)).toEqual(["a", "b", "c"]);
  });

  it("refuses several pupils that would not fit", () => {
    expect(addPupils(["a", "b"], ["c", "d"], 3)).toBeNull();
    expect(addPupils(["a", "b"], ["b", "c"], 3)).toEqual(["a", "b", "c"]);
  });
});

describe("noteChips", () => {
  it("gives the family no status, hidden or printed marks", () => {
    const n = note({
      status: "pending",
      visible: false,
      printed: true,
      letterNumber: "12",
    });
    expect(keys(n, false)).toEqual(["letter"]);
    expect(keys(n, true)).toEqual(["pending", "hidden", "printed", "letter"]);
  });

  it("shows no status mark for a record in force", () => {
    expect(keys(note(), true)).toEqual([]);
  });

  it("carries the reason of a withdrawn record and of a rejected one", () => {
    const withdrawn = noteChips(
      note({ status: "withdrawn", withdrawReason: "Ошибка в дате" }),
      true,
    );
    expect(withdrawn).toEqual([{ key: "withdrawn", title: "Ошибка в дате" }]);
    const rejected = noteChips(
      note({ status: "rejected", decision: "Нет оснований" }),
      true,
    );
    expect(rejected).toEqual([{ key: "rejected", title: "Нет оснований" }]);
  });

  it("names the one who approved", () => {
    const chips = noteChips(note({ approvedBy: "Примерова Алина" }), false);
    expect(chips).toEqual([{ key: "approved", title: "Примерова Алина" }]);
  });

  it("asks for acknowledgement only of published records that need it", () => {
    expect(keys(note({ requireAck: true }), false)).toEqual(["ackWait"]);
    expect(
      keys(
        note({
          requireAck: true,
          acknowledged: true,
          acknowledgedAt: "2026-11-17T09:30:00",
          acknowledgedBy: "Примеров Данияр",
        }),
        false,
      ),
    ).toEqual(["ackDone"]);
    expect(keys(note({ requireAck: true, status: "pending" }), false)).toEqual(
      [],
    );
    expect(keys(note({ requireAck: false }), false)).toEqual([]);
  });
});

describe("noteActions", () => {
  const family = { staff: false, canAcknowledge: true };
  const staff = { staff: true, canAcknowledge: false };

  it("gives the family no edit, withdraw or history", () => {
    const can = noteActions(note({ canChange: true }), family);
    expect(can.edit).toBeNull();
    expect(can.withdraw).toBe(false);
    expect(can.history).toBe(false);
  });

  it("gives staff edit, withdraw and history of a record they may change", () => {
    const can = noteActions(note({ canChange: true }), staff);
    expect(can.edit).toBe("on");
    expect(can.withdraw).toBe(true);
    expect(can.history).toBe(true);
    expect(noteActions(note(), staff).edit).toBeNull();
  });

  it("turns the edit off once the family acknowledged", () => {
    const can = noteActions(
      note({ canChange: true, acknowledged: true }),
      staff,
    );
    expect(can.edit).toBe("off");
  });

  it("offers a letter only of a published record", () => {
    expect(noteActions(note({ letter: true }), staff).letter).toBe(true);
    expect(
      noteActions(note({ letter: true, status: "pending" }), staff).letter,
    ).toBe(false);
    expect(noteActions(note({ status: "published" }), staff).letter).toBe(
      false,
    );
  });

  it("lets only the one who may decide decide", () => {
    expect(noteActions(note({ canDecide: true }), staff).decide).toBe(true);
    expect(noteActions(note(), staff).decide).toBe(false);
  });

  it("offers acknowledgement to the family of a record that needs it and has none yet", () => {
    expect(noteActions(note({ requireAck: true }), family).acknowledge).toBe(
      true,
    );
    expect(
      noteActions(note({ requireAck: true, acknowledged: true }), family)
        .acknowledge,
    ).toBe(false);
    expect(noteActions(note(), family).acknowledge).toBe(false);
    expect(
      noteActions(note({ requireAck: true }), {
        staff: false,
        canAcknowledge: false,
      }).acknowledge,
    ).toBe(false);
  });
});

const form = (over: Partial<NoteForm> = {}): NoteForm => ({
  categoryId: "c1",
  day: "2026-11-16",
  text: "",
  classId: "",
  visible: true,
  printed: false,
  students: [],
  ...over,
});

describe("formDirty", () => {
  it("is clean when there is neither text nor pupils", () => {
    expect(formDirty(form())).toBe(false);
    expect(formDirty(form({ text: "   " }))).toBe(false);
  });

  it("is dirty with a text or a pupil", () => {
    expect(formDirty(form({ text: "Опоздал" }))).toBe(true);
    expect(formDirty(form({ students: ["s1"] }))).toBe(true);
  });

  it("is dirty when a choice differs from the one the form was opened with", () => {
    const base = form();
    expect(formDirty(form(), base)).toBe(false);
    expect(formDirty(form({ visible: false }), base)).toBe(true);
    expect(formDirty(form({ printed: true }), base)).toBe(true);
    expect(formDirty(form({ day: "2026-11-02" }), base)).toBe(true);
    expect(formDirty(form({ categoryId: "c2" }), base)).toBe(true);
    // Without a base only the text and the pupils count.
    expect(formDirty(form({ visible: false }))).toBe(false);
  });
});

describe("freshForm", () => {
  const options = {
    today: "2026-11-17",
    myLevels: ["teacher"],
    categories: [
      { id: "c0", level: "director", active: true, report: true },
      { id: "c1", level: "teacher", active: false, report: true },
      { id: "c2", level: "teacher", active: true, report: true },
    ],
  } as unknown as Options;

  it("gives back the defaults of the account, whatever the last attempt left", () => {
    const left = form({
      categoryId: "c9",
      day: "2026-10-01",
      visible: false,
      printed: false,
      text: "Опоздал",
      classId: "c7a",
      students: ["s1"],
    });
    expect(freshForm(left, options)).toEqual({
      categoryId: "c2",
      day: "2026-11-17",
      visible: true,
      printed: true,
      text: "",
      classId: "",
      students: [],
    });
  });

  it("is not dirty once it is the form the dialog opened with", () => {
    const fresh = freshForm(form({ text: "x", visible: false }), options);
    expect(formDirty(fresh, fresh)).toBe(false);
  });
});

describe("dayLabel", () => {
  it("writes the day as day, month, year", () => {
    expect(dayLabel("2026-11-05")).toBe("05.11.2026");
  });
});

describe("buildQuery", () => {
  it("leaves out every filter that is not set, so a default request has no parameters", () => {
    expect(buildQuery(emptyFilters)).toBe("");
    expect(buildQuery(emptyFilters, { before: 7 })).toBe("?before=7");
  });
  it("carries the subject and the search only when they are set", () => {
    const params = new URLSearchParams(
      buildQuery({ ...emptyFilters, subjectId: "none", q: "ёлка и шар" }),
    );
    expect(params.get("subjectId")).toBe("none");
    expect(params.get("q")).toBe("ёлка и шар");
    expect(buildQuery({ ...emptyFilters, kind: "remark" })).not.toMatch(
      /subjectId|q=/,
    );
  });
  it("limits the filters to the keys a request understands", () => {
    const filters = { ...emptyFilters, classId: "c1", q: "слово" };
    expect(buildQuery(filters, {}, ["classId", "start"])).toBe("?classId=c1");
    expect(buildQuery(filters, { before: 3 }, [])).toBe("?before=3");
  });
});

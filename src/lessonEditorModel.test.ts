import { describe, expect, it } from "vitest";
import {
  ATTENDANCE_CHOICES,
  absenceOptions,
  attendanceCounts,
  editorState,
  lessonLine,
  needsDetails,
  pupilRows,
  showClasses,
  summaryParts,
} from "./lessonEditorModel";
import type { JournalEntry, PupilRecord } from "./journal";

const record = (
  studentId: string,
  attendance: string,
  rest: Partial<PupilRecord> = {},
): PupilRecord => ({
  studentId,
  attendance,
  lateMinutes: 0,
  comment: "",
  grades: [],
  ...rest,
});
const entryOf = (
  records: PupilRecord[],
  over: Partial<JournalEntry> = {},
): JournalEntry =>
  ({
    lessonKey: "lesson",
    version: 0,
    writable: true,
    hasPublished: false,
    publishedVersion: null,
    absenceCodes: [
      { id: "sick", name: "Болезнь", active: true },
      { id: "old", name: "Прежний тип", active: false },
    ],
    document: {
      lesson: {
        date: "2026-11-16",
        start: "08:30",
        end: "09:15",
        teachers: "Учитель",
        rooms: "301",
        students: [
          { id: "a", name: "Первый", className: "7А", enrolled: true },
          { id: "b", name: "Второй", className: "7А", enrolled: true },
          { id: "c", name: "Ученик", className: "7Б", enrolled: false },
        ],
      },
      content: { records, assessments: [] },
    },
    ...over,
  }) as unknown as JournalEntry;

describe("attendance choices", () => {
  it("offers the four marks a teacher sets, none of the old ones", () => {
    expect(ATTENDANCE_CHOICES.map((c) => c.status)).toEqual([
      "present",
      "absent",
      "late",
      "left",
    ]);
  });
});

describe("absenceOptions", () => {
  const codes = [
    { id: "sick", name: "Болезнь", active: true },
    { id: "old", name: "Прежний тип", active: false },
  ];
  it("offers only active types to a record that holds none of the retired", () => {
    expect(absenceOptions(codes, undefined).map((c) => c.id)).toEqual(["sick"]);
    expect(absenceOptions(codes, "sick").map((c) => c.id)).toEqual(["sick"]);
  });
  it("keeps a retired type only for the record that holds it", () => {
    expect(absenceOptions(codes, "old").map((c) => c.id)).toEqual([
      "sick",
      "old",
    ]);
  });
  it("keeps a type that is no longer in the list at all", () => {
    const options = absenceOptions(codes, "gone");
    expect(options.at(-1)).toMatchObject({ id: "gone", active: false });
    expect(absenceOptions(undefined, "gone")).toHaveLength(1);
    expect(absenceOptions(undefined, undefined)).toEqual([]);
  });
});

describe("pupilRows", () => {
  const entry = entryOf(
    [
      record("a", "absent", { absenceCode: "old" }),
      record("b", "late", { lateMinutes: 7 }),
      record("c", "excused"),
    ],
    { attendanceApprovals: { a: { id: "note", version: 1 } } },
  );
  const rows = pupilRows(entry);
  it("gives one numbered row per pupil of the lesson, former pupils included", () => {
    expect(rows.map((r) => r.number)).toEqual([1, 2, 3]);
    expect(rows.map((r) => r.student.id)).toEqual(["a", "b", "c"]);
  });
  it("tells which marks carry fields of their own and which are old", () => {
    expect(rows.map((r) => r.extra)).toEqual(["absent", "late", null]);
    expect(rows.map((r) => r.legacy)).toEqual([null, null, "excused"]);
    expect(pupilRows(entryOf([record("a", "unmarked")]))[0].legacy).toBe(
      "unmarked",
    );
    expect(pupilRows(entryOf([record("a", "present")]))[0]).toMatchObject({
      legacy: null,
      extra: null,
    });
  });
  it("marks the pupils covered by an accepted note", () => {
    expect(rows.map((r) => r.approved)).toEqual([true, false, false]);
  });
  it("offers the retired absence type to the record that holds it only", () => {
    expect(rows[0].codes.map((c) => c.id)).toContain("old");
    expect(rows[1].codes.map((c) => c.id)).not.toContain("old");
  });
  it("counts the filled comments of the pupil and of the grades", () => {
    const counted = pupilRows(
      entryOf([
        record("a", "present", {
          comment: "Хорошо",
          grades: [
            { assessmentId: "x", score: 5, label: "", comment: "Верно" },
            { assessmentId: "y", score: 4, label: "", comment: "" },
          ],
        }),
        record("b", "present"),
      ]),
    );
    expect(counted[0].comments).toBe(2);
    expect(counted[1].comments).toBe(0);
  });
  it("still gives a row to a pupil without a record", () => {
    const [first] = pupilRows(entryOf([]));
    expect(first.record.attendance).toBe("unmarked");
  });
});

describe("showClasses and needsDetails", () => {
  it("shows the classes only when the lesson mixes them", () => {
    expect(showClasses([{ className: "7А" }, { className: "7А" }])).toBe(false);
    expect(showClasses([{ className: "7А" }, { className: "7Б" }])).toBe(true);
    expect(showClasses([])).toBe(false);
  });
  it("asks for the details column only while some row has details", () => {
    const present = ["a", "b", "c"].map((id) => record(id, "present"));
    const withFirst = (first: PupilRecord) => [first, ...present.slice(1)];
    expect(needsDetails(pupilRows(entryOf(present)))).toBe(false);
    expect(
      needsDetails(pupilRows(entryOf(withFirst(record("a", "left"))))),
    ).toBe(true);
    expect(
      needsDetails(pupilRows(entryOf(withFirst(record("a", "unmarked"))))),
    ).toBe(true);
    expect(
      needsDetails(
        pupilRows(
          entryOf(present, {
            attendanceApprovals: { a: { id: "note", version: 1 } },
          }),
        ),
      ),
    ).toBe(true);
  });
});

describe("attendanceCounts and summaryParts", () => {
  const entry = entryOf([
    record("a", "absent"),
    record("b", "late"),
    record("c", "absent"),
  ]);
  const { students } = entry.document.lesson;
  const counts = attendanceCounts(entry.document.content, students);
  it("does not count pupils who left the group", () => {
    expect(counts.absent).toBe(1);
    expect(counts.late).toBe(1);
  });
  it("counts every status, also those nobody has", () => {
    expect(counts.present).toBe(0);
    expect(counts.left).toBe(0);
  });
  it("drops the zero parts and the present ones", () => {
    const parts = summaryParts({ ...counts, present: 9 });
    expect(parts.map(([, n]) => n)).toEqual([1, 1]);
    expect(summaryParts(attendanceCounts({ records: [] }, students))).toEqual(
      [],
    );
  });
});

describe("lessonLine", () => {
  const lesson = {
    date: "2026-11-16",
    start: "08:30",
    end: "09:15",
    teachers: "Учитель",
    rooms: "301",
  };
  it("writes the date as day.month.year", () => {
    expect(lessonLine(lesson)).toContain("16.11.2026");
    expect(lessonLine(lesson)).not.toContain("2026-11-16");
  });
  it("leaves out an empty room", () => {
    const withRoom = lessonLine(lesson).split(" · ");
    const without = lessonLine({ ...lesson, rooms: "" }).split(" · ");
    expect(without).toHaveLength(withRoom.length - 1);
    expect(without.every(Boolean)).toBe(true);
  });
});

describe("editorState", () => {
  const base = {
    writable: true,
    version: 3,
    hasPublished: true,
    publishedVersion: 3,
  };
  it("puts unsaved fields before everything else", () => {
    expect(editorState({ ...base, writable: false }, true).kind).toBe(
      "unsaved",
    );
  });
  it("tells a read-only record from an editable one", () => {
    expect(editorState({ ...base, writable: false }, false).kind).toBe(
      "readonly",
    );
  });
  it("tells a new record from a saved one", () => {
    expect(
      editorState(
        { ...base, version: 0, hasPublished: false, publishedVersion: null },
        false,
      ).kind,
    ).toBe("new");
  });
  it("tells the revision the family sees from a newer draft", () => {
    expect(editorState(base, false).kind).toBe("published");
    expect(editorState({ ...base, version: 4 }, false).kind).toBe("draft");
    expect(
      editorState(
        { ...base, hasPublished: false, publishedVersion: null },
        false,
      ).kind,
    ).toBe("draft");
  });
  it("names the revision in the text of a saved record", () => {
    expect(editorState(base, false).text).toContain("3");
  });
});

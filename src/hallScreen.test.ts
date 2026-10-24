import { describe as suite, expect, it } from "vitest";
import {
  KIND_LABEL,
  dayHeading,
  describe,
  pageSize,
  paginate,
  schoolClock,
  stale,
  tokenFromHash,
  type HallItem,
} from "./hallScreen";

const base: HallItem = {
  kind: "",
  period: "1",
  start: "08:20",
  end: "09:00",
  classes: "10A",
  subject: "Английский язык",
};
const item = (extra: Partial<HallItem>): HallItem => ({ ...base, ...extra });

suite("change descriptions", () => {
  it("names the substitution, or the missing teacher, or the extra one", () => {
    const sub = item({
      kind: "substitution",
      absent: "Иванов А.",
      teachers: "Петров Б.",
    });
    expect(describe(sub)).toBe("Замена: Иванов А. → Петров Б.");
    expect(describe(item({ kind: "substitution" }))).toBe("Замена учителя");
    expect(
      describe(
        item({ kind: "substitution", absent: "", teachers: "Петров Б." }),
      ),
    ).toBe("Дополнительный учитель: Петров Б.");
    expect(
      describe(
        item({ kind: "substitution", absent: "Иванов А.", teachers: "" }),
      ),
    ).toBe("Иванов А. не ведёт урок");
    expect(
      describe(item({ kind: "substitution", absent: "", teachers: "" })),
    ).toBe("Замена учителя");
  });
  it("describes every other kind", () => {
    expect(describe(item({ kind: "cancelled" }))).toBe("Урок отменён");
    expect(describe(item({ kind: "closed" }))).toBe("Уроки отменены");
    expect(
      describe(item({ kind: "room", previousRooms: "301", rooms: "303" })),
    ).toBe("Кабинет 301 → 303");
    expect(
      describe(item({ kind: "room", previousRooms: "", rooms: "303" })),
    ).toBe("Кабинет 303");
    expect(describe(item({ kind: "subject", previousSubject: "Физика" }))).toBe(
      "Вместо «Физика»",
    );
    expect(describe(item({ kind: "time", start: "09:10", end: "09:50" }))).toBe(
      "Новое время 09:10–09:50",
    );
    expect(describe(item({ kind: "moved_out", movedTo: "2026-11-03" }))).toBe(
      "Перенесён на 03.11",
    );
    expect(describe(item({ kind: "moved_in", movedFrom: "2026-11-02" }))).toBe(
      "Перенесён с 02.11",
    );
    expect(describe(item({ kind: "added" }))).toBe("Дополнительный урок");
  });
  it("adds the new room when another change moved the lesson", () => {
    const sub = item({
      kind: "substitution",
      absent: "А",
      teachers: "Б",
      previousRooms: "301",
      rooms: "303",
    });
    expect(describe(sub)).toBe("Замена: А → Б · каб. 303");
    expect(
      describe(item({ kind: "room", previousRooms: "301", rooms: "303" })),
    ).not.toContain("каб.");
  });
  it("labels every kind with text, not colour alone", () => {
    for (const kind of [
      "substitution",
      "cancelled",
      "closed",
      "room",
      "subject",
      "time",
      "moved_out",
      "moved_in",
      "added",
    ])
      expect(KIND_LABEL[kind]).toBeTruthy();
  });
  it("heads the days", () => {
    const day = (label: string, date: string) => ({
      label,
      date,
      items: [],
      more: 0,
    });
    expect(dayHeading(day("Сегодня", "2026-11-02"), true)).toBe(
      "Изменения сегодня",
    );
    expect(dayHeading(day("Завтра", "2026-11-03"), false)).toBe(
      "Завтра, 03.11",
    );
    expect(dayHeading(day("Понедельник", "2026-11-09"), false)).toBe(
      "Понедельник, 09.11",
    );
  });
});

suite("paging and clock", () => {
  it("splits items into pages", () => {
    const pages = paginate([1, 2, 3, 4, 5, 6, 7], 3);
    expect(pages).toEqual([[1, 2, 3], [4, 5, 6], [7]]);
    expect(paginate([], 3)).toEqual([]);
  });
  it("fits ten rows on a 1080 p wall and never fewer than three", () => {
    expect(pageSize(1080)).toBe(10);
    expect(pageSize(400)).toBe(3);
    expect(pageSize(2160, 46 / 40)).toBeGreaterThan(pageSize(1080));
  });
  it("shows the school clock from a server instant, without Intl zones", () => {
    const clock = schoolClock(Date.parse("2026-11-01T19:30:00Z"), 300);
    expect(clock.time).toBe("00:30");
    expect(clock.date).toMatch(/^понедельник, 2 ноября/);
    expect(schoolClock(Date.parse("2026-12-31T18:59:00Z"), 300).date).toBe(
      "четверг, 31 декабря",
    );
  });
  it("calls the data stale after 150 seconds", () => {
    expect(stale(0, 150001)).toBe(true);
    expect(stale(0, 150000)).toBe(false);
  });
});

suite("link token", () => {
  const token = "Abc_def-0123456789Abc_def-0123456789Abc_def";
  it("reads only a well-formed fragment", () => {
    expect(token).toHaveLength(43);
    expect(tokenFromHash("#" + token)).toBe(token);
    expect(tokenFromHash(token)).toBe(token);
    expect(tokenFromHash("#abc")).toBe("");
    expect(tokenFromHash("")).toBe("");
    expect(tokenFromHash("#" + "a".repeat(101))).toBe("");
    expect(tokenFromHash("#" + token + "%20")).toBe("");
    expect(tokenFromHash("#%E0%A4%A")).toBe("");
  });
});

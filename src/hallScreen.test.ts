import { describe as suite, expect, it } from "vitest";
import {
  KIND_LABEL,
  dayHeading,
  describe,
  fitParts,
  pageSize,
  paginate,
  pollInterval,
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
  it("tells where a one-off lesson is held, since it has no earlier room", () => {
    expect(describe(item({ kind: "added", rooms: "302" }))).toBe(
      "Дополнительный урок · каб. 302",
    );
    expect(describe(item({ kind: "added", rooms: "" }))).toBe(
      "Дополнительный урок",
    );
    expect(describe(item({ kind: "added" }))).toBe("Дополнительный урок");
    expect(
      describe(
        item({ kind: "moved_in", movedFrom: "2026-11-02", rooms: "302" }),
      ),
    ).toBe("Перенесён с 02.11");
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

suite("long announcements", () => {
  const words = Array.from({ length: 40 }, (_, i) => `слово${i}`).join(" ");
  const upTo = (n: number) => (part: string) => part.length <= n;
  it("keeps a text that fits in one piece", () => {
    expect(fitParts("Короткий текст", upTo(100))).toEqual(["Короткий текст"]);
    expect(fitParts("", upTo(100))).toEqual([""]);
  });
  it("splits a long text into parts that all fit and lose nothing", () => {
    const parts = fitParts(words, upTo(60));
    expect(parts.length).toBeGreaterThan(3);
    for (const part of parts) {
      expect(part.length).toBeLessThanOrEqual(60);
      expect(part).toBe(part.trim());
    }
    expect(parts.join(" ")).toBe(words);
  });
  it("fills each part before starting the next", () => {
    const parts = fitParts(words, upTo(60));
    parts.slice(0, -1).forEach((part, i) => {
      const next = parts[i + 1].split(" ")[0];
      expect(part.length + 1 + next.length).toBeGreaterThan(60);
    });
  });
  it("keeps line breaks inside a part", () => {
    const text = "Актовый зал," + String.fromCharCode(10) + "вход свободный";
    expect(fitParts(text, upTo(100))).toEqual([text]);
  });
  it("always moves on, even when nothing fits", () => {
    expect(fitParts("раз два три", () => false)).toEqual(["раз", "два", "три"]);
    const long = "я".repeat(100);
    const parts = fitParts(long, () => false);
    expect(parts.join("")).toBe(long);
    expect(parts.every((part) => part.length <= 40)).toBe(true);
  });
});

suite("polling", () => {
  it("asks once a minute, and rarely after the link has gone", () => {
    expect(pollInterval(false)).toBe(60000);
    // With the polling jitter of 10 %, a dead link adds at most two unknown tokens in the ten minutes the server counts.
    expect(
      Math.floor(600000 / (pollInterval(true) * 0.9)) + 1,
    ).toBeLessThanOrEqual(2);
  });
});

import { describe as suite, expect, it } from "vitest";
import {
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
    const names = { absent: "Иванов А.", teachers: "Петров Б." };
    const both = describe(item({ kind: "substitution", ...names }));
    const extra = describe(
      item({ kind: "substitution", ...names, absent: "" }),
    );
    const missing = describe(
      item({ kind: "substitution", ...names, teachers: "" }),
    );
    const nobody = describe(
      item({ kind: "substitution", absent: "", teachers: "" }),
    );
    expect(both).toContain("Иванов А.");
    expect(both).toContain("Петров Б.");
    expect(extra).toContain("Петров Б.");
    expect(extra).not.toContain("Иванов");
    expect(missing).toContain("Иванов А.");
    expect(missing).not.toContain("Петров");
    // A screen that hides the teachers sends no names at all.
    expect(describe(item({ kind: "substitution" }))).toBe(nobody);
    expect(new Set([both, extra, missing, nobody]).size).toBe(4);
  });
  it("describes every other kind", () => {
    const dates = { movedTo: "2026-11-03", movedFrom: "2026-11-02" };
    const room = describe(
      item({ kind: "room", previousRooms: "301", rooms: "303" }),
    );
    const newRoom = describe(
      item({ kind: "room", previousRooms: "", rooms: "303" }),
    );
    const subject = describe(
      item({ kind: "subject", previousSubject: "Физика" }),
    );
    const time = describe(item({ kind: "time", start: "09:10", end: "09:50" }));
    const out = describe(item({ kind: "moved_out", ...dates }));
    const into = describe(item({ kind: "moved_in", ...dates }));
    expect(room).toContain("301");
    expect(room).toContain("303");
    expect(newRoom).toContain("303");
    expect(subject).toContain("Физика");
    expect(time).toContain("09:10");
    expect(time).toContain("09:50");
    // The day shown is the one the lesson went to, or came from.
    expect(out).toContain("03.11");
    expect(out).not.toContain("02.11");
    expect(into).toContain("02.11");
    expect(into).not.toContain("03.11");
    const all = [
      describe(item({ kind: "cancelled" })),
      describe(item({ kind: "closed" })),
      room,
      subject,
      time,
      out,
      into,
      describe(item({ kind: "added" })),
    ];
    expect(new Set(all).size).toBe(all.length);
  });
  it("adds the new room when another change moved the lesson", () => {
    const sub = describe(
      item({
        kind: "substitution",
        absent: "А",
        teachers: "Б",
        previousRooms: "301",
        rooms: "303",
      }),
    );
    expect(sub).toContain("303");
    // A plain room change already names the new room, so it must not repeat it.
    const room = describe(
      item({ kind: "room", previousRooms: "301", rooms: "303" }),
    );
    expect(room.match(/303/g)).toHaveLength(1);
  });
  it("tells where a one-off lesson is held, since it has no earlier room", () => {
    expect(describe(item({ kind: "added", rooms: "302" }))).toContain("302");
    expect(describe(item({ kind: "added", rooms: "" }))).toBe(
      describe(item({ kind: "added" })),
    );
    expect(
      describe(
        item({ kind: "moved_in", movedFrom: "2026-11-02", rooms: "302" }),
      ),
    ).not.toContain("302");
  });
});

suite("paging and clock", () => {
  it("splits items into pages", () => {
    const pages = paginate([1, 2, 3, 4, 5, 6, 7], 3);
    expect(pages).toEqual([[1, 2, 3], [4, 5, 6], [7]]);
    expect(paginate([], 3)).toEqual([]);
  });
  it("fits ten rows on a 1080 p wall and never fewer than three", () => {
    expect(pageSize(1080)).toBeGreaterThanOrEqual(8);
    expect(pageSize(1080)).toBeLessThanOrEqual(12);
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

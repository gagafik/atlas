import { describe, expect, it } from "vitest";
import {
  countLabel,
  emptyState,
  filterLinks,
  formatDay,
  linkChips,
  linkCounts,
  linkFormDirty,
  linkKey,
  savedLinkOutcome,
  termText,
  type FamilyLink,
} from "./familyLinksModel";

const link = (over: Partial<FamilyLink> = {}): FamilyLink => ({
  parentId: "p1",
  studentId: "s1",
  parentName: "Анна Примерова",
  studentName: "Максим Вымыслов",
  version: 1,
  accountVersion: 1,
  relationship: "mother",
  verified: true,
  evidence: "",
  reason: "",
  startDate: null,
  endDate: null,
  status: "active",
  activity: "active",
  legacy: false,
  accountActive: true,
  ...over,
});
const good = link();
const unverified = link({ parentId: "p2", verified: false });
const legacy = link({ parentId: "p3", legacy: true });
const revoked = link({
  parentId: "p4",
  activity: "revoked",
  status: "revoked",
});
const switchedOff = link({ parentId: "p5", accountActive: false });
const rows = [good, unverified, legacy, revoked, switchedOff];
const keys = (list: FamilyLink[]) => list.map(linkKey);

describe("filterLinks", () => {
  it("shows every row for «all»", () => {
    expect(filterLinks(rows, "all")).toEqual(rows);
  });
  it("keeps the links that are not verified or are old assignments for «unclear»", () => {
    expect(keys(filterLinks(rows, "unclear"))).toEqual(
      keys([unverified, legacy]),
    );
  });
  it("keeps the links whose access is not in force or whose account is off for «inactive»", () => {
    expect(keys(filterLinks(rows, "inactive"))).toEqual(
      keys([revoked, switchedOff]),
    );
  });
  it("counts what each filter would show", () => {
    const counts = linkCounts(rows);
    for (const f of ["all", "unclear", "inactive"] as const)
      expect(counts[f]).toBe(filterLinks(rows, f).length);
  });
  it("counts nothing in an empty registry", () => {
    expect(linkCounts([])).toEqual({ all: 0, unclear: 0, inactive: 0 });
  });
  it("treats a link that is both unclear and inactive as both", () => {
    const both = link({ verified: false, activity: "expired" });
    expect(linkCounts([both])).toEqual({ all: 1, unclear: 1, inactive: 1 });
  });
});

describe("linkChips", () => {
  it("tells a verified link from one that needs clarifying", () => {
    expect(linkChips(good).check.text).not.toBe(
      linkChips(unverified).check.text,
    );
    expect(linkChips(unverified).check.text).toBe(linkChips(legacy).check.text);
  });
  it("gives each kind of access its own words", () => {
    const words = ["active", "scheduled", "expired", "revoked"].map(
      (activity) => linkChips(link({ activity })).access.text,
    );
    expect(new Set(words).size).toBe(4);
  });
  it("calls the access that is in force fine and a revoked one a problem", () => {
    expect(linkChips(good).access.tone).toBe("ok");
    expect(linkChips(revoked).access.tone).not.toBe("ok");
  });
  it("adds the chip of the account only when the account is off", () => {
    expect(linkChips(good).account).toBeNull();
    expect(linkChips(switchedOff).account?.text).toBeTruthy();
    expect(linkChips(switchedOff).account?.title).toBeTruthy();
  });
  it("does not fail on an activity the page does not know", () => {
    const chip = linkChips(link({ activity: "paused" })).access;
    expect(chip.text).toBeTruthy();
    expect(chip.tone).not.toBe("ok");
  });
});

describe("termText", () => {
  it("writes the days as day.month.year", () => {
    expect(formatDay("2026-09-01")).toBe("01.09.2026");
    const { text } = termText({
      startDate: "2026-09-01",
      endDate: "2027-05-31",
    });
    expect(text).toContain("01.09.2026");
    expect(text).toContain("31.05.2027");
  });
  it("puts a dash for a term nobody set", () => {
    const { text, title } = termText({ startDate: null, endDate: null });
    expect(text).toBe("—");
    expect(title).toBeTruthy();
  });
  it("keeps the side that is set when the other is not", () => {
    const open = termText({ startDate: "2026-09-01", endDate: null });
    expect(open.text).toContain("01.09.2026");
    expect(open.text).not.toBe("—");
    const closed = termText({ startDate: null, endDate: "2027-05-31" });
    expect(closed.text).toContain("31.05.2027");
  });
  it("leaves a value that is not a date as it came", () => {
    expect(formatDay("скоро")).toBe("скоро");
  });
  it("says in the title which side is open", () => {
    const a = termText({ startDate: "2026-09-01", endDate: null }).title;
    const b = termText({ startDate: null, endDate: "2026-09-01" }).title;
    expect(a).not.toBe(b);
  });
});

describe("linkFormDirty", () => {
  it("is not dirty before anything is typed", () => {
    expect(linkFormDirty(good, good)).toBe(false);
    expect(linkFormDirty({ ...good }, good)).toBe(false);
  });
  it("is not dirty when there is no form", () => {
    expect(linkFormDirty(null, good)).toBe(false);
    expect(linkFormDirty(good, null)).toBe(false);
  });
  it("sees a change of any field of the link", () => {
    const changes: Partial<FamilyLink>[] = [
      { relationship: "father" },
      { verified: false },
      { evidence: "справка" },
      { reason: "по заявлению" },
      { startDate: "2026-09-01" },
      { endDate: "2027-05-31" },
      { status: "revoked" },
    ];
    for (const change of changes)
      expect(linkFormDirty({ ...good, ...change }, good)).toBe(true);
  });
  it("does not take an empty text for a different one than a missing text", () => {
    const base = link({ evidence: undefined, reason: undefined });
    expect(linkFormDirty({ ...base, evidence: "", reason: "" }, base)).toBe(
      false,
    );
  });
  it("is clean again when the typed value is taken back", () => {
    const typed = { ...good, reason: "по заявлению" };
    expect(linkFormDirty(typed, good)).toBe(true);
    expect(linkFormDirty({ ...typed, reason: "" }, good)).toBe(false);
  });
  it("does not look at the fields the page does not edit", () => {
    expect(
      linkFormDirty({ ...good, version: 7, activity: "expired" }, good),
    ).toBe(false);
  });
});

describe("savedLinkOutcome", () => {
  it("finds the link among the rows that are shown", () => {
    expect(savedLinkOutcome(rows, "all", linkKey(unverified))).toBe("visible");
  });
  it("says that the filter hides a link that is loaded", () => {
    expect(savedLinkOutcome(rows, "inactive", linkKey(good))).toBe("filtered");
  });
  it("says that a link that was not loaded is absent, whatever the filter", () => {
    const other = link({ parentId: "p9" });
    expect(savedLinkOutcome(rows, "all", linkKey(other))).toBe("absent");
    expect(savedLinkOutcome(rows, "inactive", linkKey(other))).toBe("absent");
    expect(savedLinkOutcome([], "all", linkKey(good))).toBe("absent");
  });
  it("tells two links of one parent apart", () => {
    const sibling = link({ studentId: "s2" });
    expect(savedLinkOutcome([good], "all", linkKey(sibling))).toBe("absent");
    expect(savedLinkOutcome([good, sibling], "all", linkKey(sibling))).toBe(
      "visible",
    );
  });
});

describe("countLabel", () => {
  it("is the plain number when every link is loaded", () => {
    expect(countLabel(3, false)).toBe("3");
    expect(countLabel(0, false)).toBe("0");
  });
  it("is not the plain number while the server has more pages", () => {
    expect(countLabel(0, true)).not.toBe(countLabel(0, false));
    expect(countLabel(12, true)).toContain("12");
    expect(countLabel(12, true)).not.toBe(countLabel(12, false));
  });
});

describe("emptyState", () => {
  it("has nothing to say while rows are shown, whatever the status", () => {
    for (const status of ["loading", "ready", "failed"] as const)
      expect(emptyState(status, 4, 2, true)).toBeNull();
  });
  it("tells a load on the way and a failed load from an empty answer", () => {
    expect(emptyState("loading", 0, 0, false)).toBe("loading");
    expect(emptyState("failed", 0, 0, false)).toBe("failed");
    expect(emptyState("ready", 0, 0, false)).toBe("empty");
  });
  it("does not call the registry empty while a new answer is on its way", () => {
    expect(emptyState("loading", 5, 0, false)).toBe("loading");
  });
  it("tells links hidden by the filter from a registry without links", () => {
    expect(emptyState("ready", 5, 0, false)).not.toBe(
      emptyState("ready", 0, 0, false),
    );
  });
  it("says the answer is not final when the filter hides everything and more pages exist", () => {
    expect(emptyState("ready", 5, 0, true)).not.toBe(
      emptyState("ready", 5, 0, false),
    );
  });
});

import { describe, expect, it } from "vitest";
import { editableApplication, mutationId, recoveryCode } from "./admissions";
import type { Application } from "./admissions";
describe("admissions client boundaries", () => {
  it("retains mutation identity after a network retry but not an edited request", () => {
    const first = mutationId(null, { version: 2, childName: "One" });
    expect(mutationId(first, { version: 2, childName: "One" }).id).toBe(
      first.id,
    );
    expect(mutationId(first, { version: 2, childName: "Two" }).id).not.toBe(
      first.id,
    );
  });
  it("keeps submitted and archived applications read only", () => {
    const sample = (status: string, campaign = "open") =>
      ({ status, campaign: { status: campaign } }) as Application;
    expect(editableApplication(sample("draft"))).toBe(true);
    expect(editableApplication(sample("returned", "closed"))).toBe(true);
    expect(editableApplication(sample("submitted"))).toBe(false);
    expect(editableApplication(sample("accepted"))).toBe(false);
    expect(editableApplication(sample("returned", "archived"))).toBe(false);
  });
  it("creates separate high entropy recovery codes", () => {
    const first = recoveryCode();
    expect(first).toMatch(/^[a-f0-9]{48}$/);
    expect(recoveryCode()).not.toBe(first);
  });
});

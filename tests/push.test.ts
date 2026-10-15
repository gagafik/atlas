import { describe, expect, it } from "vitest";
import { applicationKey, endpointFingerprint } from "../src/push";

describe("private push subscription helpers", () => {
  it("sends a stable SHA-256 fingerprint for logout, not the capability URL", async () => {
    expect(await endpointFingerprint("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(await endpointFingerprint("https://fcm.googleapis.com/a")).not.toBe(
      await endpointFingerprint("https://fcm.googleapis.com/b"),
    );
  });
  it("decodes an unpadded URL-safe VAPID key as bytes", () => {
    expect([...applicationKey("-_8AAQ")]).toEqual([251, 255, 0, 1]);
  });
});

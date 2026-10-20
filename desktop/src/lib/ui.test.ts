import { describe, expect, it } from "vitest";

import { durationMs, fmtDuration, fmtTokens, shortSha } from "./ui";

describe("inspection formatting", () => {
  it("never shows a missing token count as zero", () => {
    expect(fmtTokens(null)).toBe("not reported");
    expect(fmtTokens(undefined)).toBe("not reported");
    expect(fmtTokens(0)).toBe("0");
    expect(fmtTokens(12_345)).toBe("12.3k");
  });

  it("measures a running attempt up to now and a finished one to its end", () => {
    const now = Date.parse("2026-10-20T10:00:30Z");
    expect(durationMs("2026-10-20T10:00:00Z", null, now)).toBe(30_000);
    expect(durationMs("2026-10-20T10:00:00Z", "2026-10-20T10:00:05Z", now)).toBe(5_000);
    expect(durationMs("not a time", null, now)).toBe(0);
  });

  it("formats durations and commits compactly", () => {
    expect([fmtDuration(400), fmtDuration(4_000), fmtDuration(42_000), fmtDuration(125_000), fmtDuration(3_720_000)]).toEqual(["0.4s", "4.0s", "42s", "2m 5s", "1h 2m"]);
    expect(shortSha("0123456789abcdef")).toBe("01234567");
    expect(shortSha(null)).toBe("");
  });
});

describe("latest run", () => {
  it("follows the bridge's run order, not object key order", async () => {
    const { latestRun } = await import("./types");
    const snap = (run_id: string) => ({ run_id }) as never;
    // Keys arrive sorted (the Rust shell's JSON maps are ordered by key), so the newer run sorts first here.
    const view = { runs: { "run-20e9": snap("run-20e9"), "run-b941": snap("run-b941") }, run_order: ["run-b941", "run-20e9"] };
    expect(latestRun(view as never)?.run_id).toBe("run-20e9");
    expect(latestRun({ runs: {}, run_order: [] } as never)).toBeUndefined();
    expect(latestRun(null)).toBeUndefined();
  });
});

describe("session preferences", () => {
  it("fills in what an older session never stored", async () => {
    const { currentPreferences } = await import("../components/SessionControls");
    expect(currentPreferences({})).toEqual({ approvals: "ask", model: null, model_mode: "prefer" });
    expect(currentPreferences({ approvals: "bypass" }).approvals).toBe("bypass");
  });
});

describe("tour card placement", () => {
  it("sits beside the spotlight and never leaves the window", async () => {
    const { cardPosition } = await import("../components/Tour");
    Object.defineProperty(window, "innerWidth", { value: 1320, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 860, configurable: true });
    expect(cardPosition({ x: 10, y: 100, w: 240, h: 40 }, 200)).toEqual({ left: 266, top: 100 });
    const right = cardPosition({ x: 1000, y: 100, w: 320, h: 600 }, 200);
    expect(right.left).toBe(1000 - 16 - 340);
    const low = cardPosition({ x: 10, y: 800, w: 240, h: 40 }, 200);
    expect(low.top + 200).toBeLessThanOrEqual(860 - 16);
    expect(cardPosition(null, 200)).toEqual({ left: (1320 - 340) / 2, top: 330 });
  });
});

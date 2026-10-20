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

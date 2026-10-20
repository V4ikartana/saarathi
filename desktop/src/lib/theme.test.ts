import { beforeEach, describe, expect, it } from "vitest";

import { applyTheme, loadDefaultPreferences, loadTheme, saveDefaultPreferences } from "./theme";

describe("per-viewer settings", () => {
  beforeEach(() => localStorage.clear());

  it("defaults new sessions to asking before every action", () => {
    expect(loadDefaultPreferences()).toEqual({ approvals: "ask", model: null, model_mode: "prefer" });
    localStorage.setItem("saarathi.defaultPreferences", "{not json");
    expect(loadDefaultPreferences().approvals).toBe("ask");
  });

  it("round-trips defaults and forced themes", () => {
    saveDefaultPreferences({ approvals: "auto_safe", model: "codex-cli:default", model_mode: "pin" });
    expect(loadDefaultPreferences()).toEqual({ approvals: "auto_safe", model: "codex-cli:default", model_mode: "pin" });
    applyTheme("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(loadTheme()).toBe("light");
    applyTheme("system");
    expect([...document.documentElement.classList]).not.toContain("light");
    expect([...document.documentElement.classList]).toContain("system");
    expect(loadTheme()).toBe("system");
  });
});

describe("appearance", () => {
  beforeEach(() => localStorage.clear());

  it("starts from sensible defaults and ignores tampered values", async () => {
    const { loadAppearance, DEFAULT_APPEARANCE } = await import("./theme");
    expect(loadAppearance()).toEqual(DEFAULT_APPEARANCE);
    localStorage.setItem("saarathi.appearance.v2", JSON.stringify({ accent: "neon", scale: 7, motion: "wild", theme: "dark" }));
    expect(loadAppearance()).toEqual({ ...DEFAULT_APPEARANCE, theme: "dark" });
  });

  it("applies the accent for the active theme and the motion preference", async () => {
    const { applyAppearance, ACCENTS, DEFAULT_APPEARANCE, loadAppearance } = await import("./theme");
    applyAppearance({ ...DEFAULT_APPEARANCE, theme: "light", accent: "blue", motion: "reduced" });
    const root = document.documentElement;
    expect(root.style.getPropertyValue("--accent")).toBe(ACCENTS.blue.light[0]);
    expect(root.dataset.motion).toBe("reduced");
    expect(loadAppearance().accent).toBe("blue");
  });
});

describe("reset times", () => {
  it("never invents a reset time", async () => {
    const { resetsIn } = await import("./ui");
    const now = Date.parse("2026-10-20T10:00:00Z");
    expect(resetsIn(null, now)).toBe("");
    expect(resetsIn("garbage", now)).toBe("");
    expect(resetsIn("2026-10-20T10:30:00Z", now)).toBe("in 30 min");
    expect(resetsIn("2026-10-20T14:15:00Z", now)).toBe("in 4 h 15 min");
    expect(resetsIn("2026-10-26T10:00:00Z", now)).toBe("in 6 days");
  });
});

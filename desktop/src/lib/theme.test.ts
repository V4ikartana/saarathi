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
    expect(loadTheme()).toBe("system");
  });
});

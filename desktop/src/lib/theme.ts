// Appearance and per-viewer defaults. Conveniences only: losing them is harmless, so storage errors are ignored.
import type { SessionPreferences } from "./types";

export type Theme = "system" | "light" | "dark";

const THEME_KEY = "saarathi.theme";
const DEFAULTS_KEY = "saarathi.defaultPreferences";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Not persisted; the choice still applies for this window.
  }
}

export function loadTheme(): Theme {
  const t = read(THEME_KEY);
  return t === "light" || t === "dark" ? t : "system";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.classList.toggle("light", theme === "light");
  write(THEME_KEY, theme);
}

export function loadDefaultPreferences(): SessionPreferences {
  try {
    const raw = JSON.parse(read(DEFAULTS_KEY) ?? "{}") as Partial<SessionPreferences>;
    return { approvals: raw.approvals ?? "ask", model: raw.model ?? null, model_mode: raw.model_mode ?? "prefer" };
  } catch {
    return { approvals: "ask", model: null, model_mode: "prefer" };
  }
}

export function saveDefaultPreferences(prefs: SessionPreferences): void {
  write(DEFAULTS_KEY, JSON.stringify(prefs));
}

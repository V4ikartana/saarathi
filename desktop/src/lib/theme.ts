// Appearance and per-viewer defaults. Conveniences only: losing them is harmless, so storage errors are ignored.
import type { SessionPreferences } from "./types";

export type Theme = "system" | "light" | "dark";
export type Accent = "amber" | "blue" | "green" | "violet" | "pink" | "graphite";
export type Motion = "system" | "full" | "reduced";
export type Scale = 0.9 | 1 | 1.1 | 1.2;

export interface Appearance {
  theme: Theme;
  accent: Accent;
  scale: Scale;
  motion: Motion;
  showExecution: boolean;
}

export const DEFAULT_APPEARANCE: Appearance = {
  theme: "system",
  accent: "amber",
  scale: 1,
  motion: "system",
  showExecution: true,
};

// Each accent has a dark-theme and a light-theme value, both with text that keeps 4.5:1 contrast on it.
export const ACCENTS: Record<Accent, { label: string; dark: [string, string]; light: [string, string] }> = {
  amber: { label: "Amber", dark: ["#f0b44c", "#1c1405"], light: ["#b5650d", "#ffffff"] },
  blue: { label: "Blue", dark: ["#5aa5ff", "#04162e"], light: ["#0066cc", "#ffffff"] },
  green: { label: "Green", dark: ["#46d391", "#03180d"], light: ["#1a7f4b", "#ffffff"] },
  violet: { label: "Violet", dark: ["#b39bff", "#140a33"], light: ["#6d4ad6", "#ffffff"] },
  pink: { label: "Pink", dark: ["#f57ab8", "#2b0716"], light: ["#c2185b", "#ffffff"] },
  graphite: { label: "Graphite", dark: ["#d2d6dd", "#111316"], light: ["#3a3a3c", "#ffffff"] },
};

const THEME_KEY = "saarathi.theme";
const APPEARANCE_KEY = "saarathi.appearance";
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
  return loadAppearance().theme;
}

export function loadAppearance(): Appearance {
  try {
    const raw = JSON.parse(read(APPEARANCE_KEY) ?? "{}") as Partial<Appearance>;
    const legacy = read(THEME_KEY);
    const theme = raw.theme ?? (legacy === "light" || legacy === "dark" ? legacy : "system");
    return {
      theme,
      accent: raw.accent && raw.accent in ACCENTS ? raw.accent : DEFAULT_APPEARANCE.accent,
      scale: ([0.9, 1, 1.1, 1.2] as const).find((s) => s === raw.scale) ?? DEFAULT_APPEARANCE.scale,
      motion: raw.motion === "full" || raw.motion === "reduced" ? raw.motion : "system",
      showExecution: raw.showExecution ?? true,
    };
  } catch {
    return DEFAULT_APPEARANCE;
  }
}

export function isDarkTheme(theme: Theme): boolean {
  if (theme !== "system") return theme === "dark";
  return typeof window.matchMedia === "function" ? !window.matchMedia("(prefers-color-scheme: light)").matches : true;
}

export function prefersReducedMotion(motion: Motion): boolean {
  if (motion !== "system") return motion === "reduced";
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Apply everything that lives outside React: the theme class, accent colours, motion flag and page zoom. */
export function applyAppearance(a: Appearance): void {
  const root = document.documentElement;
  root.classList.toggle("dark", a.theme === "dark");
  root.classList.toggle("light", a.theme === "light");
  const [accent, ink] = ACCENTS[a.accent][isDarkTheme(a.theme) ? "dark" : "light"];
  root.style.setProperty("--accent", accent);
  root.style.setProperty("--accent-ink", ink);
  root.dataset.motion = prefersReducedMotion(a.motion) ? "reduced" : "full";
  write(APPEARANCE_KEY, JSON.stringify(a));
  write(THEME_KEY, a.theme);
  void import("@tauri-apps/api/webview")
    .then(({ getCurrentWebview }) => getCurrentWebview().setZoom(a.scale))
    .catch(() => undefined); // outside Tauri (tests, plain browser) there is no webview to zoom
}

/** Kept for callers that only change the theme. */
export function applyTheme(theme: Theme): void {
  applyAppearance({ ...loadAppearance(), theme });
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

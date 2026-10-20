// Monaco, bundled locally (the CSP allows no network). Only the core editor and the syntax-colouring grammars are
// loaded: diffs need no language services, and the editor worker computes the diff off the main thread.
import "monaco-editor/basic-languages/monaco.contribution";
import * as monaco from "monaco-editor/editor/editor.api";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";

declare global {
  interface Window {
    MonacoEnvironment?: { getWorker: (workerId: string, label: string) => Worker };
  }
}

window.MonacoEnvironment = { getWorker: () => new EditorWorker() };

function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return /^#[0-9a-f]{6}$/i.test(value) ? value : fallback;
}

/** Whether the app is currently drawn dark, judged from its own background token (not a media query, which a
 * window theme can override). */
function isDark(): boolean {
  const hex = token("--color-bg", "#0e1013").slice(1);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5;
}

/** Re-read the app's colour tokens so the editor matches the current light or dark theme. */
export function applyTheme(): string {
  const dark = isDark();
  const name = dark ? "saarathi-dark" : "saarathi-light";
  monaco.editor.defineTheme(name, {
    base: dark ? "vs-dark" : "vs",
    inherit: true,
    rules: [],
    colors: {
      "editor.background": token("--color-bg", dark ? "#0e1013" : "#f7f7f5"),
      "editorGutter.background": token("--color-bg", dark ? "#0e1013" : "#f7f7f5"),
      "editor.lineHighlightBackground": "#00000000",
      "editorLineNumber.foreground": token("--color-faint", "#5d6573"),
      "diffEditor.insertedTextBackground": dark ? "#4fb87a26" : "#4fb87a33",
      "diffEditor.removedTextBackground": dark ? "#e0645c26" : "#e0645c33",
      "diffEditor.insertedLineBackground": dark ? "#4fb87a14" : "#4fb87a1f",
      "diffEditor.removedLineBackground": dark ? "#e0645c14" : "#e0645c1f",
    },
  });
  return name;
}

export function languageFor(path: string): string {
  const lower = path.toLowerCase();
  const base = lower.split("/").pop() ?? lower;
  for (const lang of monaco.languages.getLanguages()) {
    if (lang.filenames?.some((f) => f.toLowerCase() === base)) return lang.id;
    if (lang.extensions?.some((ext) => lower.endsWith(ext.toLowerCase()))) return lang.id;
  }
  return "plaintext";
}

export { monaco };

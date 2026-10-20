// Side-by-side (or inline) diff of one file a task changed. Read-only: the UI never edits task branches.
import { useEffect, useRef } from "react";

import { applyTheme, languageFor, monaco } from "../lib/monaco";
import type { FileVersions } from "../lib/types";

export default function DiffView({ file, sideBySide }: { file: FileVersions; sideBySide: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<monaco.editor.IStandaloneDiffEditor | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const diff = monaco.editor.createDiffEditor(host.current, {
      readOnly: true,
      originalEditable: false,
      automaticLayout: true,
      renderSideBySide: sideBySide,
      useInlineViewWhenSpaceIsLimited: true,
      renderSideBySideInlineBreakpoint: 520,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      renderOverviewRuler: false,
      fontSize: 12,
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--font-mono") || "monospace",
      hideUnchangedRegions: { enabled: true, contextLineCount: 4, minimumLineCount: 6 },
      theme: applyTheme(),
    });
    editor.current = diff;
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    const retheme = () => monaco.editor.setTheme(applyTheme());
    scheme.addEventListener("change", retheme);
    return () => {
      scheme.removeEventListener("change", retheme);
      const model = diff.getModel();
      diff.dispose();
      model?.original.dispose();
      model?.modified.dispose();
      editor.current = null;
    };
  }, []);

  useEffect(() => {
    editor.current?.updateOptions({ renderSideBySide: sideBySide });
  }, [sideBySide]);

  useEffect(() => {
    const diff = editor.current;
    if (!diff) return;
    const language = languageFor(file.path);
    const previous = diff.getModel();
    diff.setModel({
      original: monaco.editor.createModel(file.original, language),
      modified: monaco.editor.createModel(file.modified, language),
    });
    previous?.original.dispose();
    previous?.modified.dispose();
  }, [file]);

  return <div ref={host} className="h-full min-h-0 w-full" />;
}

// A read-only terminal for logs Saarathi recorded (already redacted by the engine). No input is ever sent anywhere.
import "@xterm/xterm/css/xterm.css";

import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { useEffect, useRef } from "react";

function palette() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    background: v("--color-bg", "#0e1013"),
    foreground: v("--color-ink", "#e7e9ee"),
    cursor: v("--color-bg", "#0e1013"),
    selectionBackground: "#e3a94a55",
    green: v("--color-ok", "#4fb87a"),
    red: v("--color-err", "#e0645c"),
    yellow: v("--color-warn", "#d9a441"),
    brightBlack: v("--color-faint", "#5d6573"),
  };
}

export default function LogView({ text }: { text: string }) {
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const fit = useRef<FitAddon | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const t = new Terminal({
      disableStdin: true,
      convertEol: true,
      cursorBlink: false,
      cursorInactiveStyle: "none",
      scrollback: 20_000,
      fontSize: 12,
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--font-mono") || "monospace",
      theme: palette(),
    });
    const f = new FitAddon();
    t.loadAddon(f);
    t.open(host.current);
    term.current = t;
    fit.current = f;
    const resize = new ResizeObserver(() => {
      try {
        f.fit();
      } catch {
        // Hidden or zero-sized: the next resize fits it.
      }
    });
    resize.observe(host.current);
    return () => {
      resize.disconnect();
      t.dispose();
      term.current = null;
    };
  }, []);

  useEffect(() => {
    const t = term.current;
    if (!t) return;
    t.reset();
    t.write(text.length > 0 ? text : "\x1b[90m(empty)\x1b[0m");
    t.scrollToBottom();
  }, [text]);

  return <div ref={host} className="h-full min-h-0 w-full overflow-hidden px-2 py-1" />;
}

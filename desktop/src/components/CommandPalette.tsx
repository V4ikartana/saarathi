// ⌘K: jump to a session or page, or run an action, without leaving the keyboard.
import { BarChart3, CornerDownLeft, MessageSquare, MessageSquarePlus, Moon, PanelRight, Search, Settings, ShieldCheck, Sun } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { cn } from "../lib/ui";
import { useApp } from "../store/app";
import { EXIT, SPRING } from "./primitives";

interface Command {
  id: string;
  label: string;
  group: string;
  icon: ReactNode;
  hint?: string;
  run: () => void;
}

export function CommandPalette() {
  const { paletteOpen, setPaletteOpen, sessions, selectSession, setPage, createSession, appearance, setAppearance } = useApp();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const commands = useMemo<Command[]>(() => {
    const close = (fn: () => void) => () => {
      setPaletteOpen(false);
      fn();
    };
    return [
      { id: "new", label: "New session", group: "Actions", icon: <MessageSquarePlus className="size-4" />, hint: "⌘N", run: close(() => void createSession()) },
      { id: "theme", label: appearance.theme === "dark" ? "Switch to light theme" : "Switch to dark theme", group: "Actions",
        icon: appearance.theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />,
        run: close(() => setAppearance({ theme: appearance.theme === "dark" ? "light" : "dark" })) },
      { id: "panel", label: appearance.showExecution ? "Hide the execution panel" : "Show the execution panel", group: "Actions",
        icon: <PanelRight className="size-4" />, run: close(() => setAppearance({ showExecution: !appearance.showExecution })) },
      { id: "usage", label: "Usage and plan limits", group: "Go to", icon: <BarChart3 className="size-4" />, run: close(() => setPage("usage")) },
      { id: "permissions", label: "Permissions", group: "Go to", icon: <ShieldCheck className="size-4" />, run: close(() => setPage("permissions")) },
      { id: "settings", label: "Settings", group: "Go to", icon: <Settings className="size-4" />, hint: "⌘,", run: close(() => setPage("settings")) },
      ...sessions.map((s) => ({
        id: `session:${s.id}`, label: s.title, group: "Sessions", icon: <MessageSquare className="size-4" />,
        hint: s.last_state ?? undefined, run: close(() => void selectSession(s.id)),
      })),
    ];
  }, [sessions, appearance, setPaletteOpen, createSession, setAppearance, setPage, selectSession]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? commands.filter((c) => c.label.toLowerCase().includes(q) || c.group.toLowerCase().includes(q)) : commands;
  }, [commands, query]);

  useEffect(() => {
    if (paletteOpen) {
      setQuery("");
      setActive(0);
      window.setTimeout(() => input.current?.focus(), 0);
    }
  }, [paletteOpen]);
  useEffect(() => setActive(0), [query]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(results.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      results[active]?.run();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setPaletteOpen(false);
    }
  };

  let lastGroup = "";
  return (
    <AnimatePresence>
      {paletteOpen && (
        <motion.div key="palette" className="fixed inset-0 z-[55] flex items-start justify-center bg-black/55 px-4 pt-[14vh] backdrop-blur-[2px]"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: EXIT }} onMouseDown={() => setPaletteOpen(false)}>
          <motion.div role="dialog" aria-modal="true" aria-label="Command palette" onMouseDown={(e) => e.stopPropagation()}
            initial={{ opacity: 0, y: -12, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -8, scale: 0.98, transition: EXIT }}
            transition={SPRING} className="w-full max-w-xl overflow-hidden rounded-2xl border border-line-strong bg-panel/95 elev-3 backdrop-blur-xl">
            <div className="flex items-center gap-3 border-b border-line px-4">
              <Search className="size-4 text-faint" aria-hidden />
              <input ref={input} value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKey}
                placeholder="Search sessions, pages and actions" aria-label="Search commands" role="combobox"
                aria-expanded="true" aria-controls="palette-results" aria-activedescendant={results[active] ? `cmd-${results[active].id}` : undefined}
                className="h-12 w-full bg-transparent text-[15px] outline-none placeholder:text-faint focus-visible:outline-none" />
              <kbd className="rounded border border-line px-1.5 font-mono text-[10px] text-faint">esc</kbd>
            </div>
            <ul id="palette-results" role="listbox" className="max-h-[50vh] overflow-y-auto p-2">
              {results.length === 0 && <li className="px-3 py-6 text-center text-[13px] text-faint">Nothing matches “{query}”.</li>}
              {results.map((c, i) => {
                const header = c.group !== lastGroup ? c.group : null;
                lastGroup = c.group;
                return (
                  <li key={c.id} role="presentation">
                    {header && <div className="px-3 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-faint">{header}</div>}
                    <div id={`cmd-${c.id}`} role="option" aria-selected={i === active} onMouseMove={() => setActive(i)} onClick={c.run}
                      className={cn("relative flex items-center gap-3 rounded-lg px-3 py-2 text-[14px]", i === active ? "text-ink" : "text-muted")}>
                      {i === active && <motion.span layoutId="palette-active" transition={SPRING} className="absolute inset-0 rounded-lg bg-raised" />}
                      <span className="relative text-faint">{c.icon}</span>
                      <span className="relative min-w-0 flex-1 truncate">{c.label}</span>
                      {c.hint && <span className="relative text-[11px] text-faint">{c.hint}</span>}
                      {i === active && <CornerDownLeft className="relative size-3.5 text-faint" aria-hidden />}
                    </div>
                  </li>
                );
              })}
            </ul>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

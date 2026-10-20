import { Archive, BarChart3, FolderGit2, MessageSquarePlus, Pencil, Search, Settings, ShieldCheck } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";

import { cn } from "../lib/ui";
import { type Page, useApp } from "../store/app";

const PAGES: { id: Page; label: string; icon: typeof Settings }[] = [
  { id: "usage", label: "Usage", icon: BarChart3 },
  { id: "permissions", label: "Permissions", icon: ShieldCheck },
  { id: "settings", label: "Settings", icon: Settings },
];
import { Button, Mark, SOFT, SPRING, StatePill, Switch } from "./primitives";

export function Sidebar() {
  const { project, sessions, activeSessionId, search, showArchived, closeProject, createSession, selectSession,
    setSearch, toggleArchived, renameSession, archiveSession, page, setPage } = useApp();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  return (
    <aside className="glass flex w-64 shrink-0 flex-col border-r border-line/70">
      <div className="flex items-center gap-2.5 px-3.5 pt-3.5 pb-2.5">
        <Mark size={28} />
        <div className="min-w-0">
          <div className="text-[13px] font-semibold leading-tight">Saarathi</div>
          <div className="truncate text-[11px] text-faint">Many agents. One direction.</div>
        </div>
      </div>

      <button type="button" onClick={closeProject} title={project?.root}
        className="mx-2 flex items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-raised">
        <FolderGit2 className="size-4 text-muted" />
        <span className="truncate text-[13px]">{project?.name}</span>
      </button>

      <div className="px-2 pt-1">
        <Button tone="primary" className="w-full justify-center" onClick={() => void createSession()}>
          <MessageSquarePlus className="size-4" /> New session <span className="ml-1 text-[11px] opacity-70">⌘N</span>
        </Button>
        <label className="mt-2 flex h-8 items-center gap-2 rounded-lg border border-line bg-bg/60 px-2 focus-within:border-accent/60">
          <Search className="size-3.5 text-faint" />
          <input value={search} onChange={(e) => void setSearch(e.target.value)} placeholder="Search sessions"
            className="w-full bg-transparent text-[13px] outline-none placeholder:text-faint" />
        </label>
      </div>

      <nav className="mt-2 flex-1 overflow-y-auto px-2" aria-label="Sessions">
        {sessions.length === 0 && <p className="px-2 py-3 text-[12px] text-faint">No sessions yet.</p>}
        <AnimatePresence initial={false}>
        {sessions.map((s) => (
          <motion.div key={s.id} layout initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, height: 0 }} transition={SOFT}
            className="group relative flex items-center gap-1 rounded-lg px-2 py-1.5 hover:bg-raised/50">
            {s.id === activeSessionId && page === "session" && (
              <motion.span layoutId="sidebar-active" transition={SPRING} className="absolute inset-0 rounded-lg bg-raised ring-1 ring-line/60" />
            )}
            {editing === s.id ? (
              <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
                onBlur={() => setEditing(null)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && draft.trim()) { void renameSession(s.id, draft); setEditing(null); }
                  if (e.key === "Escape") setEditing(null);
                }}
                className="w-full rounded bg-bg px-1 text-[13px] outline-none ring-1 ring-accent/50" />
            ) : (
              <button type="button" onClick={() => void selectSession(s.id)} className="relative min-w-0 flex-1 text-left">
                <div className={cn("truncate text-[13px]", s.status === "archived" && "text-faint")}>{s.title}</div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  <StatePill state={s.last_state} />
                  {s.run_count > 1 && <span className="text-[11px] text-faint">{s.run_count} runs</span>}
                </div>
              </button>
            )}
            <div className="relative hidden gap-0.5 group-hover:flex">
              <button type="button" title="Rename" className="rounded p-1 text-faint hover:text-ink"
                onClick={() => { setEditing(s.id); setDraft(s.title); }}><Pencil className="size-3.5" /></button>
              {s.status === "active" && (
                <button type="button" title="Archive" className="rounded p-1 text-faint hover:text-ink"
                  onClick={() => void archiveSession(s.id)}><Archive className="size-3.5" /></button>
              )}
            </div>
          </motion.div>
        ))}
        </AnimatePresence>
      </nav>

      <nav className="border-t border-line px-2 py-1.5" aria-label="Project">
        {PAGES.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" onClick={() => setPage(id)} aria-current={page === id ? "page" : undefined}
            className={cn("relative flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-[13px]",
              page === id ? "text-ink" : "text-muted hover:bg-raised/50 hover:text-ink")}>
            {page === id && (
              <motion.span layoutId="sidebar-active" transition={SPRING} className="absolute inset-0 rounded-lg bg-raised ring-1 ring-line/60" />
            )}
            <Icon className="relative size-4" /><span className="relative">{label}</span>
          </button>
        ))}
      </nav>
      <div className="border-t border-line px-3 py-2">
        <label className="flex items-center justify-between gap-2 text-[12px] text-muted">
          Show archived
          <Switch label="Show archived sessions" checked={showArchived} onChange={() => void toggleArchived()} />
        </label>
      </div>
    </aside>
  );
}

import { Archive, FolderGit2, MessageSquarePlus, Pencil, Search } from "lucide-react";
import { useState } from "react";

import { cn } from "../lib/ui";
import { useApp } from "../store/app";
import { Button, Kbd, Mark, StatePill } from "./primitives";

export function Sidebar() {
  const { project, sessions, activeSessionId, search, showArchived, closeProject, createSession, selectSession,
    setSearch, toggleArchived, renameSession, archiveSession } = useApp();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-panel">
      <div className="flex items-center gap-2 px-3 pt-3 pb-2">
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
          <MessageSquarePlus className="size-4" /> New session <Kbd>⌘N</Kbd>
        </Button>
        <label className="mt-2 flex items-center gap-2 rounded-md border border-line px-2 h-8">
          <Search className="size-3.5 text-faint" />
          <input value={search} onChange={(e) => void setSearch(e.target.value)} placeholder="Search sessions"
            className="w-full bg-transparent text-[13px] outline-none placeholder:text-faint" />
        </label>
      </div>

      <nav className="mt-2 flex-1 overflow-y-auto px-2" aria-label="Sessions">
        {sessions.length === 0 && <p className="px-2 py-3 text-[12px] text-faint">No sessions yet.</p>}
        {sessions.map((s) => (
          <div key={s.id} className={cn("group flex items-center gap-1 rounded-md px-2 py-1.5",
            s.id === activeSessionId ? "bg-raised" : "hover:bg-raised/60")}>
            {editing === s.id ? (
              <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
                onBlur={() => setEditing(null)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && draft.trim()) { void renameSession(s.id, draft); setEditing(null); }
                  if (e.key === "Escape") setEditing(null);
                }}
                className="w-full rounded bg-bg px-1 text-[13px] outline-none ring-1 ring-accent/50" />
            ) : (
              <button type="button" onClick={() => void selectSession(s.id)} className="min-w-0 flex-1 text-left">
                <div className={cn("truncate text-[13px]", s.status === "archived" && "text-faint")}>{s.title}</div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  <StatePill state={s.last_state} />
                  {s.run_count > 1 && <span className="text-[11px] text-faint">{s.run_count} runs</span>}
                </div>
              </button>
            )}
            <div className="hidden gap-0.5 group-hover:flex">
              <button type="button" title="Rename" className="rounded p-1 text-faint hover:text-ink"
                onClick={() => { setEditing(s.id); setDraft(s.title); }}><Pencil className="size-3.5" /></button>
              {s.status === "active" && (
                <button type="button" title="Archive" className="rounded p-1 text-faint hover:text-ink"
                  onClick={() => void archiveSession(s.id)}><Archive className="size-3.5" /></button>
              )}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-line px-3 py-2">
        <label className="flex items-center gap-2 text-[12px] text-muted">
          <input type="checkbox" checked={showArchived} onChange={() => void toggleArchived()} />
          Show archived
        </label>
      </div>
    </aside>
  );
}

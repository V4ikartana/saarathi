import { X } from "lucide-react";
import { useEffect } from "react";

import { Composer, RunBar } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { ProjectPicker } from "./components/ProjectPicker";
import { RunTimeline } from "./components/RunTimeline";
import { Sidebar } from "./components/Sidebar";
import { TaskInspector } from "./components/TaskInspector";
import { onBridgeStatus, onEvents } from "./lib/bridge";
import { cn } from "./lib/ui";
import { useApp } from "./store/app";

function NoticeBar() {
  const { notice, dismissNotice } = useApp();
  if (!notice) return null;
  return (
    <div role="alert" className="flex items-start gap-3 border-b border-err/30 bg-err/10 px-4 py-2 text-[13px]">
      <div className="min-w-0 flex-1">
        <span className="text-err">{notice.message}</span>
        {notice.hint && <span className="ml-2 text-muted">{notice.hint}</span>}
      </div>
      <button type="button" aria-label="Dismiss" onClick={dismissNotice} className="text-muted hover:text-ink"><X className="size-4" /></button>
    </div>
  );
}

export default function App() {
  const { project, bridgeState, connect, view, activeSessionId, centerTab, setCenterTab } = useApp();

  useEffect(() => {
    void connect();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const events = onEvents((payload) => {
      const current = useApp.getState().project;
      if (!current || payload.project !== current.root) return;
      clearTimeout(timer); // coalesce bursts of events into one refresh
      timer = setTimeout(() => {
        useApp.getState().bumpEvents();
        void useApp.getState().refreshView();
        void useApp.getState().refreshSessions();
      }, 250);
    });
    const status = onBridgeStatus((state) => {
      if (state === "exited") useApp.setState({ bridgeState: "starting" });
      if (state === "started") void useApp.getState().connect();
    });
    const keys = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n" && useApp.getState().project?.initialized) {
        e.preventDefault();
        void useApp.getState().createSession();
      }
    };
    window.addEventListener("keydown", keys);
    return () => {
      clearTimeout(timer);
      void events.then((un) => un());
      void status.then((un) => un());
      window.removeEventListener("keydown", keys);
    };
  }, [connect]);

  return (
    <div className="flex h-full flex-col">
      <NoticeBar />
      {!project?.initialized ? (
        <div className="flex-1"><ProjectPicker /></div>
      ) : (
        <div className="relative flex min-h-0 flex-1">
          <Sidebar />
          <main className="flex min-w-0 flex-1 flex-col">
            <header className="flex h-11 items-center gap-3 border-b border-line px-5 text-[13px]">
              <span className="min-w-0 truncate font-medium">{view?.session.title ?? (activeSessionId ? "" : "Select or create a session")}</span>
              {activeSessionId && (
                <nav className="ml-2 flex gap-0.5 rounded-md bg-raised p-0.5 text-[12px]" aria-label="View">
                  {(["conversation", "timeline"] as const).map((tab) => (
                    <button key={tab} type="button" onClick={() => setCenterTab(tab)}
                      className={cn("rounded px-2 py-0.5 capitalize", centerTab === tab ? "bg-panel text-ink shadow-sm" : "text-muted hover:text-ink")}>
                      {tab}
                    </button>
                  ))}
                </nav>
              )}
              {bridgeState !== "ready" && <span className="ml-auto text-[12px] text-warn">Reconnecting to the engine…</span>}
            </header>
            {!activeSessionId ? (
              <div className="grid flex-1 place-items-center text-[13px] text-faint">Create a session to start.</div>
            ) : centerTab === "timeline" ? (
              <>
                <RunTimeline />
                <RunBar />
              </>
            ) : (
              <>
                <Conversation />
                <RunBar />
                <Composer />
              </>
            )}
          </main>
          <ExecutionPanel />
          <TaskInspector />
        </div>
      )}
    </div>
  );
}

import { PanelRight, X } from "lucide-react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { useEffect, type ReactNode } from "react";

import { Composer, RunBar } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { PermissionsPage, SettingsPage, UsagePage } from "./components/Pages";
import { ProjectPicker } from "./components/ProjectPicker";
import { RunTimeline } from "./components/RunTimeline";
import { Segmented, SOFT } from "./components/primitives";
import { Sidebar } from "./components/Sidebar";
import { TaskInspector } from "./components/TaskInspector";
import { onBridgeStatus, onEvents } from "./lib/bridge";
import { prefersReducedMotion } from "./lib/theme";
import { useApp } from "./store/app";

function NoticeBar() {
  const { notice, dismissNotice } = useApp();
  return (
    <AnimatePresence initial={false}>
      {notice && (
        <motion.div key="notice" role="alert" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }} transition={SOFT} className="overflow-hidden border-b border-err/30 bg-err/10">
          <div className="flex items-start gap-3 px-4 py-2 text-[13px]">
            <div className="min-w-0 flex-1">
              <span className="text-err">{notice.message}</span>
              {notice.hint && <span className="ml-2 text-muted">{notice.hint}</span>}
            </div>
            <button type="button" aria-label="Dismiss" onClick={dismissNotice} className="text-muted hover:text-ink"><X className="size-4" /></button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** Each view fades and rises into place; switching away fades it out first. */
function View({ id, children }: { id: string; children: ReactNode }) {
  return (
    <motion.div data-view={id} className="flex min-h-0 flex-1 flex-col" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }} transition={SOFT}>
      {children}
    </motion.div>
  );
}

export default function App() {
  const { project, bridgeState, connect, view, activeSessionId, centerTab, setCenterTab, page, appearance, setAppearance } = useApp();

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

  const viewId = page !== "session" ? page : `${activeSessionId ?? "none"}:${centerTab}`;
  return (
    <MotionConfig reducedMotion={prefersReducedMotion(appearance.motion) ? "always" : "never"} transition={SOFT}>
      <div className="flex h-full flex-col">
        <NoticeBar />
        <AnimatePresence mode="wait" initial={false}>
          {!project?.initialized ? (
            <View key="picker" id="picker"><ProjectPicker /></View>
          ) : (
            <motion.div key="workspace" className="relative flex min-h-0 flex-1" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <Sidebar />
              <main className="flex min-w-0 flex-1 flex-col">
                <header className="glass sticky top-0 z-10 flex h-12 items-center gap-3 border-b border-line/70 px-5 text-[13px]">
                  <span className="min-w-0 truncate text-[14px] font-semibold">
                    {page !== "session" ? <span className="font-medium text-muted">{project.name}</span>
                      : (view?.session.title ?? (activeSessionId ? "" : "Select or create a session"))}
                  </span>
                  {activeSessionId && page === "session" && (
                    <Segmented size="sm" label="View" className="ml-2 w-52" value={centerTab} onChange={setCenterTab}
                      options={[{ value: "conversation", label: "Conversation" }, { value: "timeline", label: "Timeline" }]} />
                  )}
                  <span className="ml-auto flex items-center gap-3">
                    {bridgeState !== "ready" && <span className="text-[12px] text-warn">Reconnecting to the engine…</span>}
                    {page === "session" && (
                      <button type="button" aria-pressed={appearance.showExecution} title="Show or hide the execution panel"
                        onClick={() => setAppearance({ showExecution: !appearance.showExecution })}
                        className="rounded-md p-1.5 text-muted hover:bg-raised hover:text-ink aria-pressed:text-ink">
                        <PanelRight className="size-4" />
                      </button>
                    )}
                  </span>
                </header>
                <AnimatePresence mode="wait" initial={false}>
                  <View key={viewId} id={viewId}>
                    {page === "usage" ? <UsagePage />
                      : page === "permissions" ? <PermissionsPage />
                      : page === "settings" ? <SettingsPage />
                      : !activeSessionId ? (
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
                  </View>
                </AnimatePresence>
              </main>
              <AnimatePresence initial={false}>
                {page === "session" && appearance.showExecution && <ExecutionPanel key="execution" />}
              </AnimatePresence>
              <TaskInspector />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </MotionConfig>
  );
}

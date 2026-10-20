// Application state. It caches what the bridge returned; every change goes through a bridge call first.
import { create } from "zustand";

import { BridgeError, call, idempotencyKey } from "../lib/bridge";
import { applyTheme, loadDefaultPreferences, loadTheme, type Theme } from "../lib/theme";
import type { BridgeInfo, ProjectInfo, SessionPreferences, SessionSummary, SessionView } from "../lib/types";

const RECENT_KEY = "saarathi.recentProjects";

function loadRecent(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as string[]).slice(0, 8) : [];
  } catch {
    return [];
  }
}

function saveRecent(paths: string[]): void {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(paths.slice(0, 8)));
  } catch {
    // Recent projects are a convenience; losing them is harmless.
  }
}

export type Page = "session" | "usage" | "permissions" | "settings";

export type InspectorTab = "overview" | "attempts" | "routing" | "changes" | "logs";

export interface Inspector {
  runId: string;
  taskId: string;
  tab: InspectorTab;
}

export interface Notice {
  kind: string;
  message: string;
  hint: string | null;
}

interface AppState {
  bridge: BridgeInfo | null;
  bridgeState: "starting" | "ready" | "unavailable";
  project: ProjectInfo | null;
  recent: string[];
  sessions: SessionSummary[];
  showArchived: boolean;
  search: string;
  activeSessionId: string | null;
  view: SessionView | null;
  busy: Record<string, boolean>;
  notice: Notice | null;
  /** Bumped once per burst of engine events for the open project; inspection views refetch on it. */
  eventTick: number;
  centerTab: "conversation" | "timeline";
  inspector: Inspector | null;
  page: Page;
  theme: Theme;

  connect: () => Promise<void>;
  openProject: (path: string) => Promise<void>;
  initProject: () => Promise<void>;
  closeProject: () => void;
  refreshSessions: () => Promise<void>;
  setSearch: (q: string) => Promise<void>;
  toggleArchived: () => Promise<void>;
  selectSession: (id: string | null) => Promise<void>;
  createSession: (title?: string) => Promise<void>;
  renameSession: (id: string, title: string) => Promise<void>;
  archiveSession: (id: string) => Promise<void>;
  refreshView: () => Promise<void>;
  send: (text: string, acceptDefaults: boolean) => Promise<boolean>;
  approve: (runId: string) => Promise<void>;
  answer: (runId: string, questionId: string, text: string) => Promise<void>;
  stop: (runId: string) => Promise<void>;
  cancel: (runId: string) => Promise<void>;
  resume: (runId: string) => Promise<void>;
  dismissNotice: () => void;
  bumpEvents: () => void;
  setCenterTab: (tab: "conversation" | "timeline") => void;
  openTask: (runId: string, taskId: string, tab?: InspectorTab) => void;
  setInspectorTab: (tab: InspectorTab) => void;
  closeInspector: () => void;
  setPage: (page: Page) => void;
  setTheme: (theme: Theme) => void;
  setPreferences: (prefs: SessionPreferences) => Promise<boolean>;
}

export const useApp = create<AppState>((set, get) => {
  /** Run an action, mark it busy, and surface the bridge's error text verbatim. */
  async function act<T>(key: string, fn: () => Promise<T>): Promise<T | undefined> {
    set((s) => ({ busy: { ...s.busy, [key]: true }, notice: null }));
    try {
      return await fn();
    } catch (err) {
      const e = err instanceof BridgeError ? err : new BridgeError({ code: -1, message: String(err) });
      set({ notice: { kind: e.kind, message: e.message, hint: e.hint } });
      return undefined;
    } finally {
      set((s) => ({ busy: { ...s.busy, [key]: false } }));
    }
  }

  const root = () => get().project?.root ?? "";

  return {
    bridge: null,
    bridgeState: "starting",
    project: null,
    recent: loadRecent(),
    sessions: [],
    showArchived: false,
    search: "",
    activeSessionId: null,
    view: null,
    busy: {},
    notice: null,
    eventTick: 0,
    centerTab: "conversation",
    inspector: null,
    page: "session",
    theme: loadTheme(),

    connect: async () => {
      const info = await act("connect", () => call<BridgeInfo>("system.initialize", { client: "saarathi-desktop", protocol: 1 }));
      set({ bridge: info ?? null, bridgeState: info ? "ready" : "unavailable" });
      const last = get().recent[0];
      if (info && last) await get().openProject(last);
    },

    openProject: async (path) => {
      const info = await act("project", () => call<ProjectInfo>("projects.open", { path }));
      if (!info) return;
      const recent = [info.root, ...get().recent.filter((p) => p !== info.root)];
      saveRecent(recent);
      set({ project: info, recent, activeSessionId: null, view: null, sessions: [], inspector: null, page: "session" });
      if (info.initialized) {
        await call("subscribe", { project: info.root }).catch(() => undefined);
        await get().refreshSessions();
        const first = get().sessions[0];
        if (first) await get().selectSession(first.id);
      }
    },

    initProject: async () => {
      const info = await act("project", () => call<ProjectInfo>("projects.init", { path: root() }));
      if (info) await get().openProject(info.root);
    },

    closeProject: () => {
      const r = root();
      if (r) void call("unsubscribe", { project: r }).catch(() => undefined);
      set({ project: null, sessions: [], activeSessionId: null, view: null, inspector: null });
    },

    refreshSessions: async () => {
      if (!get().project?.initialized) return;
      const { showArchived, search } = get();
      const sessions = await act("sessions", () =>
        call<SessionSummary[]>("sessions.list", { project: root(), include_archived: showArchived, query: search || null }),
      );
      if (sessions) set({ sessions });
    },

    setSearch: async (q) => {
      set({ search: q });
      await get().refreshSessions();
    },

    toggleArchived: async () => {
      set((s) => ({ showArchived: !s.showArchived }));
      await get().refreshSessions();
    },

    selectSession: async (id) => {
      // Switching only changes what is displayed; running drivers are untouched.
      set({ activeSessionId: id, view: null, inspector: null, page: "session" });
      if (id) await get().refreshView();
    },

    createSession: async (title = "New session") => {
      const created = await act("create", () => call<SessionSummary>("sessions.create", { project: root(), title }));
      if (!created) return;
      const defaults = loadDefaultPreferences();
      if (defaults.approvals !== "ask" || defaults.model) {
        // The policy may refuse a default chosen under another project's policy; the session then keeps "ask".
        await act("preferences", () =>
          call("sessions.set_preferences", { project: root(), session: created.id, ...defaults }),
        );
      }
      await get().refreshSessions();
      await get().selectSession(created.id);
    },

    renameSession: async (id, title) => {
      await act("rename", () => call("sessions.rename", { project: root(), session: id, title }));
      await get().refreshSessions();
      if (get().activeSessionId === id) await get().refreshView();
    },

    archiveSession: async (id) => {
      await act("archive", () => call("sessions.archive", { project: root(), session: id }));
      if (get().activeSessionId === id) set({ activeSessionId: null, view: null });
      await get().refreshSessions();
    },

    refreshView: async () => {
      const id = get().activeSessionId;
      if (!id) return;
      try {
        const view = await call<SessionView>("sessions.get", { project: root(), session: id });
        if (get().activeSessionId === id) set({ view });
      } catch (err) {
        if (err instanceof BridgeError) set({ notice: { kind: err.kind, message: err.message, hint: err.hint } });
      }
    },

    send: async (text, acceptDefaults) => {
      const id = get().activeSessionId;
      if (!id) return false;
      const sent = await act("send", () =>
        call("sessions.send", {
          project: root(),
          session: id,
          text,
          idempotency_key: idempotencyKey("send"),
          accept_defaults: acceptDefaults,
        }),
      );
      await Promise.all([get().refreshView(), get().refreshSessions()]);
      return sent !== undefined;
    },

    approve: async (runId) => {
      await act(`approve:${runId}`, () =>
        call("runs.approve", { project: root(), run: runId, idempotency_key: idempotencyKey("approve") }),
      );
      await Promise.all([get().refreshView(), get().refreshSessions()]);
    },

    answer: async (runId, questionId, text) => {
      await act(`answer:${runId}:${questionId}`, () =>
        call("runs.answer", { project: root(), run: runId, question: questionId, text }),
      );
      await Promise.all([get().refreshView(), get().refreshSessions()]);
    },

    stop: async (runId) => {
      await act(`stop:${runId}`, () => call("runs.stop", { project: root(), run: runId }));
      await Promise.all([get().refreshView(), get().refreshSessions()]);
    },

    cancel: async (runId) => {
      await act(`cancel:${runId}`, () => call("runs.cancel", { project: root(), run: runId }));
      await Promise.all([get().refreshView(), get().refreshSessions()]);
    },

    resume: async (runId) => {
      await act(`resume:${runId}`, () => call("runs.resume", { project: root(), run: runId }));
      await Promise.all([get().refreshView(), get().refreshSessions()]);
    },

    dismissNotice: () => set({ notice: null }),
    bumpEvents: () => set((s) => ({ eventTick: s.eventTick + 1 })),
    setCenterTab: (centerTab) => set({ centerTab }),
    openTask: (runId, taskId, tab) =>
      set((s) => ({ inspector: { runId, taskId, tab: tab ?? s.inspector?.tab ?? "overview" } })),
    setInspectorTab: (tab) => set((s) => (s.inspector ? { inspector: { ...s.inspector, tab } } : {})),
    closeInspector: () => set({ inspector: null }),
    setPage: (page) => set({ page, inspector: null }),
    setTheme: (theme) => {
      applyTheme(theme);
      set({ theme });
    },
    setPreferences: async (prefs) => {
      const id = get().activeSessionId;
      if (!id) return false;
      const saved = await act("preferences", () =>
        call("sessions.set_preferences", { project: root(), session: id, ...prefs }),
      );
      await Promise.all([get().refreshView(), get().refreshSessions()]);
      return saved !== undefined;
    },
  };
});

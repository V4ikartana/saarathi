// A render error must never leave a blank window: show what happened and a way back (skill: error-recovery).
import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Saarathi Desktop render error", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="grid h-full place-items-center bg-bg px-6 text-center">
        <div className="max-w-md">
          <h1 className="text-[22px] font-semibold tracking-tight">Something went wrong in the app</h1>
          <p className="mt-2 text-[14px] text-muted">
            Your runs are safe: they run in the engine, not in this window. Reload the window to continue.
          </p>
          <pre className="mt-4 max-h-40 overflow-auto rounded-xl bg-raised p-3 text-left font-mono text-[12px] text-faint">
            {this.state.error.message}
          </pre>
          <button type="button" onClick={() => window.location.reload()}
            className="mt-5 h-10 rounded-[var(--radius-control)] bg-accent px-5 text-[14px] font-medium text-accent-ink">
            Reload the window
          </button>
        </div>
      </div>
    );
  }
}

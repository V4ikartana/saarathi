// The session's approval preset and worker model. The engine validates both against the policy when they are saved
// and again when a run starts; the UI only offers what the policy allows and shows why the rest is unavailable.
import { Cpu, ShieldCheck } from "lucide-react";

import type { ApprovalPreset, ModelInfo, ModelMode, PolicyView, SessionPreferences } from "../lib/types";
import { latestRun } from "../lib/types";
import { useBridge } from "../lib/useBridge";
import { useApp } from "../store/app";
import { Skeleton } from "./feedback";

const SELECT =
  "h-7 rounded-lg border border-line bg-bg px-2 text-[12px] text-ink outline-none hover:border-faint/60 focus:ring-2 focus:ring-accent/30 disabled:opacity-50";

export function currentPreferences(raw: Partial<SessionPreferences> | undefined): SessionPreferences {
  return { approvals: raw?.approvals ?? "ask", model: raw?.model ?? null, model_mode: raw?.model_mode ?? "prefer" };
}

export function SessionControls() {
  const { project, view, setPreferences, busy } = useApp();
  const root = project?.root ?? "";
  const policy = useBridge<PolicyView>("policy.get", root ? { project: root } : null, { live: false });
  const models = useBridge<ModelInfo[]>("models.list", root ? { project: root } : null, { live: false });
  if (!view) return null;
  const prefs = currentPreferences(view.session.preferences);
  const run = latestRun(view);
  const pending = Boolean(run && !run.terminal);
  const save = (next: Partial<SessionPreferences>) => void setPreferences({ ...prefs, ...next });
  const presets = policy.data?.approvals.presets ?? [];
  const chosenPreset = presets.find((p) => p.id === prefs.approvals);
  const workers = models.data ?? [];
  const chosenModel = workers.find((m) => m.candidate === prefs.model);

  if (!policy.data || !models.data) {
    // The first model list asks each provider CLI, which can take a few seconds.
    return (
      <div className="flex items-center gap-3 px-3 pb-2" data-tour="session-controls" role="status" aria-busy="true" aria-label="Loading session controls">
        <Skeleton className="h-7 w-44 rounded-lg" /><Skeleton className="h-7 w-56 rounded-lg" />
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 pb-2 text-[12px] text-muted" data-tour="session-controls">
      <label className="flex items-center gap-1.5" title={chosenPreset?.description}>
        <ShieldCheck className="size-3.5" />
        <span className="sr-only">Approvals</span>
        <select aria-label="Approval preset" className={`${SELECT} max-w-60`} value={prefs.approvals} disabled={busy.preferences || !presets.length}
          onChange={(e) => save({ approvals: e.target.value as ApprovalPreset })}>
          {presets.map((p) => (
            <option key={p.id} value={p.id} disabled={!p.allowed}>
              {p.label}{p.allowed ? "" : " (not allowed by policy)"}
            </option>
          ))}
        </select>
      </label>
      <label className="flex min-w-0 items-center gap-1.5">
        <Cpu className="size-3.5 shrink-0" />
        <span className="sr-only">Worker model</span>
        <select aria-label="Worker model" className={`${SELECT} min-w-0 max-w-64`} value={prefs.model ?? ""} disabled={busy.preferences}
          onChange={(e) => save({ model: e.target.value || null })}>
          <option value="">Workers: chosen by the router</option>
          {workers.map((m) => (
            <option key={m.candidate} value={m.candidate} disabled={m.disabled || !m.available}>
              {m.candidate} · {m.reasoning_tier}{m.source.startsWith("discovered") ? " · discovered" : ""}
              {m.disabled ? " (turned off in Settings)" : m.available ? "" : ` (unavailable: ${m.unavailable_reason ?? "unknown reason"})`}{m.provider_limit ? " (limited)" : ""}
            </option>
          ))}
        </select>
        {prefs.model && (
          <select aria-label="Model mode" className={SELECT} value={prefs.model_mode} disabled={busy.preferences}
            onChange={(e) => save({ model_mode: e.target.value as ModelMode })}>
            <option value="prefer">Prefer</option>
            <option value="pin">Pin</option>
          </select>
        )}
      </label>
      <span className="text-[11px] text-faint">
        {prefs.model && prefs.model_mode === "pin"
          ? "Pinned: tasks wait if this model is ineligible."
          : prefs.model
            ? "Preferred when eligible and free."
            : ""}
        {chosenModel && !chosenModel.available && <span className="text-warn"> {chosenModel.unavailable_reason}</span>}
        {pending && " Changes apply to the next request."}
      </span>
    </div>
  );
}

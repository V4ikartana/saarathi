// The session's approval preset and worker model. The engine validates both against the policy when they are saved
// and again when a run starts; the UI only offers what the policy allows and shows why the rest is unavailable.
import { Cpu, ShieldCheck } from "lucide-react";

import type { ApprovalPreset, ModelInfo, ModelMode, PolicyView, SessionPreferences } from "../lib/types";
import { latestRun } from "../lib/types";
import { useBridge } from "../lib/useBridge";
import { useApp } from "../store/app";

const SELECT =
  "h-7 rounded-md border border-line bg-bg px-1.5 text-[12px] text-ink outline-none focus:ring-1 focus:ring-accent/50 disabled:opacity-50";

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

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 pb-2 text-[12px] text-muted">
      <label className="flex items-center gap-1.5" title={chosenPreset?.description}>
        <ShieldCheck className="size-3.5" />
        <span className="sr-only">Approvals</span>
        <select aria-label="Approval preset" className={SELECT} value={prefs.approvals} disabled={busy.preferences || !presets.length}
          onChange={(e) => save({ approvals: e.target.value as ApprovalPreset })}>
          {presets.map((p) => (
            <option key={p.id} value={p.id} disabled={!p.allowed}>
              {p.label}{p.allowed ? "" : " (not allowed by policy)"}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5">
        <Cpu className="size-3.5" />
        <span className="sr-only">Worker model</span>
        <select aria-label="Worker model" className={SELECT} value={prefs.model ?? ""} disabled={busy.preferences}
          onChange={(e) => save({ model: e.target.value || null })}>
          <option value="">Workers: chosen by the router</option>
          {workers.map((m) => (
            <option key={m.candidate} value={m.candidate}>
              {m.candidate}{m.source.startsWith("discovered") ? " · discovered" : ""}{m.available ? "" : " (unavailable)"}{m.provider_limit ? " (limited)" : ""}
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

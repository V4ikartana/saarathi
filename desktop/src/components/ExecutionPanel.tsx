import { GitBranch, Users } from "lucide-react";

import { useApp } from "../store/app";
import { StatePill } from "./primitives";

export function ExecutionPanel() {
  const view = useApp((s) => s.view);
  const run = view ? Object.values(view.runs).at(-1) : undefined;
  return (
    <aside className="hidden w-80 shrink-0 flex-col overflow-y-auto border-l border-line bg-panel xl:flex">
      <div className="border-b border-line px-4 py-3">
        <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Execution</div>
        {run ? (
          <div className="mt-2 space-y-1 text-[12px] text-muted">
            <div className="flex items-center gap-2"><StatePill state={run.state} /><span className="font-mono text-faint">{run.run_id}</span></div>
            {run.lead?.adapter && <div><Users className="mr-1 inline size-3.5" />Lead: {run.lead.adapter}:{run.lead.model}</div>}
            {run.integration_branch && <div className="truncate"><GitBranch className="mr-1 inline size-3.5" />{run.integration_branch}</div>}
          </div>
        ) : (
          <p className="mt-2 text-[12px] text-faint">No run in this session yet.</p>
        )}
      </div>
      {run && run.tasks.length > 0 && (
        <div className="px-4 py-3">
          <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Tasks</div>
          <ul className="mt-2 space-y-2">
            {run.tasks.map((t) => (
              <li key={t.id} className="rounded-lg border border-line px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[11px] text-faint">{t.id}</span>
                  <span className="min-w-0 flex-1 truncate text-[13px]">{t.title}</span>
                  <StatePill state={t.state} />
                </div>
                <div className="mt-1 text-[11px] text-muted">
                  {t.worker ? <>{t.worker} · {t.provider}</> : "not assigned yet"}
                  {t.attempts > 0 && <> · {t.attempts} attempt{t.attempts > 1 ? "s" : ""}</>}
                  {t.dependencies.length > 0 && <> · after {t.dependencies.join(", ")}</>}
                </div>
                {(t.blocked_reason || (t.failure_reason && t.state !== "SUCCEEDED")) && (
                  <div className="mt-1 text-[11px] text-warn">{t.blocked_reason ?? t.failure_reason}</div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {run?.integration?.checks && (
        <div className="border-t border-line px-4 py-3">
          <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Integration checks</div>
          <ul className="mt-2 space-y-1 font-mono text-[11px]">
            {run.integration.checks.map((c) => (
              <li key={c.command} className={c.exit_code === 0 ? "text-ok" : "text-err"}>exit {c.exit_code} · {c.command}</li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}

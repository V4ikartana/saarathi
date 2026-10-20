import { ChartGantt, GitBranch, Users } from "lucide-react";
import { motion } from "motion/react";

import { cn } from "../lib/ui";

import { latestRun } from "../lib/types";
import { useApp } from "../store/app";
import { SOFT, StatePill } from "./primitives";

export function ExecutionPanel() {
  const { view, openTask, inspector, setCenterTab } = useApp();
  const run = latestRun(view);
  return (
    <motion.aside initial={{ width: 0, opacity: 0 }} animate={{ width: 320, opacity: 1 }} exit={{ width: 0, opacity: 0 }}
      transition={SOFT} className="hidden shrink-0 overflow-hidden border-l border-line bg-panel lg:block" data-tour="execution">
      <div className="flex h-full w-80 flex-col overflow-y-auto">
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
          <div className="flex items-center text-[11px] font-medium uppercase tracking-wide text-faint">
            Tasks
            <button type="button" onClick={() => setCenterTab("timeline")}
              className="ml-auto flex items-center gap-1 normal-case tracking-normal text-muted hover:text-ink">
              <ChartGantt className="size-3.5" />Timeline
            </button>
          </div>
          <ul className="mt-2 space-y-2">
            {run.tasks.map((t) => (
              <motion.li key={t.id} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SOFT}>
                <button type="button" onClick={() => openTask(run.run_id, t.id)}
                  className={cn("w-full rounded-xl border px-3 py-2.5 text-left hover:bg-raised hover:shadow-sm",
                    inspector?.taskId === t.id && inspector.runId === run.run_id ? "border-accent/60" : "border-line")}>
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
                </button>
              </motion.li>
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
      </div>
    </motion.aside>
  );
}

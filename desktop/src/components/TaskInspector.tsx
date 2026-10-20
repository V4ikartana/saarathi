// Everything the engine recorded about one task. Read-only: the inspector shows records, it never changes them.
import { ChevronRight, Columns2, FileCode2, Rows2, ScrollText, X } from "lucide-react";
import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";

import type {
  Attempt,
  CommandEvidence,
  EngineEvent,
  FileVersions,
  RoutingDecision,
  TaskDiff,
  TaskInspection,
  TaskLogs,
} from "../lib/types";
import { cn, durationMs, fmtDuration, fmtTokens, OUTCOME_TONE, shortSha, timeOf } from "../lib/ui";
import { useBridge } from "../lib/useBridge";
import { type InspectorTab, useApp } from "../store/app";
import { StatePill } from "./primitives";

const DiffView = lazy(() => import("./DiffView"));
const LogView = lazy(() => import("./LogView"));

const TABS: { id: InspectorTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "attempts", label: "Attempts" },
  { id: "routing", label: "Routing" },
  { id: "changes", label: "Changes" },
  { id: "logs", label: "Logs" },
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-5 first:mt-0">
      <h3 className="text-[11px] font-medium uppercase tracking-wide text-faint">{title}</h3>
      <div className="mt-1.5">{children}</div>
    </section>
  );
}

function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-[12px]", className)}>{children}</span>;
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="grid h-full place-items-center px-6 text-center text-[13px] text-faint">{children}</div>;
}

function Loading() {
  return <Empty>Loading…</Empty>;
}

function OutcomePill({ attempt }: { attempt: Pick<Attempt, "status" | "outcome"> }) {
  if (attempt.status === "running") return <StatePill state="RUNNING" />;
  const tone = OUTCOME_TONE[attempt.outcome ?? ""] ?? "idle";
  const cls = { ok: "bg-ok/15 text-ok", err: "bg-err/15 text-err", warn: "bg-warn/15 text-warn", busy: "", idle: "bg-raised text-muted" }[tone];
  return <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", cls)}>{attempt.outcome ?? attempt.status}</span>;
}

// ---------------------------------------------------------------- overview

function Overview({ data, runId }: { data: TaskInspection; runId: string }) {
  const openTask = useApp((s) => s.openTask);
  const t = data.task;
  const last = data.attempts.at(-1);
  const report = last?.result?.worker_report;
  const failure = t.blocked_reason ?? (t.state !== "SUCCEEDED" ? (last?.result?.failure_reason ?? t.result?.failure_reason) : null);
  return (
    <div className="px-5 py-4 text-[13px]">
      {failure && <div className="mb-4 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-warn">{failure}</div>}
      <Section title="Objective"><p className="whitespace-pre-wrap leading-relaxed">{t.objective}</p></Section>
      <Section title="Acceptance criteria">
        <ul className="list-disc space-y-0.5 pl-5 text-muted">{t.acceptance_criteria.map((c) => <li key={c}>{c}</li>)}</ul>
      </Section>
      <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4">
        <Section title="Depends on">
          {t.dependencies.length === 0 ? <span className="text-faint">Nothing</span> : (
            <div className="flex flex-wrap gap-1.5">
              {t.dependencies.map((d) => (
                <button key={d} type="button" onClick={() => openTask(runId, d, "overview")}
                  className="rounded border border-line px-1.5 font-mono text-[12px] hover:border-accent hover:text-accent">{d}</button>
              ))}
            </div>
          )}
        </Section>
        <Section title="Profile">
          <span className="text-muted">{t.complexity} · {t.risk} risk · up to {t.max_attempts} attempts</span>
        </Section>
        <Section title="Allowed paths">
          <div className="space-y-0.5">{t.allowed_paths.map((p) => <div key={p}><Mono>{p}</Mono></div>)}</div>
        </Section>
        <Section title="Verification (run by Saarathi)">
          <div className="space-y-0.5">{t.verification_commands.map((c) => <div key={c}><Mono>{c}</Mono></div>)}</div>
        </Section>
        <Section title="Capabilities">
          <span className="text-muted">{t.required_capabilities.join(", ") || "none"}</span>
        </Section>
        <Section title="Branch">
          {t.branch ? <Mono className="break-all">{t.branch}</Mono> : <span className="text-faint">Not created yet</span>}
          {t.result?.commit && (
            <div className="text-[12px] text-muted">
              commit <Mono>{shortSha(t.result.commit)}</Mono>{t.state !== "SUCCEEDED" && <span className="text-warn"> · not accepted</span>}
            </div>
          )}
        </Section>
      </div>
      {report && (
        <Section title={`Worker's report (attempt ${last?.number}, advisory)`}>
          <p className="whitespace-pre-wrap leading-relaxed">{report.summary}</p>
          {report.unresolved_risks.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-warn">{report.unresolved_risks.map((r) => <li key={r}>{r}</li>)}</ul>
          )}
        </Section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- attempts

function Evidence({ e }: { e: CommandEvidence }) {
  const [open, setOpen] = useState(false);
  const passed = e.exit_code === 0 && !e.timed_out && !e.rejected_reason;
  return (
    <div className="rounded-md border border-line">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left">
        <ChevronRight className={cn("size-3.5 shrink-0 text-faint transition-transform", open && "rotate-90")} />
        <Mono className="min-w-0 flex-1 truncate">{e.command}</Mono>
        <span className={cn("shrink-0 text-[11px]", passed ? "text-ok" : "text-err")}>
          {e.rejected_reason ? "rejected" : e.timed_out ? "timed out" : `exit ${e.exit_code}`} · {e.duration_s.toFixed(1)}s
        </span>
      </button>
      {open && (
        <pre className="max-h-64 overflow-auto border-t border-line bg-raised px-3 py-2 font-mono text-[11px] whitespace-pre-wrap text-muted">
          {e.rejected_reason ?? (e.output_tail || "(no output)")}
        </pre>
      )}
    </div>
  );
}

function AttemptCard({ a }: { a: Attempt }) {
  const r = a.result;
  const usage = r?.usage;
  return (
    <div className="rounded-xl border border-line bg-panel">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
        <span className="text-[13px] font-medium">Attempt {a.number}</span>
        <OutcomePill attempt={a} />
        <Mono className="text-muted">{a.adapter}:{r?.model ?? a.model}</Mono>
        <span className="ml-auto text-[11px] text-faint">
          {timeOf(a.started_at)} · {fmtDuration(durationMs(a.started_at, a.finished_at))}{a.finished_at ? "" : " so far"}
        </span>
      </div>
      <div className="space-y-3 px-4 py-3 text-[13px]">
        {r?.failure_reason && <div className="text-err">{r.failure_reason}</div>}
        {a.feedback && (
          <details>
            <summary className="cursor-pointer text-[11px] uppercase tracking-wide text-faint">Feedback given to this attempt</summary>
            <pre className="mt-1 max-h-56 overflow-auto rounded-md bg-raised px-3 py-2 font-mono text-[11px] whitespace-pre-wrap text-muted">{a.feedback}</pre>
          </details>
        )}
        {r?.verification && r.verification.length > 0 && (
          <div className="space-y-1.5">
            <div className="text-[11px] uppercase tracking-wide text-faint">Verification evidence</div>
            {r.verification.map((e, i) => <Evidence key={`${e.command}-${i}`} e={e} />)}
          </div>
        )}
        {r?.changed_files && r.changed_files.length > 0 && (
          <div>
            <div className="text-[11px] uppercase tracking-wide text-faint">Changed files (from Git)</div>
            <div className="mt-0.5 flex flex-wrap gap-x-3">{r.changed_files.map((f) => <Mono key={f}>{f}</Mono>)}</div>
          </div>
        )}
        {r?.scope_violations && r.scope_violations.length > 0 && (
          <div className="text-err">Outside allowed paths: <Mono>{r.scope_violations.join(", ")}</Mono></div>
        )}
        {r?.checkout_modified && <div className="text-err">Your checkout changed during this attempt.</div>}
        {r?.worker_report && (
          <div>
            <div className="text-[11px] uppercase tracking-wide text-faint">Worker says ({r.worker_report.status}, advisory)</div>
            <p className="mt-0.5 whitespace-pre-wrap">{r.worker_report.summary}</p>
            {r.worker_report.assumptions.length > 0 && (
              <ul className="mt-1 list-disc pl-5 text-muted">{r.worker_report.assumptions.map((x) => <li key={x}>{x}</li>)}</ul>
            )}
          </div>
        )}
        {r?.received_inputs && r.received_inputs.length > 0 && (
          <div className="text-muted">Inputs from dependencies: <Mono>{r.received_inputs.join(", ")}</Mono></div>
        )}
        {usage && (
          <div className="flex flex-wrap gap-x-4 text-[12px] text-muted">
            <span>Input {fmtTokens(usage.input_tokens)}</span>
            {usage.cached_input_tokens !== null && <span>cached {fmtTokens(usage.cached_input_tokens)}</span>}
            <span>Output {fmtTokens(usage.output_tokens)}</span>
            {usage.cost_usd !== null && (
              <span>{usage.cost_is_estimate ? "≈ " : ""}${usage.cost_usd.toFixed(4)}{usage.cost_is_estimate ? " (client estimate)" : ""}</span>
            )}
            {r?.commit && <span>commit <Mono>{shortSha(r.commit)}</Mono></span>}
          </div>
        )}
        {!r && a.status === "running" && <div className="text-muted">Running in an isolated worktree. Results appear when it finishes.</div>}
        {!r && a.status !== "running" && <div className="text-faint">No assessment was recorded ({a.outcome ?? a.status}).</div>}
      </div>
    </div>
  );
}

function Attempts({ data }: { data: TaskInspection }) {
  if (data.attempts.length === 0) return <Empty>No attempts yet. The task starts when its dependencies pass.</Empty>;
  return (
    <div className="space-y-3 px-5 py-4">
      {[...data.attempts].reverse().map((a) => <AttemptCard key={a.id} a={a} />)}
    </div>
  );
}

// ---------------------------------------------------------------- routing

function Decision({ d, attempt }: { d: RoutingDecision; attempt: Attempt | undefined }) {
  const scores = Object.entries(d.score_breakdown);
  return (
    <div className="rounded-xl border border-line bg-panel px-4 py-3 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        {d.chosen ? <Mono className="text-accent">{d.chosen}</Mono> : <span className="text-warn">No candidate chosen</span>}
        {attempt && <span className="text-[11px] text-faint">used by attempt {attempt.number}</span>}
        {d.awaiting && <span className="rounded-full bg-warn/15 px-2 py-0.5 text-[11px] text-warn">awaiting {d.awaiting.replace("_", " ")}</span>}
      </div>
      <p className="mt-1 text-muted">{d.reason}</p>
      {scores.length > 0 && (
        <table className="mt-2 w-full text-[12px]">
          <tbody>
            {scores.map(([k, v]) => (
              <tr key={k} className="border-t border-line/60">
                <td className="py-0.5 text-muted">{k.replaceAll("_", " ")}</td>
                <td className="py-0.5 text-right font-mono">{v.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {d.alternatives.length > 0 && <div className="mt-2 text-[12px] text-muted">Also eligible: <Mono>{d.alternatives.join(", ")}</Mono></div>}
      {d.cooling.length > 0 && (
        <div className="mt-1 text-[12px] text-warn">Cooling down: <Mono>{d.cooling.join(", ")}</Mono>{d.available_at && <> until {timeOf(d.available_at)}</>}</div>
      )}
      {d.busy.length > 0 && <div className="mt-1 text-[12px] text-muted">Busy: <Mono>{d.busy.join(", ")}</Mono></div>}
      {d.rejected.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[12px] text-faint">Filtered out ({d.rejected.length})</summary>
          <ul className="mt-1 space-y-1 text-[12px]">
            {d.rejected.map((r) => (
              <li key={r.candidate}><Mono>{r.candidate}</Mono><span className="text-muted"> — {r.reasons.join("; ")}</span></li>
            ))}
          </ul>
        </details>
      )}
      {d.estimated_cost_usd !== null && <div className="mt-1 text-[12px] text-faint">Estimated cost ≈ ${d.estimated_cost_usd.toFixed(4)}</div>}
    </div>
  );
}

function Routing({ data }: { data: TaskInspection }) {
  if (data.decisions.length === 0) return <Empty>Not routed yet.</Empty>;
  const byDecision = new Map(data.attempts.map((a) => [a.routing_decision_id, a]));
  return (
    <div className="space-y-3 px-5 py-4">
      <p className="text-[12px] text-faint">Hard filters run first; only eligible candidates are scored. Newest first.</p>
      {[...data.decisions].reverse().map((d) => <Decision key={d.decision_id} d={d} attempt={byDecision.get(d.decision_id)} />)}
    </div>
  );
}

// ---------------------------------------------------------------- changes

function Changes({ project, runId, taskId, accepted }: { project: string; runId: string; taskId: string; accepted: boolean }) {
  const diff = useBridge<TaskDiff>("tasks.diff", { project, run: runId, task: taskId });
  const [selected, setSelected] = useState<string | null>(null);
  const [sideBySide, setSideBySide] = useState(true);
  const files = diff.data?.files ?? [];
  const path = selected && files.some((f) => f.path === selected) ? selected : (files[0]?.path ?? null);
  const file = useBridge<FileVersions>(
    "tasks.file",
    path && diff.data?.commit ? { project, run: runId, task: taskId, path } : null,
    { live: false, version: diff.data?.commit ?? "" },
  );

  if (!diff.data) return diff.error ? <Empty>{diff.error.message}</Empty> : <Loading />;
  if (!diff.data.commit) return <Empty>No committed change yet. The diff appears once an attempt commits its work.</Empty>;
  return (
    <div className="flex h-full min-h-0 flex-col">
      {!accepted && (
        <div className="border-b border-warn/30 bg-warn/10 px-4 py-1.5 text-[12px] text-warn">
          The latest attempt's change. It has not been accepted: the task has not passed verification.
        </div>
      )}
      <div className="flex min-h-0 flex-1">
      <div className="w-56 shrink-0 overflow-y-auto border-r border-line py-2">
        <div className="px-3 pb-1 text-[11px] text-faint">
          <Mono>{shortSha(diff.data.base)}</Mono> → <Mono>{shortSha(diff.data.commit)}</Mono>
        </div>
        {files.map((f) => (
          <button key={f.path} type="button" onClick={() => setSelected(f.path)} title={f.path}
            className={cn("flex w-full items-center gap-2 px-3 py-1 text-left text-[12px] hover:bg-raised",
              f.path === path && "bg-raised text-ink")}>
            <FileCode2 className="size-3.5 shrink-0 text-faint" />
            <span className="min-w-0 flex-1 truncate font-mono">{f.path.split("/").pop()}</span>
            {f.binary ? <span className="text-faint">bin</span> : (
              <span className="shrink-0 font-mono text-[11px]"><span className="text-ok">+{f.added}</span> <span className="text-err">−{f.deleted}</span></span>
            )}
          </button>
        ))}
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-9 items-center gap-2 border-b border-line px-3 text-[12px]">
          <Mono className="min-w-0 flex-1 truncate text-muted">{path}</Mono>
          {file.data?.truncated && <span className="text-warn">truncated</span>}
          <button type="button" aria-label="Side by side" onClick={() => setSideBySide(true)}
            className={cn("rounded p-1 hover:bg-raised", sideBySide ? "text-ink" : "text-faint")}><Columns2 className="size-4" /></button>
          <button type="button" aria-label="Inline" onClick={() => setSideBySide(false)}
            className={cn("rounded p-1 hover:bg-raised", !sideBySide ? "text-ink" : "text-faint")}><Rows2 className="size-4" /></button>
        </div>
        <div className="min-h-0 flex-1">
          {file.error ? <Empty>{file.error.message}</Empty>
            : !file.data ? <Loading />
            : file.data.binary ? <Empty>Binary file: no text diff.</Empty>
            : <Suspense fallback={<Loading />}><DiffView file={file.data} sideBySide={sideBySide} /></Suspense>}
        </div>
      </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- logs

function eventLine(e: EngineEvent): string {
  const time = new Date(e.ts).toLocaleTimeString([], { hour12: false });
  const payload = JSON.stringify(e.payload);
  const colour = e.type.includes("fail") || e.type.includes("error") ? "31" : e.type.includes("succeed") || e.type.includes("pass") ? "32" : "36";
  return `\x1b[90m${time}\x1b[0m \x1b[${colour}m${e.type}\x1b[0m \x1b[90m${e.actor}\x1b[0m ${payload.length > 2 ? payload : ""}`;
}

function Logs({ project, runId, taskId }: { project: string; runId: string; taskId: string }) {
  const logs = useBridge<TaskLogs>("tasks.logs", { project, run: runId, task: taskId });
  const [selected, setSelected] = useState("events");
  if (!logs.data) return logs.error ? <Empty>{logs.error.message}</Empty> : <Loading />;
  const sources = [{ name: "events", label: "Engine events", truncated: false }, ...logs.data.logs.map((l) => ({ ...l, label: l.name }))];
  const current = logs.data.logs.find((l) => l.name === selected);
  const text = current ? current.text : logs.data.events.map(eventLine).join("\n");
  return (
    <div className="flex h-full min-h-0">
      <div className="w-56 shrink-0 overflow-y-auto border-r border-line py-2">
        {sources.map((s) => (
          <button key={s.name} type="button" onClick={() => setSelected(s.name)} title={s.label}
            className={cn("flex w-full items-center gap-2 px-3 py-1 text-left text-[12px] hover:bg-raised",
              (current?.name ?? "events") === s.name && "bg-raised text-ink")}>
            <ScrollText className="size-3.5 shrink-0 text-faint" />
            <span className="min-w-0 flex-1 truncate font-mono">{s.label}</span>
            {s.truncated && <span className="text-[10px] text-warn">tail</span>}
          </button>
        ))}
        {logs.data.logs.length === 0 && (
          <p className="px-3 pt-2 text-[11px] text-faint">Process and verification logs are written when an attempt finishes.</p>
        )}
      </div>
      <div className="min-w-0 flex-1 bg-bg">
        <Suspense fallback={<Loading />}><LogView text={text} /></Suspense>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- shell

export function TaskInspector() {
  const { inspector, project, setInspectorTab, closeInspector } = useApp();
  const root = project?.root ?? "";
  const data = useBridge<TaskInspection>(
    "tasks.inspect",
    inspector ? { project: root, run: inspector.runId, task: inspector.taskId } : null,
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeInspector();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeInspector]);

  if (!inspector) return null;
  const { runId, taskId, tab } = inspector;
  const t = data.data?.task;
  const fullHeight = tab === "changes" || tab === "logs";
  return (
    <div role="dialog" aria-label={`Task ${taskId}`}
      className="absolute inset-y-0 right-0 z-20 flex w-[min(920px,72vw)] flex-col border-l border-line bg-bg shadow-2xl shadow-black/40">
      <header className="flex items-center gap-2 border-b border-line px-5 py-3">
        <Mono className="text-faint">{taskId}</Mono>
        <span className="min-w-0 flex-1 truncate text-[14px] font-medium">{t?.title ?? ""}</span>
        {t && <StatePill state={t.state} />}
        <button type="button" aria-label="Close inspector" onClick={closeInspector} className="rounded p-1 text-muted hover:bg-raised hover:text-ink">
          <X className="size-4" />
        </button>
      </header>
      <nav className="flex gap-1 border-b border-line px-4">
        {TABS.map(({ id, label }) => (
          <button key={id} type="button" onClick={() => setInspectorTab(id)}
            className={cn("-mb-px border-b-2 px-2.5 py-2 text-[13px]", tab === id ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink")}>
            {label}
            {id === "attempts" && t ? <span className="ml-1 text-faint">{t.attempts}</span> : null}
          </button>
        ))}
      </nav>
      <div className={cn("min-h-0 flex-1", fullHeight ? "overflow-hidden" : "overflow-y-auto")}>
        {tab === "changes" ? <Changes project={root} runId={runId} taskId={taskId} accepted={t?.state === "SUCCEEDED"} />
          : tab === "logs" ? <Logs project={root} runId={runId} taskId={taskId} />
          : !data.data ? (data.error ? <Empty>{data.error.message}</Empty> : <Loading />)
          : tab === "overview" ? <Overview data={data.data} runId={runId} />
          : tab === "attempts" ? <Attempts data={data.data} />
          : <Routing data={data.data} />}
      </div>
    </div>
  );
}

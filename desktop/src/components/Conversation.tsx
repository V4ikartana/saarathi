import { AlertTriangle, CheckCircle2, ChevronRight, CircleDot, GitBranch, Info, ShieldAlert, XCircle } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";

import type { ConversationEntry, PlannedTask, Question, RunSnapshot } from "../lib/types";
import { latestRun } from "../lib/types";
import { cn, timeOf } from "../lib/ui";
import { useApp } from "../store/app";
import { AnimatedMark } from "./brand";
import { Skeleton, SkeletonCard, ThinkingDots } from "./feedback";
import { Button, SOFT, STAGGER } from "./primitives";

const LEVEL_ICON = {
  info: <CircleDot className="size-3.5 text-faint" />,
  success: <CheckCircle2 className="size-3.5 text-ok" />,
  warn: <AlertTriangle className="size-3.5 text-warn" />,
  error: <XCircle className="size-3.5 text-err" />,
};

function UserMessage({ entry }: { entry: ConversationEntry }) {
  const text = String(entry.data.text ?? entry.title);
  return (
    <div className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-accent/12 px-4 py-2.5 ring-1 ring-accent/25">
      {entry.data.answers ? <div className="mb-1 text-[11px] text-faint">Answer to {String(entry.data.answers)}</div> : null}
      <div className="whitespace-pre-wrap text-[14px] leading-relaxed">{text}</div>
    </div>
  );
}

/** One real engine event. Concise by default; technical detail on demand. */
function ActivityRow({ entry }: { entry: ConversationEntry }) {
  const [open, setOpen] = useState(false);
  const openTask = useApp((s) => s.openTask);
  const taskId = entry.task_id;
  const runId = entry.run_id;
  const hasDetail = Boolean(entry.detail) || entry.kind === "delegation";
  const icon = entry.kind === "delegation" ? <GitBranch className="size-3.5 text-accent" />
    : entry.kind === "security" ? <ShieldAlert className="size-3.5 text-err" />
    : LEVEL_ICON[entry.level ?? "info"];
  return (
    <div className="group">
      <div className="flex items-center gap-1 rounded-md hover:bg-raised/60">
        <button type="button" disabled={!hasDetail} onClick={() => setOpen(!open)}
          className="flex min-w-0 flex-1 items-center gap-2 px-1 py-1 text-left text-[13px] text-muted">
          {icon}
          <span className={cn("min-w-0 flex-1 truncate", entry.level === "error" && "text-err")}>{entry.title}</span>
        </button>
        {taskId && runId && (
          <button type="button" onClick={() => openTask(runId, taskId)}
            className="rounded px-1 text-[11px] text-accent opacity-0 hover:underline focus-visible:opacity-100 group-hover:opacity-100">
            Inspect {taskId}
          </button>
        )}
        <span className="text-[11px] text-faint">{timeOf(entry.ts)}</span>
        {hasDetail && (
          <button type="button" aria-label={open ? "Hide detail" : "Show detail"} onClick={() => setOpen(!open)} className="px-1">
            <ChevronRight className={cn("size-3.5 text-faint transition-transform", open && "rotate-90")} />
          </button>
        )}
      </div>
      {open && entry.detail && (
        <div className="ml-6 mb-1 whitespace-pre-wrap rounded-md bg-raised px-3 py-2 font-mono text-[12px] text-muted">
          {entry.detail}
        </div>
      )}
    </div>
  );
}

function PlanCard({ entry, run }: { entry: ConversationEntry; run: RunSnapshot | undefined }) {
  const { approve, busy } = useApp();
  const tasks = (entry.data.tasks ?? []) as PlannedTask[];
  const awaiting = run?.pending_approval === "plan";
  return (
    <div className="rounded-2xl border border-line bg-panel shadow-sm shadow-black/5">
      <div className="border-b border-line px-4 py-3">
        <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Plan</div>
        <div className="prose-sm mt-1 text-[14px] leading-relaxed"><Markdown>{entry.title}</Markdown></div>
      </div>
      <ol className="divide-y divide-line">
        {tasks.map((t) => (
          <li key={t.id} className="px-4 py-2.5">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[12px] text-faint">{t.id}</span>
              <span className="text-[13px]">{t.title}</span>
              <span className="ml-auto text-[11px] text-faint">{t.complexity} · {t.risk} risk</span>
            </div>
            <div className="mt-1 text-[12px] text-muted">
              {t.dependencies.length > 0 && <>after {t.dependencies.join(", ")} · </>}
              <span className="font-mono">{t.allowed_paths.join("  ")}</span>
            </div>
          </li>
        ))}
      </ol>
      {awaiting && run && (
        <div className="flex items-center gap-2 border-t border-line px-4 py-3">
          <span className="text-[12px] text-muted">Nothing runs until you approve.</span>
          <Button tone="primary" className="ml-auto" loading={busy[`approve:${run.run_id}`]}
            onClick={() => void approve(run.run_id)}>Approve plan</Button>
        </div>
      )}
    </div>
  );
}

function QuestionsCard({ run }: { run: RunSnapshot }) {
  const { answer, busy } = useApp();
  const open = run.questions.filter((q: Question) => q.answer === null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  if (open.length === 0) return null;
  return (
    <div className="rounded-2xl border border-warn/40 bg-panel px-4 py-3 shadow-sm shadow-black/5">
      <div className="text-[11px] font-medium uppercase tracking-wide text-warn">The lead needs answers</div>
      {open.map((q) => (
        <div key={q.id} className="mt-3">
          <div className="text-[14px]">{q.question}</div>
          <div className="mt-0.5 text-[12px] text-muted">{q.why_it_matters}</div>
          <textarea rows={2} value={drafts[q.id] ?? ""} placeholder={`Default if you accept it: ${q.default_assumption}`}
            onChange={(e) => setDrafts({ ...drafts, [q.id]: e.target.value })}
            className="mt-2 w-full resize-y rounded-md border border-line bg-bg px-2 py-1.5 text-[13px] outline-none focus:ring-1 focus:ring-accent/50" />
          <div className="mt-1.5 flex gap-2">
            <Button tone="primary" disabled={!drafts[q.id]?.trim() || busy[`answer:${run.run_id}:${q.id}`]}
              onClick={() => void answer(run.run_id, q.id, drafts[q.id] ?? "")}>Answer</Button>
            <Button tone="ghost" disabled={busy[`answer:${run.run_id}:${q.id}`]}
              onClick={() => void answer(run.run_id, q.id, `[accepted default] ${q.default_assumption}`)}>
              Accept default
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

function ReviewCard({ entry, run }: { entry: ConversationEntry; run: RunSnapshot | undefined }) {
  const { approve, busy } = useApp();
  const concerns = (entry.data.concerns ?? []) as string[];
  return (
    <div className="rounded-2xl border border-line bg-panel px-4 py-3 shadow-sm shadow-black/5">
      <div className="text-[11px] font-medium uppercase tracking-wide text-faint">{entry.title}</div>
      {entry.detail && <div className="prose-sm mt-1 text-[14px] leading-relaxed"><Markdown>{entry.detail}</Markdown></div>}
      {concerns.length > 0 && (
        <ul className="mt-2 space-y-1 text-[13px] text-muted">
          {concerns.map((c) => <li key={c} className="flex gap-2"><Info className="mt-0.5 size-3.5 shrink-0" />{c}</li>)}
        </ul>
      )}
      {run?.pending_approval === "review" && (
        <div className="mt-3 flex items-center gap-2">
          <span className="text-[12px] text-muted">Approving records the report. Saarathi never merges into your branches.</span>
          <Button tone="primary" className="ml-auto" loading={busy[`approve:${run.run_id}`]}
            onClick={() => void approve(run.run_id)}>Approve and finish</Button>
        </div>
      )}
    </div>
  );
}

const PHASE: Record<string, string> = {
  CREATED: "Starting the run",
  INTAKE: "The engineering lead is reading your request",
  PLANNING: "The engineering lead is planning",
  EXECUTING: "Workers are building in isolated worktrees",
  VERIFYING: "Merging and running integration checks",
};

/** The live "working" row: only while the engine is actually driving the run. */
function WorkingRow({ run }: { run: RunSnapshot }) {
  const done = run.tasks.filter((t) => t.state === "SUCCEEDED").length;
  const running = run.tasks.filter((t) => t.state === "RUNNING").length;
  return (
    <motion.div layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={SOFT}
      className="mt-2 flex items-center gap-3 rounded-2xl border border-accent/25 bg-accent/[0.06] px-4 py-3" role="status" aria-live="polite">
      <ThinkingDots className="text-accent" />
      <div className="min-w-0 flex-1 text-[13px]">
        <div className="font-medium">{PHASE[run.state] ?? "Working"}</div>
        {run.state === "EXECUTING" && run.tasks.length > 0 && (
          <div className="text-[12px] text-muted tabular">{done} of {run.tasks.length} tasks passed{running ? ` · ${running} running` : ""}</div>
        )}
      </div>
    </motion.div>
  );
}

const SUGGESTIONS = [
  "Add a health check endpoint with tests",
  "Fix the failing tests and explain the root cause",
  "Refactor the settings module and keep behaviour identical",
];

function EmptySession() {
  const setDraft = useApp((s) => s.setDraft);
  return (
    <div className="grid flex-1 place-items-center px-8">
      <div className="flex max-w-lg flex-col items-center text-center">
        <AnimatedMark size={64} />
        <motion.h2 className="mt-6 text-[26px] font-bold tracking-tight" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
          transition={{ ...SOFT, delay: 0.15 }}>What should we build?</motion.h2>
        <motion.p className="mt-2 text-[14px] leading-relaxed text-muted" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
          transition={{ ...SOFT, delay: 0.22 }}>
          Describe the change. The engineering lead asks if something is unclear, proposes a plan for your approval, then
          routes tasks to Claude Code, Codex or Antigravity in isolated worktrees.
        </motion.p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          {SUGGESTIONS.map((text, i) => (
            <motion.button key={text} type="button" onClick={() => setDraft(text)}
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: 0.3 + i * STAGGER }}
              whileHover={{ y: -2 }} whileTap={{ scale: 0.97 }}
              className="rounded-full border border-line bg-panel px-3.5 py-1.5 text-[13px] text-muted hover:border-accent/50 hover:text-ink elev-1">
              {text}
            </motion.button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ConversationSkeleton() {
  return (
    <div className="flex-1 overflow-hidden" role="status" aria-busy="true" aria-label="Loading the session">
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-6 py-8">
        <Skeleton className="ml-auto h-10 w-2/3 rounded-2xl" />
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="flex items-center gap-3"><Skeleton className="size-4 rounded-full" /><Skeleton className="w-1/2" /></div>
        ))}
        <SkeletonCard lines={4} />
      </div>
    </div>
  );
}

export function Conversation() {
  const view = useApp((s) => s.view);
  const end = useRef<HTMLDivElement>(null);
  const count = view?.conversation.length ?? 0;
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [count]);

  if (!view) return <ConversationSkeleton />;
  if (view.conversation.length === 0) return <EmptySession />;
  const lastRun = latestRun(view);
  const working = lastRun && lastRun.driver.running && !lastRun.pending_approval && !lastRun.terminal;
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-2 px-6 py-8">
        {view.conversation.map((e) => {
          const run = e.run_id ? view.runs[e.run_id] : undefined;
          const body = e.role === "user" ? <UserMessage entry={e} />
            : e.kind === "plan" ? <PlanCard entry={e} run={run} />
            : e.kind === "review" ? <ReviewCard entry={e} run={run} />
            : e.kind === "questions" ? (run ? <QuestionsCard run={run} /> : null)
            : <ActivityRow entry={e} />;
          if (!body) return null;
          return (
            <motion.div key={e.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={SOFT}>
              {body}
            </motion.div>
          );
        })}
        <AnimatePresence>{working && lastRun && <WorkingRow key="working" run={lastRun} />}</AnimatePresence>
        {lastRun?.pending_approval === "escalation" && (
          <div className="rounded-2xl border border-warn/40 bg-panel px-4 py-3 text-[13px]">
            The lead proposes changing an accepted decision. Review it with <code>saarathi escalations</code>; the
            approval screen arrives in a later milestone.
          </div>
        )}
        <div ref={end} />
      </div>
    </div>
  );
}

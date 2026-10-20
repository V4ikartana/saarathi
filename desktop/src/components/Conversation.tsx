import { AlertTriangle, CheckCircle2, ChevronRight, CircleDot, GitBranch, Info, ShieldAlert, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";

import type { ConversationEntry, PlannedTask, Question, RunSnapshot } from "../lib/types";
import { cn, timeOf } from "../lib/ui";
import { useApp } from "../store/app";
import { Button } from "./primitives";

const LEVEL_ICON = {
  info: <CircleDot className="size-3.5 text-faint" />,
  success: <CheckCircle2 className="size-3.5 text-ok" />,
  warn: <AlertTriangle className="size-3.5 text-warn" />,
  error: <XCircle className="size-3.5 text-err" />,
};

function UserMessage({ entry }: { entry: ConversationEntry }) {
  const text = String(entry.data.text ?? entry.title);
  return (
    <div className="ml-auto max-w-[85%] rounded-xl border border-line bg-raised px-3.5 py-2.5">
      {entry.data.answers ? <div className="mb-1 text-[11px] text-faint">Answer to {String(entry.data.answers)}</div> : null}
      <div className="whitespace-pre-wrap text-[14px] leading-relaxed">{text}</div>
    </div>
  );
}

/** One real engine event. Concise by default; technical detail on demand. */
function ActivityRow({ entry }: { entry: ConversationEntry }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(entry.detail) || entry.kind === "delegation";
  const icon = entry.kind === "delegation" ? <GitBranch className="size-3.5 text-accent" />
    : entry.kind === "security" ? <ShieldAlert className="size-3.5 text-err" />
    : LEVEL_ICON[entry.level ?? "info"];
  return (
    <div className="group">
      <button type="button" disabled={!hasDetail} onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 rounded-md px-1 py-1 text-left text-[13px] text-muted enabled:hover:bg-raised/60">
        {icon}
        <span className={cn("min-w-0 flex-1 truncate", entry.level === "error" && "text-err")}>{entry.title}</span>
        <span className="text-[11px] text-faint">{timeOf(entry.ts)}</span>
        {hasDetail && <ChevronRight className={cn("size-3.5 text-faint transition-transform", open && "rotate-90")} />}
      </button>
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
    <div className="rounded-xl border border-line bg-panel">
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
          <Button tone="primary" className="ml-auto" disabled={busy[`approve:${run.run_id}`]}
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
    <div className="rounded-xl border border-warn/40 bg-panel px-4 py-3">
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
    <div className="rounded-xl border border-line bg-panel px-4 py-3">
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
          <Button tone="primary" className="ml-auto" disabled={busy[`approve:${run.run_id}`]}
            onClick={() => void approve(run.run_id)}>Approve and finish</Button>
        </div>
      )}
    </div>
  );
}

export function Conversation() {
  const view = useApp((s) => s.view);
  const end = useRef<HTMLDivElement>(null);
  const count = view?.conversation.length ?? 0;
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [count]);

  if (!view) return <div className="flex-1" />;
  if (view.conversation.length === 0) {
    return (
      <div className="grid flex-1 place-items-center px-8 text-center">
        <div className="max-w-md">
          <div className="text-[18px] font-semibold">What should we build?</div>
          <p className="mt-2 text-[13px] text-muted">
            Describe the change. The engineering lead asks questions if something is unclear, proposes a plan for your
            approval, then routes tasks to Claude Code, Codex or Antigravity in isolated worktrees.
          </p>
        </div>
      </div>
    );
  }
  const lastRun = Object.values(view.runs).at(-1);
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-2 px-6 py-6">
        {view.conversation.map((e) => {
          const run = e.run_id ? view.runs[e.run_id] : undefined;
          if (e.role === "user") return <UserMessage key={e.id} entry={e} />;
          if (e.kind === "plan") return <PlanCard key={e.id} entry={e} run={run} />;
          if (e.kind === "review") return <ReviewCard key={e.id} entry={e} run={run} />;
          if (e.kind === "questions") return run ? <QuestionsCard key={e.id} run={run} /> : null;
          return <ActivityRow key={e.id} entry={e} />;
        })}
        {lastRun?.pending_approval === "escalation" && (
          <div className="rounded-xl border border-warn/40 bg-panel px-4 py-3 text-[13px]">
            The lead proposes changing an accepted decision. Review it with <code>saarathi escalations</code>; the
            approval screen arrives in a later milestone.
          </div>
        )}
        <div ref={end} />
      </div>
    </div>
  );
}

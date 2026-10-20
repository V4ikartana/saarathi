import { ArrowUp, Pause, Play, Square } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { latestRun, type RunSnapshot } from "../lib/types";
import { useApp } from "../store/app";
import { ProgressBar } from "./feedback";
import { Button, EXIT, Kbd, SOFT, StatePill, Switch } from "./primitives";
import { SessionControls } from "./SessionControls";

export function RunBar() {
  const view = useApp((s) => s.view);
  const run = latestRun(view);
  return (
    <AnimatePresence initial={false}>
      {run && !run.terminal && <RunBarBody key="runbar" run={run} />}
    </AnimatePresence>
  );
}

function RunBarBody({ run }: { run: RunSnapshot }) {
    const { stop, cancel, resume, busy } = useApp();
    const driving = run.driver.running;
    const waitingOnYou = run.pending_approval !== null || run.state === "CLARIFYING";
    const total = run.tasks.length;
    const done = run.tasks.filter((t) => t.state === "SUCCEEDED").length;
    const progress = run.state === "EXECUTING" && total ? (done / total) * 100 : run.state === "VERIFYING" ? 100 : null;
    return (
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6, transition: EXIT }}
        transition={SOFT} className="border-t border-line/70 px-5 pb-2 pt-2.5">
        <div className="flex items-center gap-2.5 text-[12px]">
          <StatePill state={run.state} />
          <span className="min-w-0 flex-1 truncate text-muted">
            {run.pause_reason ?? (waitingOnYou ? "Waiting for you" : driving ? "The engine is working" : "Idle")}
          </span>
          {total > 0 && <span className="tabular text-faint">{done}/{total} tasks</span>}
          <span className="flex gap-1.5">
            {driving && !waitingOnYou && (
              <Button tone="ghost" title="Finish in-flight attempts, then pause" loading={busy[`stop:${run.run_id}`]}
                onClick={() => void stop(run.run_id)}><Pause className="size-3.5" />Stop</Button>
            )}
            {!driving && !waitingOnYou && (
              <Button tone="ghost" loading={busy[`resume:${run.run_id}`]} onClick={() => void resume(run.run_id)}>
                <Play className="size-3.5" />Resume
              </Button>
            )}
            <Button tone="danger" title="Interrupt and cancel this run; worktrees are kept" loading={busy[`cancel:${run.run_id}`]}
              onClick={() => { if (confirm("Cancel this run? In-flight work is interrupted. Branches are kept.")) void cancel(run.run_id); }}>
              <Square className="size-3.5" />Cancel
            </Button>
          </span>
        </div>
        {driving && !waitingOnYou && <ProgressBar className="mt-2.5" value={progress} label="Run progress" />}
      </motion.div>
    );
}

export function Composer() {
  const { view, send, busy, draft, setDraft } = useApp();
  const [acceptDefaults, setAcceptDefaults] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const run = latestRun(view);
  const locked = Boolean(run && !run.terminal);
  useEffect(() => {
    if (draft && !locked) area.current?.focus(); // a suggestion or the palette filled the draft
  }, [draft, locked]);
  const submit = async () => {
    if (!draft.trim() || locked) return;
    if (await send(draft.trim(), acceptDefaults)) setDraft("");
  };
  return (
    <div className="px-6 pb-5 pt-2">
      <motion.div layout transition={SOFT}
        className="mx-auto max-w-3xl rounded-[20px] border border-line bg-panel elev-2 focus-within:border-accent/60 focus-within:ring-4 focus-within:ring-accent/10">
        <label htmlFor="composer" className="sr-only">Your request</label>
        <textarea id="composer" ref={area} value={draft} rows={3} disabled={locked}
          placeholder={locked ? "This session's run is still active. Answer, approve, stop or cancel it first." : "Describe the change you want…"}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit(); } }}
          className="w-full resize-none bg-transparent px-4 pt-3.5 text-[14px] leading-relaxed outline-none placeholder:text-faint focus-visible:outline-none disabled:opacity-60" />
        <SessionControls />
        <div className="flex items-center gap-3 border-t border-line/60 px-3 pb-2.5 pt-2">
          <label className="flex items-center gap-2 text-[12px] text-muted" title="Answer the lead's questions with its stated defaults">
            <Switch label="Accept the lead's default answers" checked={acceptDefaults} onChange={setAcceptDefaults} />
            Accept the lead's default answers
          </label>
          <span className="ml-auto text-[11px] text-faint"><Kbd>⌘</Kbd> <Kbd>Enter</Kbd></span>
          <Button tone="primary" className="size-9 rounded-full px-0" disabled={!draft.trim() || locked} loading={busy.send}
            onClick={() => void submit()} aria-label="Send">
            <ArrowUp className="size-4" />
          </Button>
        </div>
      </motion.div>
    </div>
  );
}

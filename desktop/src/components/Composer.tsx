import { ArrowUp, Pause, Play, Square } from "lucide-react";
import { useState } from "react";

import { latestRun } from "../lib/types";
import { useApp } from "../store/app";
import { Button, Kbd, StatePill } from "./primitives";
import { SessionControls } from "./SessionControls";

export function RunBar() {
  const { view, stop, cancel, resume, busy } = useApp();
  const run = latestRun(view);
  if (!run || run.terminal) return null;
  const driving = run.driver.running;
  const waitingOnYou = run.pending_approval !== null || run.state === "CLARIFYING";
  return (
    <div className="flex items-center gap-2 border-t border-line px-4 py-2 text-[12px]">
      <StatePill state={run.state} />
      <span className="truncate text-muted">
        {run.pause_reason ?? (waitingOnYou ? "Waiting for you" : driving ? "The engine is working" : "Idle")}
      </span>
      <span className="ml-auto flex gap-1.5">
        {driving && !waitingOnYou && (
          <Button tone="ghost" title="Finish in-flight attempts, then pause" disabled={busy[`stop:${run.run_id}`]}
            onClick={() => void stop(run.run_id)}><Pause className="size-3.5" />Stop</Button>
        )}
        {!driving && !waitingOnYou && (
          <Button tone="ghost" disabled={busy[`resume:${run.run_id}`]} onClick={() => void resume(run.run_id)}>
            <Play className="size-3.5" />Resume
          </Button>
        )}
        <Button tone="danger" title="Interrupt and cancel this run; worktrees are kept" disabled={busy[`cancel:${run.run_id}`]}
          onClick={() => { if (confirm("Cancel this run? In-flight work is interrupted. Branches are kept.")) void cancel(run.run_id); }}>
          <Square className="size-3.5" />Cancel
        </Button>
      </span>
    </div>
  );
}

export function Composer() {
  const { view, send, busy } = useApp();
  const [text, setText] = useState("");
  const [acceptDefaults, setAcceptDefaults] = useState(false);
  const run = latestRun(view);
  const locked = Boolean(run && !run.terminal);
  const submit = async () => {
    if (!text.trim() || locked) return;
    if (await send(text.trim(), acceptDefaults)) setText("");
  };
  return (
    <div className="px-6 pb-5 pt-2">
      <div className="mx-auto max-w-3xl rounded-2xl border border-line bg-panel shadow-lg shadow-black/10 focus-within:border-accent/60 focus-within:ring-4 focus-within:ring-accent/10">
        <textarea value={text} rows={3} disabled={locked}
          placeholder={locked ? "This session's run is still active. Answer, approve, stop or cancel it first." : "Describe the change you want…"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit(); } }}
          className="w-full resize-none bg-transparent px-4 pt-3 text-[14px] outline-none placeholder:text-faint disabled:opacity-60" />
        <SessionControls />
        <div className="flex items-center gap-3 border-t border-line/60 px-3 pb-2.5 pt-2">
          <label className="flex items-center gap-1.5 text-[12px] text-muted" title="Answer the lead's questions with its stated defaults">
            <input type="checkbox" checked={acceptDefaults} onChange={(e) => setAcceptDefaults(e.target.checked)} />
            Accept the lead's default answers
          </label>
          <span className="ml-auto text-[11px] text-faint"><Kbd>⌘</Kbd> <Kbd>Enter</Kbd></span>
          <Button tone="primary" disabled={!text.trim() || locked || busy.send} onClick={() => void submit()} aria-label="Send">
            <ArrowUp className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

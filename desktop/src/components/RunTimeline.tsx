// The run as the engine recorded it: the task graph by dependency depth, then every attempt on a time axis.
import { useEffect, useMemo, useState } from "react";

import type { Timeline } from "../lib/types";
import { latestRun } from "../lib/types";
import { cn, durationMs, fmtDuration, OUTCOME_TONE, stateTone } from "../lib/ui";
import { useBridge } from "../lib/useBridge";
import { useApp } from "../store/app";
import { Skeleton } from "./feedback";
import { StatePill } from "./primitives";

const NODE_W = 196;
const NODE_H = 54;
const COL_GAP = 56;
const ROW_GAP = 14;
const PAD = 16;
const ARC = 34;

const BAR = {
  ok: "bg-ok/70",
  err: "bg-err/70",
  warn: "bg-warn/70",
  busy: "bg-accent/80 animate-pulse",
  idle: "bg-faint/60",
};
const BORDER = {
  ok: "border-ok/50",
  err: "border-err/60",
  warn: "border-warn/60",
  busy: "border-accent/70",
  idle: "border-line",
};

function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function Graph({ data, onOpen }: { data: Timeline; onOpen: (taskId: string) => void }) {
  const layout = useMemo(() => {
    const columns = new Map<number, string[]>();
    for (const t of data.tasks) columns.set(t.depth, [...(columns.get(t.depth) ?? []), t.id]);
    const pos = new Map<string, { x: number; y: number }>();
    for (const [depth, ids] of columns) {
      ids.forEach((id, i) => pos.set(id, { x: PAD + depth * (NODE_W + COL_GAP), y: PAD + i * (NODE_H + ROW_GAP) }));
    }
    const depthCount = Math.max(0, ...columns.keys()) + 1;
    const rows = Math.max(1, ...[...columns.values()].map((c) => c.length));
    const depth = new Map(data.tasks.map((t) => [t.id, t.depth]));
    // An edge that skips a column would run behind the nodes in between, so it arcs below them instead.
    const skips = data.tasks.some((t) => t.dependencies.some((d) => t.depth - (depth.get(d) ?? t.depth) > 1));
    const body = PAD * 2 + rows * NODE_H + (rows - 1) * ROW_GAP;
    return {
      pos,
      depth,
      bottom: body - PAD,
      width: PAD * 2 + depthCount * NODE_W + (depthCount - 1) * COL_GAP,
      height: body + (skips ? ARC : 0),
    };
  }, [data.tasks]);

  return (
    <div className="overflow-x-auto">
      <div className="relative" style={{ width: layout.width, height: layout.height }}>
        <svg className="absolute inset-0" width={layout.width} height={layout.height} aria-hidden>
          {data.tasks.flatMap((t) =>
            t.dependencies.map((d) => {
              const a = layout.pos.get(d);
              const b = layout.pos.get(t.id);
              if (!a || !b) return null;
              const skip = t.depth - (layout.depth.get(d) ?? t.depth) > 1;
              const path = skip
                ? (() => {
                    const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H, x2 = b.x + NODE_W / 2, y2 = b.y + NODE_H + 4;
                    const low = layout.bottom + ARC - 6;
                    return `M${x1},${y1} C${x1},${low} ${x2},${low} ${x2},${y2}`;
                  })()
                : (() => {
                    const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
                    const mid = (x1 + x2) / 2;
                    return `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2 - 4},${y2}`;
                  })();
              return (
                <path key={`${d}-${t.id}`} d={path} fill="none" stroke="var(--color-line)" strokeWidth={1.5} markerEnd="url(#arrow)" />
              );
            }),
          )}
          <defs>
            <marker id="arrow" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="8" markerHeight="8" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" fill="var(--color-line)" />
            </marker>
          </defs>
        </svg>
        {data.tasks.map((t) => {
          const p = layout.pos.get(t.id);
          if (!p) return null;
          return (
            <button key={t.id} type="button" onClick={() => onOpen(t.id)} title={t.title}
              style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}
              className={cn("absolute flex flex-col justify-center rounded-lg border bg-panel px-2.5 text-left hover:bg-raised", BORDER[stateTone(t.state)])}>
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-[11px] text-faint">{t.id}</span>
                <StatePill state={t.state} className="ml-auto" />
              </div>
              <div className="truncate text-[12px]">{t.title}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Bars({ data, now, onOpen }: { data: Timeline; now: number; onOpen: (taskId: string) => void }) {
  const starts = data.attempts.map((a) => Date.parse(a.started_at)).filter((n) => !Number.isNaN(n));
  if (starts.length === 0) return <p className="px-4 py-6 text-[13px] text-faint">No attempts yet. They appear here as tasks start.</p>;
  const t0 = Math.min(...starts);
  const ends = data.attempts.map((a) => (a.finished_at ? Date.parse(a.finished_at) : now));
  const span = Math.max(1000, Math.max(...ends) - t0);
  const ticks = 4;
  const ordered = [...data.tasks].sort((a, b) => a.depth - b.depth || a.id.localeCompare(b.id));
  return (
    <div className="px-4 pb-4">
      <div className="ml-40 flex justify-between pb-1 text-[10px] text-faint">
        {Array.from({ length: ticks + 1 }, (_, i) => <span key={i}>{fmtDuration((span * i) / ticks)}</span>)}
      </div>
      {ordered.map((t) => {
        const attempts = data.attempts.filter((a) => a.task_id === t.id);
        return (
          <div key={t.id} className="flex h-8 items-center border-t border-line/60">
            <button type="button" onClick={() => onOpen(t.id)} className="w-40 shrink-0 truncate pr-2 text-left text-[12px] hover:text-accent">
              <span className="font-mono text-faint">{t.id}</span> {t.title}
            </button>
            <div className="relative h-4 flex-1 rounded bg-raised/50">
              {attempts.map((a) => {
                const start = Date.parse(a.started_at) - t0;
                const length = durationMs(a.started_at, a.finished_at, now);
                const tone = a.status === "running" ? "busy" : (OUTCOME_TONE[a.outcome ?? ""] ?? "idle");
                return (
                  <button key={a.id} type="button" onClick={() => onOpen(t.id)}
                    title={`Attempt ${a.number} · ${a.adapter}:${a.model} · ${a.outcome ?? a.status} · ${fmtDuration(length)}`}
                    style={{ left: `${(start / span) * 100}%`, width: `max(4px, ${(length / span) * 100}%)` }}
                    className={cn("absolute inset-y-0 rounded", BAR[tone])} />
                );
              })}
            </div>
          </div>
        );
      })}
      <div className="mt-3 flex flex-wrap gap-4 text-[11px] text-muted">
        <span><span className={cn("mr-1 inline-block size-2 rounded-sm", BAR.ok)} />passed</span>
        <span><span className={cn("mr-1 inline-block size-2 rounded-sm", BAR.err)} />failed</span>
        <span><span className={cn("mr-1 inline-block size-2 rounded-sm", BAR.warn)} />escalated or stopped</span>
        <span><span className="mr-1 inline-block size-2 rounded-sm bg-accent/80" />running</span>
      </div>
    </div>
  );
}

export function RunTimeline() {
  const { view, project, openTask } = useApp();
  const run = latestRun(view);
  const timeline = useBridge<Timeline>("runs.timeline", run && project ? { project: project.root, run: run.run_id } : null);
  const data = timeline.data;
  const running = Boolean(data?.attempts.some((a) => a.status === "running"));
  // Server time at fetch plus local elapsed time, so running bars grow between engine events.
  const [fetchedAt, setFetchedAt] = useState(Date.now());
  useEffect(() => setFetchedAt(Date.now()), [data]);
  const local = useNow(running);
  const now = data ? Date.parse(data.now) + (local - fetchedAt) : local;

  if (!run) return <div className="grid flex-1 place-items-center text-[13px] text-faint">No run in this session yet.</div>;
  if (!data) {
    return timeline.error ? (
      <div className="grid flex-1 place-items-center text-[13px] text-err">{timeline.error.message}</div>
    ) : (
      <div className="mx-auto w-full max-w-5xl space-y-6 px-4 py-6" role="status" aria-busy="true" aria-label="Loading the timeline">
        <div className="flex gap-14">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14 w-48 rounded-xl" />)}</div>
        {[0, 1, 2].map((i) => <div key={i} className="flex gap-4"><Skeleton className="w-36" /><Skeleton className="h-4 flex-1 rounded-full" /></div>)}
      </div>
    );
  }
  if (data.tasks.length === 0) {
    return <div className="grid flex-1 place-items-center text-[13px] text-faint">Tasks appear here once the plan is approved.</div>;
  }
  const open = (taskId: string) => openTask(data.run_id, taskId);
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl py-4">
        <h2 className="px-4 text-[11px] font-medium uppercase tracking-wide text-faint">Task graph</h2>
        <Graph data={data} onOpen={open} />
        <h2 className="mt-4 px-4 pb-2 text-[11px] font-medium uppercase tracking-wide text-faint">Attempts over time</h2>
        <Bars data={data} now={now} onOpen={open} />
      </div>
    </div>
  );
}

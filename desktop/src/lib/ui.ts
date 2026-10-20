import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export const STATE_LABEL: Record<string, string> = {
  CREATED: "Starting", INTAKE: "Reading request", CLARIFYING: "Questions", PLANNING: "Planning",
  AWAITING_PLAN_APPROVAL: "Plan ready", EXECUTING: "Executing", VERIFYING: "Verifying",
  AWAITING_REVIEW: "Review", READY_TO_SHIP: "Approved", COMPLETED: "Completed", PAUSED: "Paused",
  FAILED: "Failed", CANCELLED: "Cancelled",
  PENDING: "Pending", READY: "Ready", LEASED: "Assigned", RUNNING: "Running", SUCCEEDED: "Passed",
  BLOCKED: "Blocked", RETRY_WAIT: "Retrying",
};

export function stateTone(state: string | null | undefined): "ok" | "warn" | "err" | "busy" | "idle" {
  switch (state) {
    case "COMPLETED": case "SUCCEEDED": case "READY_TO_SHIP": return "ok";
    case "FAILED": return "err";
    case "PAUSED": case "BLOCKED": case "CANCELLED": case "RETRY_WAIT": return "warn";
    case "AWAITING_PLAN_APPROVAL": case "AWAITING_REVIEW": case "CLARIFYING": return "warn";
    case null: case undefined: case "PENDING": return "idle";
    default: return "busy";
  }
}

export function timeOf(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function durationMs(start: string, end: string | null, now = Date.now()): number {
  const a = Date.parse(start);
  const b = end ? Date.parse(end) : now;
  return Number.isNaN(a) || Number.isNaN(b) ? 0 : Math.max(0, b - a);
}

export function fmtDuration(ms: number): string {
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Token counts as the provider reported them; null stays "not reported", never zero. */
export function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return "not reported";
  return n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString();
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 8) : "";
}

export const OUTCOME_TONE: Record<string, "ok" | "warn" | "err" | "busy" | "idle"> = {
  succeeded: "ok",
  failed: "err",
  escalated: "warn",
  cancelled: "warn",
  orphaned: "warn",
};

/** "in 4 h 12 min", "in 3 days" or "" for a reset time; never invents one. */
export function resetsIn(iso: string | null, now = Date.now()): string {
  if (!iso) return "";
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "";
  const minutes = Math.max(0, Math.round((at - now) / 60_000));
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `in ${hours} h ${minutes % 60} min`;
  return `in ${Math.round(hours / 24)} days`;
}

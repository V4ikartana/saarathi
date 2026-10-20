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

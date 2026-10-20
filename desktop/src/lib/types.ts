// Shapes returned by `saarathi bridge --stdio`, protocol 1 (docs/desktop/architecture.md §4).
// The UI renders these; it never derives task state, eligibility or routing on its own.

export type RunState =
  | "CREATED" | "INTAKE" | "CLARIFYING" | "PLANNING" | "AWAITING_PLAN_APPROVAL" | "EXECUTING" | "VERIFYING"
  | "AWAITING_REVIEW" | "READY_TO_SHIP" | "COMPLETED" | "PAUSED" | "FAILED" | "CANCELLED";

export interface BridgeInfo {
  protocol: number;
  saarathi_version: string;
  python: string;
  platform: string;
  pid: number;
  data_dir: string;
}

export interface ProjectInfo {
  root: string;
  name: string;
  initialized: boolean;
  project_id?: string;
  mode?: string;
  lead?: string;
  active_run?: string | null;
  driver_pid?: number | null;
}

export interface SessionSummary {
  id: string;
  project_id: string;
  title: string;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
  run_count: number;
  last_run: string | null;
  last_state: RunState | null;
}

export interface ConversationEntry {
  id: string;
  kind: string; // message | lead | questions | plan | phase | delegation | attempt | task | escalation | review | ...
  role: "user" | "system" | "orchestrator";
  ts: string;
  run_id: string | null;
  task_id?: string | null;
  level?: "info" | "warn" | "error" | "success";
  title: string;
  detail?: string | null;
  data: Record<string, unknown>;
  event_type?: string;
}

export interface TaskStatus {
  id: string;
  title: string;
  state: string;
  attempts: number;
  failures: number;
  dependencies: string[];
  worker: string | null;
  provider: string | null;
  transport: string | null;
  blocked_reason: string | null;
  failure_reason: string | null;
}

export interface Question {
  id: string;
  question: string;
  why_it_matters: string;
  default_assumption: string;
  answer: string | null;
}

export interface PlannedTask {
  id: string;
  title: string;
  objective: string;
  dependencies: string[];
  allowed_paths: string[];
  verification_commands: string[];
  complexity: string;
  risk: string;
}

export interface RunSnapshot {
  run_id: string;
  state: RunState;
  pause_reason: string | null;
  paused_from: string | null;
  base_commit: string | null;
  integration_branch: string | null;
  open_questions: string[];
  tasks: TaskStatus[];
  pending_approval: "plan" | "review" | "escalation" | null;
  questions: Question[];
  plan: { summary: string; architecture_notes: string; tasks: PlannedTask[]; integration_checks: string[] } | null;
  review: { decision: string; summary: string; concerns: string[] } | null;
  integration: { branch?: string; passed?: boolean; checks?: { command: string; exit_code: number | null }[] } | null;
  lead: { adapter?: string; model?: string; provider?: string } | null;
  driver: { running: boolean; pid: number | null };
  terminal: boolean;
}

export interface SessionView {
  session: SessionSummary;
  conversation: ConversationEntry[];
  runs: Record<string, RunSnapshot>;
}

export interface BridgeErrorData {
  code: number;
  message: string;
  data?: { kind?: string; hint?: string | null };
}

export interface EventsAppended {
  protocol: number;
  project: string;
  events: { seq: number; run_id: string | null; type: string }[];
  runs: string[];
}

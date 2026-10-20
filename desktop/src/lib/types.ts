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
  preferences: Partial<SessionPreferences>;
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
  /** Oldest first. Object key order is not preserved across the bridge, so never infer order from `runs`. */
  run_order: string[];
}

/** The session's most recent run, or undefined before its first request. */
export function latestRun(view: SessionView | null | undefined): RunSnapshot | undefined {
  const id = view?.run_order.at(-1);
  return id ? view?.runs[id] : undefined;
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

// ---- M3: task inspection (tasks.inspect, tasks.diff, tasks.file, tasks.logs, runs.timeline)

export interface Usage {
  input_tokens: number | null;
  cached_input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  cost_is_estimate: boolean;
  reported_model: string | null;
}

export interface CommandEvidence {
  command: string;
  exit_code: number | null;
  duration_s: number;
  timed_out: boolean;
  output_tail: string;
  log_path: string | null;
  rejected_reason: string | null;
}

export interface WorkerReport {
  status: "succeeded" | "failed" | "blocked";
  summary: string;
  changed_files: string[];
  tests_run: string[];
  assumptions: string[];
  unresolved_risks: string[];
  memory_proposals: string[];
}

/** Saarathi's own assessment of an attempt, built from Git state and verification. */
export interface WorkerResult {
  status: "succeeded" | "failed";
  failure_reason: string | null;
  adapter: string;
  provider: string;
  model: string;
  commit: string | null;
  changed_files: string[];
  scope_violations: string[];
  checkout_modified: boolean;
  verification: CommandEvidence[];
  worker_report: WorkerReport | null;
  usage: Usage;
  error_kind: string | null;
  received_inputs: string[];
}

export interface Attempt {
  id: string;
  task_id: string;
  number: number;
  status: string; // running | finished | abandoned
  outcome: string | null; // succeeded | failed | escalated | cancelled | orphaned
  adapter: string;
  provider: string;
  model: string;
  transport: string;
  routing_decision_id: string;
  base_commit: string;
  feedback: string | null;
  result: Partial<WorkerResult> | null;
  started_at: string;
  finished_at: string | null;
}

export interface RoutingDecision {
  decision_id: string;
  task_id: string;
  chosen: string | null;
  provider: string | null;
  model: string | null;
  adapter: string | null;
  transport: string | null;
  reason: string;
  score_breakdown: Record<string, number>;
  alternatives: string[];
  cooling: string[];
  available_at: string | null;
  busy: string[];
  rejected: { candidate: string; reasons: string[] }[];
  estimated_cost_usd: number | null;
  awaiting: "account_selection" | "approval" | null;
  integration: Record<string, unknown> | null;
}

export interface TaskInspection {
  task: PlannedTask & {
    acceptance_criteria: string[];
    required_capabilities: string[];
    max_attempts: number;
    state: string;
    attempts: number;
    failures: number;
    branch: string | null;
    worktree: string | null;
    blocked_reason: string | null;
    result: { commit?: string; failure_reason?: string } | null;
    updated_at: string;
  };
  attempts: Attempt[];
  decisions: RoutingDecision[];
}

export interface DiffFile {
  path: string;
  added: number | null;
  deleted: number | null;
  binary: boolean;
}

export interface TaskDiff {
  commit: string | null;
  base: string | null;
  diff: string;
  files: DiffFile[];
}

export interface FileVersions {
  path: string;
  binary: boolean;
  original: string;
  modified: string;
  truncated: boolean;
}

export interface EngineEvent {
  seq: number;
  ts: string;
  actor: string;
  type: string;
  task_id: string | null;
  payload: Record<string, unknown>;
}

export interface TaskLogs {
  logs: { name: string; text: string; truncated: boolean }[];
  events: EngineEvent[];
}

export interface Timeline {
  run_id: string;
  state: RunState;
  created_at: string;
  updated_at: string;
  now: string;
  tasks: { id: string; title: string; state: string; dependencies: string[]; depth: number }[];
  attempts: Pick<Attempt, "id" | "task_id" | "number" | "status" | "outcome" | "adapter" | "model" | "started_at" | "finished_at">[];
}

// ---- M4: preferences, policy, usage, providers (sessions.set_preferences, policy.get, usage.summary, models.list)

export type ApprovalPreset = "ask" | "auto_safe" | "bypass";
export type ModelMode = "prefer" | "pin";

export interface SessionPreferences {
  approvals: ApprovalPreset;
  model: string | null;
  model_mode: ModelMode;
}

export interface ModelInfo {
  candidate: string;
  provider: string;
  adapter: string;
  model: string;
  transport: string;
  available: boolean;
  unavailable_reason: string | null;
  reasoning_tier: string;
  metered: boolean;
  source: string;
  provider_limit: string | null;
  disabled: boolean; // turned off by this person in Settings, in every project
}

export interface PresetInfo {
  id: ApprovalPreset;
  label: string;
  description: string;
  allowed: boolean;
}

export interface WorkerPolicy {
  adapter: string;
  provider: string;
  enabled: boolean;
  models: string[];
  isolation: string;
  worker_command: string | null;
}

export interface UnconfiguredCli {
  adapter: string;
  provider: string;
  binary: string;
  steps: string[];
}

export interface PolicyView {
  path: string;
  unconfigured_clis: UnconfiguredCli[];
  mode: string;
  lead: string;
  lead_substitute: string | null;
  approvals: { max_preset: ApprovalPreset; presets: PresetInfo[]; never_automatic: string[] };
  permissions: Record<string, boolean>;
  routing: {
    allowed_providers: string[];
    allowed_models: string[];
    denied_models: string[];
    max_parallel_workers: number;
    escalate_after_failures: number;
    spread_across_providers: boolean;
  };
  integrations: Record<string, unknown>;
  execution: Record<string, unknown>;
  workers: WorkerPolicy[];
}

export interface UsageRow {
  calls: number;
  succeeded: number;
  failed: number;
  other: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  unreported: number;
  cost_usd: number | null;
  cost_reported: number;
  cost_is_estimate: boolean;
  duration_s: number;
}

export interface UsageSummary {
  generated_at: string;
  quota_note: string;
  totals: UsageRow;
  by_model: (UsageRow & { role: "worker" | "lead"; candidate: string; adapter: string; model: string })[];
  runs: (UsageRow & { run_id: string; state: RunState; created_at: string })[];
  limits: { adapter: string; state: string; until: string; reason: string }[];
}

export interface DoctorReport {
  adapters: {
    name: string;
    type: string;
    provider: string;
    available: boolean;
    version: string | null;
    auth_method: string | null;
    metered: boolean | null;
    reason: string | null;
    test_only: boolean;
    checks: { name: string; ok: boolean; detail: string }[];
    notices: string[];
  }[];
  lead: string | null;
  lead_problems: string[];
}

export interface BridgeDiagnostics {
  running: boolean;
  command: string;
  stderr_tail: string[] | string;
}

export interface LimitWindow {
  label: string;
  used_percent: number;
  remaining_percent: number;
  resets_at: string | null;
  resets_text: string | null;
  window: string | null;
}

export interface ProviderLimits {
  adapter: string;
  source: string;
  plan: string | null;
  error: string | null;
  checked_at: string;
  windows: LimitWindow[];
}

// Project-level pages. Usage and permissions are read-only views of engine records and the policy file; settings
// changes only per-viewer conveniences (theme, defaults for new sessions). The policy itself is edited in its file.
import { CheckCircle2, CircleAlert, RefreshCw, XCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { call } from "../lib/bridge";
import { loadDefaultPreferences, saveDefaultPreferences, type Theme } from "../lib/theme";
import type {
  ApprovalPreset,
  BridgeDiagnostics,
  DoctorReport,
  PolicyView,
  SessionPreferences,
  UsageRow,
  UsageSummary,
} from "../lib/types";
import { cn, fmtDuration, fmtTokens, timeOf } from "../lib/ui";
import { useBridge, type Query } from "../lib/useBridge";
import { useApp } from "../store/app";
import { Button, StatePill } from "./primitives";

function PageShell({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-6 py-6">
        <div className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-[18px] font-semibold">{title}</h1>
            {subtitle && <p className="mt-1 text-[13px] text-muted">{subtitle}</p>}
          </div>
          {actions}
        </div>
        <div className="mt-6 space-y-6">{children}</div>
      </div>
    </div>
  );
}

function Card({ title, children, className }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-xl border border-line bg-panel", className)}>
      {title && <h2 className="border-b border-line px-4 py-2.5 text-[11px] font-medium uppercase tracking-wide text-faint">{title}</h2>}
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

function Loaded<T>({ query, children }: { query: Query<T>; children: (data: T) => ReactNode }) {
  if (query.data !== undefined) return <>{children(query.data)}</>;
  return <p className="text-[13px] text-faint">{query.error ? query.error.message : "Loading…"}</p>;
}

function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[12px]">{children}</span>;
}

// ---------------------------------------------------------------- usage

function cost(row: UsageRow): string {
  if (row.cost_usd === null) return "not reported";
  return `${row.cost_is_estimate ? "≈ " : ""}$${row.cost_usd.toFixed(4)}`;
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-faint">{label}</div>
      <div className="mt-0.5 text-[18px] font-semibold tabular-nums">{value}</div>
      {note && <div className="text-[11px] text-faint">{note}</div>}
    </div>
  );
}

export function UsagePage() {
  const project = useApp((s) => s.project);
  const usage = useBridge<UsageSummary>("usage.summary", project ? { project: project.root } : null);
  return (
    <PageShell title="Usage" subtitle="Token counts and costs exactly as each provider reported them, for this project's runs.">
      <Loaded query={usage}>
        {(u) => (
          <>
            <div className="flex items-start gap-3 rounded-lg border border-line bg-raised/50 px-4 py-3 text-[13px] text-muted">
              <CircleAlert className="mt-0.5 size-4 shrink-0" />
              <span>{u.quota_note} Costs, where shown, are what the provider or adapter recorded; "≈" marks client-side estimates.</span>
            </div>
            <Card>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat label="Calls" value={String(u.totals.calls)} note={`${u.totals.succeeded} passed · ${u.totals.failed} failed`} />
                <Stat label="Input tokens" value={fmtTokens(u.totals.input_tokens)} note={u.totals.cached_input_tokens ? `${fmtTokens(u.totals.cached_input_tokens)} cached` : undefined} />
                <Stat label="Output tokens" value={fmtTokens(u.totals.output_tokens)} note={u.totals.unreported ? `${u.totals.unreported} calls reported none` : undefined} />
                <Stat label="Cost" value={cost(u.totals)} note={u.totals.cost_reported ? `${u.totals.cost_reported} calls reported a cost` : undefined} />
              </div>
            </Card>
            {u.limits.length > 0 && (
              <Card title="Provider limits in effect">
                <ul className="space-y-1 text-[13px]">
                  {u.limits.map((l) => (
                    <li key={l.adapter} className="text-warn"><Mono>{l.adapter}</Mono> {l.state.replace("_", " ")} until {timeOf(l.until)} · {l.reason}</li>
                  ))}
                </ul>
              </Card>
            )}
            <Card title="By model">
              {u.by_model.length === 0 ? <p className="text-[13px] text-faint">No calls yet.</p> : (
                <table className="w-full text-[12px]">
                  <thead className="text-left text-faint">
                    <tr><th className="pb-1 font-normal">Model</th><th className="pb-1 font-normal">Role</th>
                      <th className="pb-1 text-right font-normal">Calls</th><th className="pb-1 text-right font-normal">Input</th>
                      <th className="pb-1 text-right font-normal">Output</th><th className="pb-1 text-right font-normal">Cost</th>
                      <th className="pb-1 text-right font-normal">Time</th></tr>
                  </thead>
                  <tbody>
                    {u.by_model.map((m) => (
                      <tr key={`${m.role}-${m.candidate}`} className="border-t border-line/60">
                        <td className="py-1.5"><Mono>{m.candidate}</Mono></td>
                        <td className="py-1.5 text-muted">{m.role}</td>
                        <td className="py-1.5 text-right tabular-nums">{m.calls}<span className="text-faint"> ({m.succeeded}✓ {m.failed}✗)</span></td>
                        <td className="py-1.5 text-right tabular-nums">{m.unreported === m.calls ? <span className="text-faint">not reported</span> : fmtTokens(m.input_tokens)}</td>
                        <td className="py-1.5 text-right tabular-nums">{m.unreported === m.calls ? <span className="text-faint">not reported</span> : fmtTokens(m.output_tokens)}</td>
                        <td className="py-1.5 text-right tabular-nums">{cost(m)}</td>
                        <td className="py-1.5 text-right tabular-nums text-muted">{m.role === "lead" ? "—" : fmtDuration(m.duration_s * 1000)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
            <Card title="Recent runs">
              {u.runs.length === 0 ? <p className="text-[13px] text-faint">No runs yet.</p> : (
                <ul className="divide-y divide-line/60">
                  {u.runs.map((r) => (
                    <li key={r.run_id} className="flex items-center gap-3 py-1.5 text-[12px]">
                      <StatePill state={r.state} />
                      <Mono>{r.run_id}</Mono>
                      <span className="text-faint">{new Date(r.created_at).toLocaleString()}</span>
                      <span className="ml-auto tabular-nums text-muted">
                        {r.calls} calls · in {fmtTokens(r.input_tokens)} · out {fmtTokens(r.output_tokens)} · {cost(r)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </>
        )}
      </Loaded>
    </PageShell>
  );
}

// ---------------------------------------------------------------- permissions

function Flag({ on, label }: { on: boolean; label: string }) {
  return (
    <li className="flex items-center gap-2 text-[13px]">
      {on ? <CheckCircle2 className="size-4 text-warn" /> : <XCircle className="size-4 text-faint" />}
      <span className={on ? "" : "text-muted"}>{label}: {on ? "allowed" : "not allowed"}</span>
    </li>
  );
}

const PERMISSION_LABELS: Record<string, string> = {
  allow_network: "Network access for workers",
  allow_dependency_install: "Installing dependencies",
  allow_deployment: "Deployment",
};

export function PermissionsPage() {
  const project = useApp((s) => s.project);
  const policy = useBridge<PolicyView>("policy.get", project ? { project: project.root } : null, { live: false });
  return (
    <PageShell title="Permissions"
      subtitle={<>What Saarathi and its workers may do in this project. Read-only here: the policy owner edits <Mono>{policy.data?.path ?? ".saarathi/policies.yaml"}</Mono>, and changes take effect on the next request.</>}>
      <Loaded query={policy}>
        {(p) => (
          <>
            <Card title="Approvals">
              <p className="text-[13px] text-muted">Each session picks a preset. This project allows up to <b className="text-ink">{p.approvals.presets.find((x) => x.id === p.approvals.max_preset)?.label}</b>.</p>
              <ul className="mt-3 space-y-2">
                {p.approvals.presets.map((x) => (
                  <li key={x.id} className={cn("rounded-lg border px-3 py-2", x.allowed ? "border-line" : "border-line/50 opacity-60")}>
                    <div className="flex items-center gap-2 text-[13px] font-medium">{x.label}
                      {!x.allowed && <span className="text-[11px] font-normal text-faint">not allowed by approvals.max_preset</span>}</div>
                    <p className="mt-0.5 text-[12px] text-muted">{x.description}</p>
                  </li>
                ))}
              </ul>
              <div className="mt-3 text-[12px] text-muted">Always needs a person, under every preset:</div>
              <ul className="mt-1 list-disc pl-5 text-[12px] text-muted">{p.approvals.never_automatic.map((n) => <li key={n}>{n}</li>)}</ul>
            </Card>
            <Card title="Workers and their containment">
              <ul className="space-y-4">
                {p.workers.map((w) => (
                  <li key={w.adapter}>
                    <div className="flex items-center gap-2 text-[13px]">
                      <Mono>{w.adapter}</Mono><span className="text-faint">{w.provider}</span>
                      {!w.enabled && <span className="rounded bg-raised px-1.5 text-[11px] text-faint">disabled</span>}
                      <span className="ml-auto text-[11px] text-faint">models: {w.models.join(", ")}</span>
                    </div>
                    <p className="mt-1 text-[12px] text-muted">{w.isolation}</p>
                    {w.worker_command ? (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-[12px] text-faint">Exact command for a worker task</summary>
                        <pre className="mt-1 overflow-x-auto rounded-md bg-raised px-3 py-2 font-mono text-[11px] whitespace-pre-wrap break-all text-muted">{w.worker_command}</pre>
                      </details>
                    ) : <p className="mt-1 text-[12px] text-faint">No external command (CLI not installed, or a test adapter).</p>}
                  </li>
                ))}
              </ul>
            </Card>
            <div className="grid gap-6 md:grid-cols-2">
              <Card title="Project permissions">
                <ul className="space-y-1.5">
                  {Object.entries(PERMISSION_LABELS).map(([k, label]) => <Flag key={k} on={Boolean(p.permissions[k])} label={label} />)}
                </ul>
                <p className="mt-2 text-[12px] text-muted">Saarathi never merges into your branches; finished work stays on its own branch.</p>
              </Card>
              <Card title="Routing">
                <dl className="space-y-1 text-[12px]">
                  <div><dt className="inline text-faint">Lead: </dt><dd className="inline"><Mono>{p.lead}</Mono>{p.lead_substitute ? <> (substitute <Mono>{p.lead_substitute}</Mono>)</> : " (no substitute: pauses if unavailable)"}</dd></div>
                  <div><dt className="inline text-faint">Providers: </dt><dd className="inline">{p.routing.allowed_providers.join(", ")}</dd></div>
                  <div><dt className="inline text-faint">Models: </dt><dd className="inline"><Mono>{p.routing.allowed_models.join("  ")}</Mono></dd></div>
                  {p.routing.denied_models.length > 0 && <div><dt className="inline text-faint">Denied: </dt><dd className="inline"><Mono>{p.routing.denied_models.join("  ")}</Mono></dd></div>}
                  <div><dt className="inline text-faint">Parallel workers: </dt><dd className="inline">{p.routing.max_parallel_workers}</dd></div>
                  <div><dt className="inline text-faint">Attempts per task: </dt><dd className="inline">{String(p.execution.max_attempts)}</dd></div>
                </dl>
              </Card>
            </div>
            <Card title="Integrations">
              <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-[12px] sm:grid-cols-2">
                {Object.entries(p.integrations).map(([k, v]) => (
                  <div key={k}><dt className="inline text-faint">{k.replaceAll("_", " ")}: </dt>
                    <dd className="inline font-mono">{Array.isArray(v) ? (v.length ? v.join(", ") : "none") : String(v)}</dd></div>
                ))}
              </dl>
            </Card>
          </>
        )}
      </Loaded>
    </PageShell>
  );
}

// ---------------------------------------------------------------- settings

export function SettingsPage() {
  const { project, bridge, theme, setTheme } = useApp();
  const root = project?.root ?? "";
  const policy = useBridge<PolicyView>("policy.get", root ? { project: root } : null, { live: false });
  const [doctor, setDoctor] = useState<{ data?: DoctorReport; error?: string; loading: boolean }>({ loading: false });
  const [diag, setDiag] = useState<BridgeDiagnostics | null>(null);
  const [defaults, setDefaults] = useState<SessionPreferences>(loadDefaultPreferences);

  const runDoctor = async () => {
    setDoctor({ loading: true });
    try {
      setDoctor({ data: await call<DoctorReport>("system.doctor", { project: root }), loading: false });
    } catch (err) {
      setDoctor({ error: err instanceof Error ? err.message : String(err), loading: false });
    }
  };
  useEffect(() => {
    void import("@tauri-apps/api/core")
      .then(({ invoke }) => invoke<BridgeDiagnostics>("bridge_diagnostics"))
      .then(setDiag)
      .catch(() => setDiag(null));
  }, []);
  const updateDefaults = (next: Partial<SessionPreferences>) => {
    const merged = { ...defaults, ...next };
    setDefaults(merged);
    saveDefaultPreferences(merged);
  };

  return (
    <PageShell title="Settings" subtitle="Preferences for this app on this computer. Project rules live in the policy file (see Permissions).">
      <Card title="Appearance">
        <div className="flex gap-1 rounded-md bg-raised p-0.5 text-[13px]" role="radiogroup" aria-label="Theme">
          {(["system", "light", "dark"] as Theme[]).map((t) => (
            <button key={t} type="button" role="radio" aria-checked={theme === t} onClick={() => setTheme(t)}
              className={cn("flex-1 rounded px-3 py-1 capitalize", theme === t ? "bg-panel text-ink shadow-sm" : "text-muted hover:text-ink")}>{t}</button>
          ))}
        </div>
      </Card>
      <Card title="Defaults for new sessions">
        <label className="flex items-center gap-3 text-[13px]">
          <span className="w-32 text-muted">Approvals</span>
          <select className="h-8 rounded-md border border-line bg-bg px-2 text-[13px]" value={defaults.approvals}
            onChange={(e) => updateDefaults({ approvals: e.target.value as ApprovalPreset })}>
            {(policy.data?.approvals.presets ?? []).map((x) => (
              <option key={x.id} value={x.id} disabled={!x.allowed}>{x.label}{x.allowed ? "" : " (not allowed in this project)"}</option>
            ))}
          </select>
        </label>
        <p className="mt-2 text-[12px] text-faint">The engine checks the default against each project's policy when a session is created; a session keeps "Ask before actions" if the policy refuses it.</p>
      </Card>
      <Card title="Providers">
        <div className="flex items-center gap-3">
          <p className="flex-1 text-[13px] text-muted">Check each provider CLI: installed, version, sign-in method and whether it is metered. Nothing is sent to a model.</p>
          <Button disabled={!root || doctor.loading} onClick={() => void runDoctor()}><RefreshCw className={cn("size-3.5", doctor.loading && "animate-spin")} />Check providers</Button>
        </div>
        {doctor.error && <p className="mt-2 text-[13px] text-err">{doctor.error}</p>}
        {doctor.data && (
          <ul className="mt-3 space-y-3">
            {doctor.data.adapters.map((a) => (
              <li key={a.name} className="rounded-lg border border-line px-3 py-2">
                <div className="flex items-center gap-2 text-[13px]">
                  {a.available ? <CheckCircle2 className="size-4 text-ok" /> : <XCircle className="size-4 text-err" />}
                  <Mono>{a.name}</Mono><span className="text-faint">{a.provider}</span>
                  {a.test_only && <span className="rounded bg-raised px-1.5 text-[11px] text-faint">test only</span>}
                  <span className="ml-auto text-[12px] text-muted">{a.version ?? ""}{a.auth_method ? ` · ${a.auth_method}` : ""}{a.metered === true ? " · metered (blocked)" : ""}</span>
                </div>
                {a.reason && <p className="mt-1 text-[12px] text-warn">{a.reason}</p>}
                {a.notices.map((n) => <p key={n} className="mt-1 text-[12px] text-muted">{n}</p>)}
              </li>
            ))}
            <li className="text-[12px] text-muted">Lead: {doctor.data.lead ? <Mono>{doctor.data.lead}</Mono> : <span className="text-err">unavailable: {doctor.data.lead_problems.join("; ")}</span>}</li>
          </ul>
        )}
      </Card>
      <Card title="Engine">
        <dl className="space-y-1 text-[12px]">
          <div><dt className="inline text-faint">Saarathi: </dt><dd className="inline">{bridge ? `${bridge.saarathi_version} (protocol ${bridge.protocol}, Python ${bridge.python})` : "not connected"}</dd></div>
          <div><dt className="inline text-faint">Data directory: </dt><dd className="inline"><Mono>{bridge?.data_dir ?? "—"}</Mono></dd></div>
          <div><dt className="inline text-faint">Bridge command: </dt><dd className="inline"><Mono>{diag?.command ?? "—"}</Mono></dd></div>
          <div className="text-faint">Set <Mono>SAARATHI_BRIDGE</Mono> to use a specific <Mono>saarathi</Mono>; otherwise the app finds it on your login shell's PATH.</div>
        </dl>
      </Card>
    </PageShell>
  );
}
